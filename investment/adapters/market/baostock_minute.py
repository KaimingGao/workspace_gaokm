"""BaoStock A 股分钟 K 线（备用源）。

官方 5/15/30/60 分钟约 2020-01-03 至今；本仓默认只回看 **30 日历日**。前复权 adjustflag=2。
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.numbers import to_float as _to_float
from adapters.market.minute_history import normalize_minute_bars

logger = logging.getLogger(__name__)

_BS_LOCK = threading.Lock()
_BS_LOGGED_IN = False

BAOSTOCK_MINUTE_START = "2020-01-03"


def baostock_enabled() -> bool:
    raw = os.environ.get("INVESTMENT_MINUTE_BS_FALLBACK", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def to_baostock_symbol(bare: str) -> Optional[str]:
    """6 位 A 股 → sh.600000 / sz.000001。"""
    code = str(bare or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "3")):
        return f"sz.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


def baostock_adjustflag(adjust: str) -> str:
    a = str(adjust or "qfq").strip().lower()
    if a in {"qfq", "forward", "2", "前复权"}:
        return "2"
    if a in {"hfq", "backward", "1", "后复权"}:
        return "1"
    return "3"


def _ensure_login() -> None:
    global _BS_LOGGED_IN
    with _BS_LOCK:
        if _BS_LOGGED_IN:
            return
        import baostock as bs

        lg = bs.login()
        if str(lg.error_code) != "0":
            raise RuntimeError(str(lg.error_msg or lg.error_code or "baostock login failed"))
        _BS_LOGGED_IN = True


def _parse_baostock_datetime(date_s: str, time_s: str) -> Optional[str]:
    d = str(date_s or "").strip().replace("/", "-")[:10]
    if len(d) != 10:
        return None
    t = str(time_s or "").strip()
    if not t:
        return f"{d} 00:00:00"
    # YYYYMMDDHHMMSSsss
    if len(t) >= 14 and t[:4].isdigit() and int(t[:4]) > 1900:
        hh, mm, ss = t[8:10], t[10:12], t[12:14]
        return f"{d} {hh}:{mm}:{ss}"
    # HHMMSS or HHMMSSsss
    if len(t) >= 6 and t[:6].isdigit():
        hh, mm, ss = t[0:2], t[2:4], t[4:6]
        return f"{d} {hh}:{mm}:{ss}"
    return f"{d} 00:00:00"


def _rows_to_bars(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        dt = _parse_baostock_datetime(str(row.get("date") or ""), str(row.get("time") or ""))
        if not dt:
            continue
        close = _to_float(row.get("close"))
        if close is None:
            continue
        out.append(
            {
                "datetime": dt,
                "date": dt[:10],
                "open": _to_float(row.get("open")) or close,
                "high": _to_float(row.get("high")) or close,
                "low": _to_float(row.get("low")) or close,
                "close": close,
                "volume": _to_float(row.get("volume")) or 0.0,
                "amount": _to_float(row.get("amount")),
            }
        )
    return normalize_minute_bars(out)


def _fetch_baostock_minute_bars_impl(
    bare: str,
    *,
    period: str = "5",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
) -> Tuple[List[dict], Dict[str, Any]]:
    """进程内拉取（无超时）；供子进程 worker 与 ``timeout_sec=0`` 直调。"""
    sym = to_baostock_symbol(bare)
    period_s = str(period or "5").strip()
    if period_s not in {"5", "15", "30", "60"}:
        return [], {
            "data_source": "empty",
            "error": f"baostock unsupported period={period_s}",
            "period": period_s,
        }
    if not sym:
        return [], {"data_source": "empty", "error": f"invalid bare code: {bare}"}

    end = datetime.now()
    end_s = (end_date or end.strftime("%Y-%m-%d"))[:10]
    start_s = (start_date or BAOSTOCK_MINUTE_START)[:10]
    if start_s < BAOSTOCK_MINUTE_START:
        start_s = BAOSTOCK_MINUTE_START
    adj_flag = baostock_adjustflag(adjust)

    try:
        _ensure_login()
        import baostock as bs

        rs = bs.query_history_k_data_plus(
            sym,
            "date,time,code,open,high,low,close,volume,amount,adjustflag",
            start_date=start_s,
            end_date=end_s,
            frequency=period_s,
            adjustflag=adj_flag,
        )
        if str(rs.error_code) != "0":
            return [], {
                "data_source": "baostock:query_history_k_data_plus",
                "error": str(rs.error_msg or rs.error_code),
                "period": period_s,
                "symbol": sym,
            }
        rows: List[dict] = []
        fields = list(rs.fields or [])
        while rs.next():
            row = rs.get_row_data()
            if row and fields:
                rows.append({fields[i]: row[i] for i in range(min(len(fields), len(row)))})
        bars = _rows_to_bars(rows)
        src = f"baostock:query_history_k_data_plus:{period_s}"
        meta: Dict[str, Any] = {
            "market": "CN",
            "code": bare,
            "stock_code": bare,
            "period": period_s,
            "data_source": src,
            "from_cache": False,
            "ok": bool(bars),
            "bar_count": len(bars),
            "adjust_policy": adjust,
            "symbol": sym,
            "start_date": start_s,
            "end_date": end_s,
        }
        if bars:
            meta["date_min"] = bars[0].get("date")
            meta["date_max"] = bars[-1].get("date")
        return bars, meta
    except Exception as e:
        logger.warning("fetch_baostock_minute_bars failed %s period=%s: %s", bare, period_s, e)
        return [], {
            "data_source": "baostock:query_history_k_data_plus",
            "error": str(e),
            "period": period_s,
            "symbol": sym,
        }


def _child_fetch_worker(payload: Dict[str, Any], out_queue: Any) -> None:
    try:
        bars, meta = _fetch_baostock_minute_bars_impl(**payload)
        out_queue.put(("ok", bars, meta))
    except Exception as e:  # noqa: BLE001 — 子进程边界须回传错误
        logger.warning("baostock child worker failed: %s", e)
        out_queue.put(
            (
                "err",
                [],
                {
                    "data_source": "baostock:query_history_k_data_plus",
                    "error": str(e),
                    "period": payload.get("period"),
                    "symbol": payload.get("bare"),
                },
            )
        )


def _fetch_baostock_minute_bars_subprocess(
    payload: Dict[str, Any],
    *,
    timeout_sec: float,
) -> Tuple[List[dict], Dict[str, Any]]:
    bare = str(payload.get("bare") or "")
    period_s = str(payload.get("period") or "5")
    sym = to_baostock_symbol(bare)
    timeout = max(1.0, float(timeout_sec))
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue()
    proc = ctx.Process(
        target=_child_fetch_worker,
        args=(payload, out_queue),
        name=f"baostock-minute-{bare}",
        daemon=True,
    )
    proc.start()
    proc.join(timeout=timeout)
    if proc.is_alive():
        proc.kill()
        proc.join(5.0)
        logger.warning(
            "fetch_baostock_minute_bars timeout %s period=%s after %.1fs",
            bare,
            period_s,
            timeout,
        )
        return [], {
            "data_source": "baostock:query_history_k_data_plus",
            "error": f"baostock timeout ({timeout}s)",
            "period": period_s,
            "symbol": sym,
            "timeout_sec": timeout,
        }
    if out_queue.empty():
        return [], {
            "data_source": "baostock:query_history_k_data_plus",
            "error": "baostock child exited without result",
            "period": period_s,
            "symbol": sym,
        }
    status, bars, meta = out_queue.get()
    if status != "ok":
        return list(bars or []), dict(meta or {})
    return bars, meta


def fetch_baostock_minute_bars(
    bare: str,
    *,
    period: str = "5",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
    timeout_sec: Optional[float] = None,
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 BaoStock 分钟线；失败返回空列表 + meta.error。

    默认在子进程执行并在超时后 kill，避免 Web 强更时单票挂死整批。
    ``timeout_sec=0`` 或策略为 0 时退回进程内直调（单测/调试）。
    """
    from core.data.policy import minute_baostock_timeout_sec

    period_s = str(period or "5").strip()
    payload = {
        "bare": bare,
        "period": period_s,
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust,
    }
    if timeout_sec is None:
        timeout = minute_baostock_timeout_sec()
    else:
        timeout = float(timeout_sec)
    if timeout <= 0:
        return _fetch_baostock_minute_bars_impl(**payload)
    return _fetch_baostock_minute_bars_subprocess(payload, timeout_sec=timeout)


def suggest_baostock_start(lookback_days: int) -> str:
    """按日历日回看算起点（默认 30 日，上限见策略；再封顶 2020-01-03）。"""
    from core.data.policy import minute_baostock_lookback_days

    cap = minute_baostock_lookback_days()
    cal = min(max(5, int(lookback_days or cap)), cap)
    start = datetime.now() - timedelta(days=min(cal, 730))
    start_s = start.strftime("%Y-%m-%d")
    return start_s if start_s >= BAOSTOCK_MINUTE_START else BAOSTOCK_MINUTE_START
