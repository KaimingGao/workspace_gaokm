"""A 股 5 分钟线拉取（新浪 ``getKLineData``，不复权）。

不走 ``ak.stock_zh_a_minute``：那个函数在 ``adjust=""`` 时仍会先拉整段前复权日线，
一只就要几十秒。这里只打分钟 JSON，datalen=1970（约 41 个交易日）。
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.data.policy import (
    MINUTE_CACHE_HOURS,
    MINUTE_ISOLATED_TIMEOUT_SEC,
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
MINUTE_DATA_SOURCE = "sina:getKLineData"
_SINA_MINUTE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
)
_SINA_MINUTE_URL_FALLBACK = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
    "var%20_{symbol}_{period}_1658852984203=/CN_MarketDataService.getKLineData"
)
_SINA_MINUTE_TIMEOUT_SEC = 15.0
_SINA_MINUTE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}


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


def _sina_symbol(bare: str) -> Optional[str]:
    """6 位 A 股 → 新浪分钟代码（sh/sz/bj）。"""
    code = str(bare or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _sina_minute_records(text: str) -> List[dict]:
    """解开新浪 JSONP。失败抛错，交给调用方换备用地址。"""
    raw = str(text or "")
    parts = raw.split("=(", 1)
    if len(parts) != 2:
        raise ValueError("sina minute payload missing")
    body = parts[1].split(");", 1)[0]
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError("sina minute payload not a list")
    return [row for row in data if isinstance(row, dict)]


def _fetch_sina_minute_bars(bare: str) -> Tuple[List[dict], Dict[str, Any]]:
    """新浪 5 分钟 JSON。不拉日线。窗口 datalen=1970。"""
    symbol = _sina_symbol(bare)
    if not symbol:
        return [], {
            "data_source": "empty",
            "error": f"invalid bare code: {bare}",
        }
    params = {
        "symbol": symbol,
        "scale": MINUTE_PERIOD,
        "ma": "no",
        "datalen": "1970",
    }
    urls = (
        _SINA_MINUTE_URL,
        _SINA_MINUTE_URL_FALLBACK.format(symbol=symbol, period=MINUTE_PERIOD),
    )
    records: List[dict] = []
    last_err = ""
    for url in urls:
        try:
            resp = requests.get(
                url,
                params=params,
                headers=_SINA_MINUTE_HEADERS,
                timeout=_SINA_MINUTE_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            records = _sina_minute_records(resp.text)
            last_err = ""
            break
        except Exception as exc:  # noqa: BLE001 — 主地址失败再试备用
            last_err = str(exc)[:200]
            records = []
    if last_err and not records:
        logger.info("sina minute failed %s period=%s: %s", bare, MINUTE_PERIOD, last_err)
        return [], {
            "data_source": "empty",
            "error": last_err,
            "symbol": symbol,
            "period": MINUTE_PERIOD,
        }
    for row in records:
        if isinstance(row, dict) and row.get("day") and not row.get("datetime"):
            row["datetime"] = row.get("day")
    bars = normalize_minute_bars(records)
    meta: Dict[str, Any] = {
        "data_source": MINUTE_DATA_SOURCE,
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
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    throttle: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 A 股 5 分钟线（新浪）；失败返回空列表。

    ``period`` 固定为 5。新浪窗口固定（约 41 个交易日），不按回看天数切片。
    use_cache: True 时先读本地有效仓；False 跳过读仓直接打远端。
      远端成功后**始终** merge+save（与 use_cache 无关），避免 force refresh 不落盘。
    远端失败时回退本地过期缓存，避免做T回测整批挂死。
    """
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
    logger.debug(
        "fetch_a_minute_bars success %s period=%s use_cache=%s max_age_hours=%s bars_count=%s",
        code, period, use_cache, max_age_hours, len(bars),
    )

    try:
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
                "hint": "新浪 5 分钟失败；可稍后重试",
                "sina_error": as_meta.get("error"),
            }

        bars, meta = _merge_save_minute_bars(
            market,
            bare,
            bars,
            period=period,
            data_source=str(as_meta.get("data_source") or MINUTE_DATA_SOURCE),
            adjust_policy=None,
        )
        meta["sina"] = as_meta
        return bars, meta
    finally:
        # 先落盘再睡，避免 45s 超时把已拉到的根杀掉
        if throttle:
            _throttle_minute_remote_fetch()


def fetch_minute_bars(
    code: str,
    *,
    period: str = MINUTE_PERIOD,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    throttle: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """对外入口：目前仅 A 股 5 分钟。"""
    return fetch_a_minute_bars(
        code,
        period=period,
        use_cache=use_cache,
        max_age_hours=max_age_hours,
        throttle=throttle,
    )


def _minute_isolated_ack(
    bars: Optional[List[dict]], meta: Optional[Dict[str, Any]]
) -> Tuple[str, int, Dict[str, Any]]:
    """队列只带回根数和状态。K 线已落盘，父进程不再收整段。"""
    meta_out = dict(meta or {})
    meta_out.pop("sina", None)
    n = int(meta_out.get("bar_count") or len(bars or []))
    meta_out["bar_count"] = n
    if n > 0 and not meta_out.get("error"):
        meta_out["ok"] = True
    return "ok", n, meta_out


def _child_fetch_a_minute_worker(payload: Dict[str, Any], out_queue: Any) -> None:
    """子进程入口：拉分钟线并落盘；队列只回根数和状态。"""
    try:
        raw = dict(payload or {})
        code = str(raw.pop("code") or "")
        kw = {
            k: raw[k]
            for k in ("period", "use_cache", "max_age_hours", "throttle")
            if k in raw
        }
        bars, meta = fetch_a_minute_bars(code, **kw)
        out_queue.put(_minute_isolated_ack(bars, meta))
    except Exception as e:  # noqa: BLE001 — 子进程兜底
        out_queue.put(
            (
                "err",
                0,
                {
                    "data_source": "empty",
                    "error": str(e)[:200],
                    "period": MINUTE_PERIOD,
                    "bar_count": 0,
                },
            )
        )


def _recv_child_queue(proc: Any, out_queue: Any, timeout: float) -> Tuple[Any, bool]:
    """在子进程退出前把队列读完。返回 ``(item, timed_out)``。

    先 ``join`` 再 ``get`` 时，结果大于管道缓冲会互等：子进程写不完就退不出。
    """
    from queue import Empty

    deadline = time.monotonic() + max(1.0, float(timeout))
    item = None
    while item is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            item = out_queue.get(timeout=min(0.5, remaining))
        except Empty:
            if not proc.is_alive():
                try:
                    item = out_queue.get(timeout=0.2)
                except Empty:
                    item = None
                break
    timed_out = item is None and proc.is_alive()
    if timed_out:
        proc.kill()
    proc.join(5.0)
    if proc.is_alive():
        proc.kill()
        proc.join(2.0)
    return item, timed_out


def fetch_a_minute_bars_isolated(
    code: str,
    *,
    timeout_sec: float = MINUTE_ISOLATED_TIMEOUT_SEC,
    period: str = MINUTE_PERIOD,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    throttle: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """子进程拉 5 分钟线；超时 ``kill`` 子进程。只打 ``getKLineData``，不进 ``ak_lock``。

    供观察池强更 / schedule 预热：单票挂死时父进程可继续下一只。
    队列只回根数。有数据时返回一行占位，调用方只看是否非空。
    ``timeout_sec<=0`` 时退回进程内直调（单测），此时仍返回完整 K 线。
    """
    import multiprocessing as mp

    timeout = float(timeout_sec or 0)
    code_s = str(code or "").strip()
    if timeout <= 0:
        return fetch_a_minute_bars(
            code_s,
            period=period,
            use_cache=use_cache,
            max_age_hours=max_age_hours,
            throttle=throttle,
        )

    payload = {
        "code": code_s,
        "period": MINUTE_PERIOD,
        "use_cache": bool(use_cache),
        "max_age_hours": float(max_age_hours),
        "throttle": bool(throttle),
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
    item, timed_out = _recv_child_queue(proc, out_queue, timeout)
    if timed_out:
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
    if not isinstance(item, tuple) or len(item) != 3:
        return [], {
            "data_source": "empty",
            "error": "minute child exited without result",
            "period": MINUTE_PERIOD,
        }
    status, count, meta = item
    meta_out = dict(meta or {})
    if status != "ok":
        return [], meta_out
    n = int(count or 0)
    meta_out["bar_count"] = n
    if n > 0:
        return [{"bar_count": n}], meta_out
    return [], meta_out
