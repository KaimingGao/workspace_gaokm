"""A 股分钟线拉取（东财主 · ak.stock_zh_a_minute 近端 · BaoStock 30 日历日）。

限量（东财常见）：1 分钟约近 5 日；5/15/30/60 分钟默认近 **120 日历日**。
近端备：东财空或 skip_em 时打 ``ak.stock_zh_a_minute``（新浪，datalen=1970，5m ≈ 41 日）；有数则不再打 BaoStock。
``sina_minute`` 保留为备选客户端，更新路径不调用。
BaoStock 备：5/15/30/60 约近 **30 日历日**（与东财同窗）；仅东财与新浪分钟都空、或东财短于该窗口时再拉。
做 T 第一触达默认 period=5。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.data.policy import (
    MINUTE_CACHE_HOURS,
    MINUTE_ISOLATED_TIMEOUT_SEC,
    minute_fetch_delay_sec,
)
from core.numbers import to_float as _to_float
from core.store import (
    align_minute_volume_units,
    load_minute_cache,
    merge_minute_bars_by_time,
    save_minute_cache,
)
from adapters.market.history import resolve_market_code

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

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


def _calendar_span_days(bars: List[dict]) -> int:
    if not bars:
        return 0
    try:
        d0 = datetime.strptime(str(bars[0].get("date") or "")[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(bars[-1].get("date") or "")[:10], "%Y-%m-%d")
        return max(0, (d1 - d0).days)
    except (ValueError, TypeError):
        return 0


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


def _fetch_em_minute_bars(
    bare: str,
    *,
    period: str,
    lookback_days: int,
    adjust: str,
    span_cap: Optional[int] = None,
) -> Tuple[List[dict], Dict[str, Any], Optional[str]]:
    """东财分钟线；返回 (bars, meta, error)。

    ``span_cap`` 缺省用日常回看（默认 120 日历日）。补缺同样受硬上限 120。
    """
    from core.data.policy import (
        MINUTE_EM_LOOKBACK_MAX_DAYS,
        MINUTE_FETCH_DELAY_MAX_SEC,
        minute_em_lookback_days,
        minute_fetch_delay_sec,
    )
    from core.http_retry import call_with_retry

    end = datetime.now()
    hard = int(MINUTE_EM_LOOKBACK_MAX_DAYS)
    cap = minute_em_lookback_days() if span_cap is None else min(int(span_cap), hard)
    if period == "1":
        span = min(int(lookback_days or 5), 8)
    else:
        span = min(max(int(lookback_days or cap), 5), cap)
    start = end - timedelta(days=max(span, 10))
    start_s = start.strftime("%Y-%m-%d 09:30:00")
    end_s = end.strftime("%Y-%m-%d 15:00:00")
    adj = "" if period == "1" else (adjust or "qfq")
    try:
        # 失败后再试必须拉开，不能在 1 秒内连打。间隔与票间频控同一档。
        gap = minute_fetch_delay_sec()
        retry_kw = dict(
            retries=1,
            base_delay_sec=gap if gap > 0 else float(MINUTE_FETCH_DELAY_MAX_SEC),
            max_delay_sec=float(MINUTE_FETCH_DELAY_MAX_SEC),
        )
        from core.data.ak_lock import import_akshare

        ak = import_akshare()

        def _once():
            return ak.stock_zh_a_hist_min_em(
                symbol=bare,
                start_date=start_s,
                end_date=end_s,
                period=period,
                adjust=adj,
            )

        df = call_with_retry(_once, **retry_kw)
        records = (
            df.to_dict(orient="records")
            if df is not None and hasattr(df, "to_dict")
            else []
        )
    except Exception as e:
        logger.warning(
            " DEBUG: fetch_a_minute_bars em failed %s start=%s end=%s period=%s adjust=%s: %s",
            bare, start_s, end_s, period, adjust, e,
        )
        return [], {}, str(e)

    bars = normalize_minute_bars(records)
    src = f"akshare:stock_zh_a_hist_min_em:{period}"
    meta: Dict[str, Any] = {
        "data_source": src,
        "bar_count": len(bars),
        "adjust_policy": adj or None,
        "em_start": start_s,
        "em_end": end_s,
    }
    date_min = bars[0].get("date") if bars else None
    date_max = bars[-1].get("date") if bars else None
    if bars:
        meta["date_min"] = date_min
        meta["date_max"] = date_max

    logger.info(
        " DEBUG: fetch_a_minute_bars em success %s start=%s end=%s period=%s adjust=%s bar_count=%s date_min=%s date_max=%s",
        bare, start_s, end_s, period, adjust, len(bars), date_min, date_max,
    )

    return bars, meta, None


def _maybe_fetch_baostock_minute_bars(
    bare: str,
    *,
    period: str,
    lookback_days: int,
    adjust: str,
    have_bars: List[dict],
    prior_error: Optional[str] = None,
) -> Tuple[List[dict], Dict[str, Any]]:
    from core.data.policy import minute_baostock_lookback_days
    from adapters.market.baostock_minute import (
        baostock_enabled,
        fetch_baostock_minute_bars,
        suggest_baostock_start,
    )

    if not baostock_enabled():
        return [], {}
    have = list(have_bars or [])
    need = not have
    bs_lookback = min(max(5, int(lookback_days or 30)), minute_baostock_lookback_days())
    if have:
        cal_span = _calendar_span_days(have)
        if cal_span < bs_lookback:
            need = True
    if not need:
        return [], {}
    start_s = suggest_baostock_start(bs_lookback)
    bs_bars, bs_meta = fetch_baostock_minute_bars(
        bare,
        period=period,
        start_date=start_s,
        adjust=adjust,
    )
    if bs_meta:
        if have:
            bs_meta["backfill_reason"] = "short"
        elif prior_error:
            bs_meta["backfill_reason"] = "prior_fail"
        else:
            bs_meta["backfill_reason"] = "empty"
    return bs_bars, bs_meta


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


def _maybe_fetch_sina_minute_bars(
    bare: str,
    *,
    period: str,
    lookback_days: int,
    reason: str = "em_empty",
) -> Tuple[List[dict], Dict[str, Any]]:
    """东财空时的近端。走 ``ak.stock_zh_a_minute``（不复权，datalen=1970）。

    ``lookback_days`` 不传给该接口（窗口由 akshare 固定）。``sina_minute`` 不在此调用。
    """
    del lookback_days  # 接口不接受回看天数
    from core.data.ak_lock import import_akshare

    symbol = _ak_sina_symbol(bare)
    period_s = str(period or "5")
    if not symbol:
        return [], {"data_source": "empty", "error": f"invalid bare code: {bare}", "backfill_reason": reason}
    try:
        ak = import_akshare()
        df = ak.stock_zh_a_minute(symbol=symbol, period=period_s, adjust="")
    except Exception as e:  # noqa: BLE001 — 失败则交给 BaoStock
        logger.info("stock_zh_a_minute failed %s period=%s: %s", bare, period_s, e)
        return [], {
            "data_source": "empty",
            "error": str(e),
            "symbol": symbol,
            "period": period_s,
            "backfill_reason": reason,
        }
    records = df.to_dict(orient="records") if df is not None and hasattr(df, "to_dict") else []
    for row in records:
        if isinstance(row, dict) and row.get("day") and not row.get("datetime"):
            row["datetime"] = row.get("day")
    bars = normalize_minute_bars(records)
    meta: Dict[str, Any] = {
        "data_source": "akshare:stock_zh_a_minute",
        "symbol": symbol,
        "period": period_s,
        "adjust": "",
        "ok": bool(bars),
        "bar_count": len(bars),
        "backfill_reason": reason,
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
    return bars, meta


def _throttle_minute_remote_fetch() -> None:
    """东财 / 新浪 / BaoStock 分钟远端拉取后的频控间隔。"""
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
    period: str = "5",
    lookback_days: int = 120,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
    skip_em: bool = False,
    skip_bs: bool = False,
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 A 股分钟线；失败返回空列表。

    period: "1"|"5"|"15"|"30"|"60"
    use_cache: True 时先读本地有效仓；False 跳过读仓直接打远端。
      远端成功后**始终** merge+save（与 use_cache 无关），避免 force refresh 不落盘。
    skip_em: True 时跳过东财（T0 回测 / 显式 skip）；``stock_zh_a_minute`` 有数则不再打 BaoStock。
    skip_bs: True 时跳过 BaoStock（增量补齐近端，避免 90s 子进程把 Job 挂死）。
    远端失败时回退本地过期缓存（与日线 fetch 一致），避免做T回测整批挂死。
    """
    market, bare = resolve_market_code(code)
    if market != "CN" or not bare:
        raw = str(code).strip()
        if raw.isdigit() and len(raw) == 6:
            market, bare = "CN", raw
        else:
            return [], {"data_source": "empty", "error": f"仅支持 A 股分钟线: {code}"}

    period = str(period or "5").strip()
    if period not in {"1", "5", "15", "30", "60"}:
        period = "5"

    if use_cache:
        cached = load_minute_cache(
            market, bare, period, min_bars=10, max_age_hours=max_age_hours
        )
        if cached:
            bars, meta = cached
            meta = dict(meta)
            meta["ok"] = True
            return bars, meta

    adj = "" if period == "1" else (adjust or "qfq")
    em_bars: List[dict] = []
    em_meta: Dict[str, Any] = {}
    em_error: Optional[str] = None
    as_bars: List[dict] = []
    as_meta: Dict[str, Any] = {}
    bs_bars: List[dict] = []
    bs_meta: Dict[str, Any] = {}

    if skip_em:
        em_error = "skip_em"
    else:
        em_bars, em_meta, em_error = _fetch_em_minute_bars(
            bare, period=period, lookback_days=lookback_days, adjust=adjust or "qfq"
        )
        _throttle_minute_remote_fetch()

    bars = list(em_bars or [])
    if not bars:
        as_bars, as_meta = _maybe_fetch_sina_minute_bars(
            bare,
            period=period,
            lookback_days=lookback_days,
            reason="skip_em" if skip_em else "em_empty",
        )
        if as_meta:
            _throttle_minute_remote_fetch()
        if as_bars:
            # 重叠日若东财为「手」、新浪为「股」，先对齐再整日替换合并
            if bars:
                bars = align_minute_volume_units(bars, as_bars)
            bars = merge_minute_bars_by_time(bars, as_bars, period=period)

    if as_bars or skip_bs:
        # stock_zh_a_minute 已接住近端，或调用方禁止 BaoStock（增量补齐）
        bs_bars, bs_meta = [], {
            "skipped": True,
            "reason": "sina_ok" if as_bars else "skip_bs",
        }
    else:
        bs_bars, bs_meta = _maybe_fetch_baostock_minute_bars(
            bare,
            period=period,
            lookback_days=lookback_days,
            adjust=adjust or "qfq",
            have_bars=bars,
            prior_error=em_error,
        )
        if bs_meta:
            _throttle_minute_remote_fetch()
        if bs_bars:
            if bars:
                bars = align_minute_volume_units(bars, bs_bars)
            # 同日整段以 incoming（BaoStock）为准，禁止跨源缝合
            bars = merge_minute_bars_by_time(bars, bs_bars, period=period)

    logger.info(" DEBUG: fetch_a_minute_bars success %s period=%s lookback_days=%s use_cache=%s max_age_hours=%s adjust=%s skip_em=%s skip_bs=%s bars_count=%s",
        code, period, lookback_days, use_cache, max_age_hours, adjust, skip_em, skip_bs, len(bars),
    )

    sources: List[str] = []
    if em_bars:
        sources.append(str(em_meta.get("data_source") or "em"))
    if as_bars:
        sources.append(str(as_meta.get("data_source") or "akshare:stock_zh_a_minute"))
    if bs_bars:
        sources.append(str(bs_meta.get("data_source") or "baostock"))
    src = "+".join(sources) if sources else "empty"

    if not bars:
        if use_cache:
            stale = _load_stale_minute(market, bare, period)
            if stale:
                bars, meta = stale
                meta["remote_error"] = em_error or as_meta.get("error") or bs_meta.get("error")
                meta["period"] = period
                return bars, meta
        hint = "东财/stock_zh_a_minute/BaoStock 分钟源均失败；可稍后重试"
        return [], {
            "data_source": "empty",
            "error": em_error or as_meta.get("error") or bs_meta.get("error") or "no_minute_bars",
            "period": period,
            "hint": hint,
            "em_error": em_error,
            "bs_error": bs_meta.get("error"),
            "sina_error": as_meta.get("error"),
        }

    bars, meta = _merge_save_minute_bars(
        market,
        bare,
        bars,
        period=period,
        data_source=src,
        adjust_policy=adj or None,
    )
    meta["em"] = em_meta if not skip_em else {"skipped": True, "reason": "skip_em"}
    if bs_meta:
        meta["baostock"] = bs_meta
    if as_meta:
        meta["sina"] = as_meta
    if em_error and not skip_em:
        meta["em_error"] = em_error
    if skip_em:
        meta["skip_em"] = True
    return bars, meta


def fetch_minute_bars(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 120,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
    skip_em: bool = False,
    skip_bs: bool = False,
) -> Tuple[List[dict], Dict[str, Any]]:
    """对外入口：目前仅 A 股。"""
    return fetch_a_minute_bars(
        code,
        period=period,
        lookback_days=lookback_days,
        use_cache=use_cache,
        max_age_hours=max_age_hours,
        adjust=adjust,
        skip_em=skip_em,
        skip_bs=skip_bs,
    )


def _child_fetch_a_minute_worker(payload: Dict[str, Any], out_queue: Any) -> None:
    """子进程入口：拉分钟线并落盘；异常只回传 meta，不拖垮父进程。"""
    try:
        kw = dict(payload or {})
        code = str(kw.pop("code") or "")
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
                    "period": str((payload or {}).get("period") or "5"),
                },
            )
        )


def fetch_a_minute_bars_isolated(
    code: str,
    *,
    timeout_sec: float = MINUTE_ISOLATED_TIMEOUT_SEC,
    period: str = "5",
    lookback_days: int = 120,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
    skip_em: bool = False,
    skip_bs: bool = False,
) -> Tuple[List[dict], Dict[str, Any]]:
    """子进程拉分钟线；超时 ``kill`` 子进程，不占主进程 ``ak_lock``。

    供观察池强更 / schedule 预热：单票东财挂死时父进程可继续下一只。
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
            adjust=adjust,
            skip_em=skip_em,
            skip_bs=skip_bs,
        )

    payload = {
        "code": code_s,
        "period": str(period or "5"),
        "lookback_days": int(lookback_days or 120),
        "use_cache": bool(use_cache),
        "max_age_hours": float(max_age_hours),
        "adjust": str(adjust or "qfq"),
        "skip_em": bool(skip_em),
        "skip_bs": bool(skip_bs),
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
        # 超时后尽量回退本地过期仓，避免强更后整票变 Missing
        try:
            market, bare = resolve_market_code(code_s)
            if market == "CN" and bare:
                stale = _load_stale_minute(market, bare, str(period or "5"))
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
            "period": str(period or "5"),
            "timeout_sec": timeout,
        }
    if out_queue.empty():
        return [], {
            "data_source": "empty",
            "error": "minute child exited without result",
            "period": str(period or "5"),
        }
    status, bars, meta = out_queue.get()
    if status != "ok":
        return list(bars or []), dict(meta or {})
    return list(bars or []), dict(meta or {})
