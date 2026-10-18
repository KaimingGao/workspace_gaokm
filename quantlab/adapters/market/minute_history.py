"""A 股 5 分钟线拉取（只走新浪 ``ak.stock_zh_a_minute``）。

不复权，datalen=1970（约 41 个交易日）。做 T / ŷ_τc / 强更默认 period=5。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from core.data.policy import (
    MINUTE_CACHE_HOURS,
    MINUTE_ISOLATED_TIMEOUT_SEC,
    MINUTE_LOOKBACK_DAYS,
    minute_fetch_delay_sec,
)
from core.numbers import to_float as _to_float
from core.store import (
    load_minute_cache,
    merge_minute_bars_by_time,
    save_minute_cache,
)
from adapters.market.history import resolve_market_code

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

MINUTE_PERIOD = "5"


def normalize_minute_bars(rows: List[dict]) -> List[dict]:
    """统一为 datetime/date/open/high/low/close/volume。"""
    bars: List[dict] = []
    for row in rows or []:
        raw_ts = (
            row.get("datetime")
            or row.get("时间")
            or row.get("date")
            or row.get("日期")
            or row.get("time")
        )
        if raw_ts is None:
            continue
        ts = str(raw_ts).strip().replace("/", "-")
        if len(ts) == 10:
            ts = f"{ts} 00:00:00"
        close = _to_float(
            row.get("close") or row.get("收盘") or row.get("收盘价") or row.get("最新价")
        )
        if close is None:
            continue
        day = ts[:10]
        bars.append(
            {
                "datetime": ts,
                "date": day,
                "open": _to_float(row.get("open") or row.get("开盘") or row.get("开盘价"))
                or close,
                "high": _to_float(row.get("high") or row.get("最高") or row.get("最高价"))
                or close,
                "low": _to_float(row.get("low") or row.get("最低") or row.get("最低价"))
                or close,
                "close": close,
                "volume": _to_float(row.get("volume") or row.get("成交量")) or 0.0,
            }
        )
        amt = _to_float(row.get("amount") or row.get("成交额") or row.get("turnover"))
        if amt is not None:
            bars[-1]["amount"] = float(amt)
    bars.sort(key=lambda x: x["datetime"])
    return bars


def group_minute_bars_by_date(bars: List[dict]) -> Dict[str, List[dict]]:
    """按交易日分组，组内按时间升序。"""
    out: Dict[str, List[dict]] = defaultdict(list)
    for b in bars or []:
        d = str(b.get("date") or str(b.get("datetime") or "")[:10])
        if not d:
            continue
        out[d].append(b)
    for d in out:
        out[d].sort(key=lambda x: str(x.get("datetime") or ""))
    return dict(out)


def _merge_save_minute_bars(
    market: str,
    bare: str,
    bars: List[dict],
    *,
    period: str,
    data_source: str,
    adjust_policy: Optional[str],
) -> Tuple[List[dict], Dict[str, Any]]:
    """远端有数时与本地仓按时间合并并落盘（与入口 ``use_cache`` 读短路无关）。"""
    meta: Dict[str, Any] = {
        "market": market,
        "code": bare,
        "stock_code": bare,
        "period": period,
        "data_source": data_source,
        "from_cache": False,
        "ok": bool(bars),
        "bar_count": len(bars),
        "adjust_policy": adjust_policy,
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
        incoming = list(bars)
        old = load_minute_cache(
            market, bare, period, min_bars=1, max_age_hours=0, ignore_age=True
        )
        if old:
            bars = merge_minute_bars_by_time(
                old[0], incoming, period=period, lock_calendar_day=False
            )
        from core.store import bars_backend

        # sqlite 按时刻 upsert incoming，不删库里多出来的根；JSON 写合并后的全量
        to_write = incoming if bars_backend() == "sqlite" else bars
        save_minute_cache(
            market,
            bare,
            to_write,
            period=period,
            data_source=data_source,
            stock_code=bare,
            adjust_policy=adjust_policy,
        )
        meta["bar_count"] = len(bars)
        meta["date_min"] = bars[0].get("date") if bars else meta.get("date_min")
        meta["date_max"] = bars[-1].get("date") if bars else meta.get("date_max")
        meta["cached"] = True
    return bars, meta


def _ak_sina_symbol(bare: str) -> Optional[str]:
    """6 位 A 股 → ``stock_zh_a_minute`` 的 sh/sz/bj 代码。"""
    code = str(bare or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _fetch_sina_minute_bars(bare: str) -> Tuple[List[dict], Dict[str, Any]]:
    """新浪 5 分钟。窗口由 akshare datalen 固定。"""
    from core.data.ak_lock import import_akshare

    symbol = _ak_sina_symbol(bare)
    if not symbol:
        return [], {
            "data_source": "empty",
            "error": f"invalid bare code: {bare}",
        }
    try:
        ak = import_akshare()
        df = ak.stock_zh_a_minute(symbol=symbol, period=MINUTE_PERIOD, adjust="")
    except Exception as e:  # noqa: BLE001 — 远端失败则空返回
        logger.info("stock_zh_a_minute failed %s period=%s: %s", bare, MINUTE_PERIOD, e)
        return [], {
            "data_source": "empty",
            "error": str(e),
            "symbol": symbol,
            "period": MINUTE_PERIOD,
        }
    records = df.to_dict(orient="records") if df is not None and hasattr(df, "to_dict") else []
    for row in records:
        if isinstance(row, dict) and row.get("day") and not row.get("datetime"):
            row["datetime"] = row.get("day")
    bars = normalize_minute_bars(records)
    meta: Dict[str, Any] = {
        "data_source": "akshare:stock_zh_a_minute",
        "symbol": symbol,
        "period": MINUTE_PERIOD,
        "adjust": "",
        "ok": bool(bars),
        "bar_count": len(bars),
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
    return bars, meta


def _throttle_minute_remote_fetch() -> None:
    """新浪分钟远端拉取后的频控间隔。"""
    delay = minute_fetch_delay_sec()
    if delay > 0:
        time.sleep(delay)


def _load_stale_minute(
    market: str, bare: str, period: str, *, min_bars: int = 10
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    """忽略 TTL 读本地分钟缓存（网络失败 / 研究兜底）。"""
    packed = load_minute_cache(
        market, bare, period, min_bars=min_bars, max_age_hours=0, ignore_age=True
    )
    if not packed:
        return None
    bars, meta = packed
    meta = dict(meta)
    meta["ok"] = True
    meta["from_cache"] = True
    meta["cache_stale"] = True
    src = meta.get("data_source") or "cache"
    meta["data_source"] = f"cache:stale:{src}"
    return bars, meta


def fetch_a_minute_bars(
    code: str,
    *,
    period: str = MINUTE_PERIOD,
    lookback_days: int = MINUTE_LOOKBACK_DAYS,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 A 股 5 分钟线（新浪）；失败返回空列表。

    ``period`` 固定为 5。``lookback_days`` 不传给新浪接口（窗口固定）。
    use_cache: True 时先读本地有效仓；False 跳过读仓直接打远端。
      远端成功后**始终** merge+save（与 use_cache 无关），避免 force refresh 不落盘。
    远端失败时回退本地过期缓存，避免做T回测整批挂死。
    """
    del lookback_days  # 新浪固定窗口
    market, bare = resolve_market_code(code)
    if market != "CN" or not bare:
        raw = str(code).strip()
        if raw.isdigit() and len(raw) == 6:
            market, bare = "CN", raw
        else:
            return [], {"data_source": "empty", "error": f"仅支持 A 股分钟线: {code}"}

    period = MINUTE_PERIOD

    if use_cache:
        cached = load_minute_cache(
            market, bare, period, min_bars=10, max_age_hours=max_age_hours
        )
        if cached:
            bars, meta = cached
            meta = dict(meta)
            meta["ok"] = True
            return bars, meta

    bars, as_meta = _fetch_sina_minute_bars(bare)
    if as_meta:
        _throttle_minute_remote_fetch()

    logger.debug(
        "fetch_a_minute_bars success %s period=%s use_cache=%s max_age_hours=%s bars_count=%s",
        code, period, use_cache, max_age_hours, len(bars),
    )

    if not bars:
        if use_cache:
            stale = _load_stale_minute(market, bare, period)
            if stale:
                bars, meta = stale
                meta["remote_error"] = as_meta.get("error")
                meta["period"] = period
                return bars, meta
        return [], {
            "data_source": "empty",
            "error": as_meta.get("error") or "no_minute_bars",
            "period": period,
            "hint": "stock_zh_a_minute 失败；可稍后重试",
            "sina_error": as_meta.get("error"),
        }

    bars, meta = _merge_save_minute_bars(
        market,
        bare,
        bars,
        period=period,
        data_source=str(as_meta.get("data_source") or "akshare:stock_zh_a_minute"),
        adjust_policy=None,
    )
    meta["sina"] = as_meta
    return bars, meta


def fetch_minute_bars(
    code: str,
    *,
    period: str = MINUTE_PERIOD,
    lookback_days: int = MINUTE_LOOKBACK_DAYS,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """对外入口：目前仅 A 股 5 分钟。"""
    return fetch_a_minute_bars(
        code,
        period=period,
        lookback_days=lookback_days,
        use_cache=use_cache,
        max_age_hours=max_age_hours,
    )


def _child_fetch_a_minute_worker(payload: Dict[str, Any], out_queue: Any) -> None:
    """子进程入口：拉分钟线并落盘；异常只回传 meta，不拖垮父进程。"""
    try:
        raw = dict(payload or {})
        code = str(raw.pop("code") or "")
        kw = {
            k: raw[k]
            for k in ("period", "lookback_days", "use_cache", "max_age_hours")
            if k in raw
        }
        bars, meta = fetch_a_minute_bars(code, **kw)
        out_queue.put(("ok", list(bars or []), dict(meta or {})))
    except Exception as e:  # noqa: BLE001 — 子进程兜底
        out_queue.put(
            (
                "err",
                [],
                {
                    "data_source": "empty",
                    "error": str(e)[:200],
                    "period": MINUTE_PERIOD,
                },
            )
        )


def fetch_a_minute_bars_isolated(
    code: str,
    *,
    timeout_sec: float = MINUTE_ISOLATED_TIMEOUT_SEC,
    period: str = MINUTE_PERIOD,
    lookback_days: int = MINUTE_LOOKBACK_DAYS,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """子进程拉 5 分钟线；超时 ``kill`` 子进程，不占主进程 ``ak_lock``。

    供观察池强更 / schedule 预热：单票挂死时父进程可继续下一只。
    ``timeout_sec<=0`` 时退回进程内直调（单测）。
    """
    import multiprocessing as mp

    timeout = float(timeout_sec or 0)
    code_s = str(code or "").strip()
    if timeout <= 0:
        return fetch_a_minute_bars(
            code_s,
            period=period,
            lookback_days=lookback_days,
            use_cache=use_cache,
            max_age_hours=max_age_hours,
        )

    payload = {
        "code": code_s,
        "period": MINUTE_PERIOD,
        "lookback_days": int(lookback_days or MINUTE_LOOKBACK_DAYS),
        "use_cache": bool(use_cache),
        "max_age_hours": float(max_age_hours),
    }
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue()
    proc = ctx.Process(
        target=_child_fetch_a_minute_worker,
        args=(payload, out_queue),
        name=f"minute-warmup-{code_s}",
        daemon=True,
    )
    proc.start()
    proc.join(timeout=max(1.0, timeout))
    if proc.is_alive():
        proc.kill()
        proc.join(5.0)
        logger.warning(
            "fetch_a_minute_bars_isolated timeout %s after %.0fs",
            code_s,
            timeout,
        )
        try:
            market, bare = resolve_market_code(code_s)
            if market == "CN" and bare:
                stale = _load_stale_minute(market, bare, MINUTE_PERIOD)
                if stale and stale[0]:
                    bars, meta = stale
                    meta = dict(meta)
                    meta["error"] = f"timeout ({timeout:.0f}s)"
                    meta["timeout_sec"] = timeout
                    return bars, meta
        except Exception:  # noqa: BLE001
            logger.debug("stale fallback after minute timeout failed", exc_info=True)
        return [], {
            "data_source": "empty",
            "error": f"timeout ({timeout:.0f}s)",
            "period": MINUTE_PERIOD,
            "timeout_sec": timeout,
        }
    if out_queue.empty():
        return [], {
            "data_source": "empty",
            "error": "minute child exited without result",
            "period": MINUTE_PERIOD,
        }
    status, bars, meta = out_queue.get()
    if status != "ok":
        return list(bars or []), dict(meta or {})
    return list(bars or []), dict(meta or {})
