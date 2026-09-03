"""A 股分钟线拉取（东财主 · 新浪/腾讯近端 · BaoStock 30 日历日）。

限量（东财常见）：1 分钟约近 5 日；5/15/30/60 分钟默认近 **30 日历日**（实测东财也常只有约一个月）。
新浪/腾讯备：东财空或 skip_em 时先打；分钟仅近端约 1023 根（5m ≈ 20 日）；有数则不再打 BaoStock。
BaoStock 备：5/15/30/60 约近 **30 日历日**（与东财同窗）；仅东财与新浪/腾讯都空、或东财短于该窗口时再拉。
做 T 第一触达默认 period=5。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.data.policy import MINUTE_CACHE_HOURS, minute_fetch_delay_sec
from core.numbers import to_float as _to_float
from core.store import (
    align_minute_volume_units,
    load_minute_cache,
    merge_minute_bars_by_time,
    save_minute_cache,
)
from skills.common.history import resolve_market_code

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
        old = load_minute_cache(
            market, bare, period, min_bars=1, max_age_hours=0, ignore_age=True
        )
        if old:
            bars = merge_minute_bars_by_time(old[0], bars)
        save_minute_cache(
            market,
            bare,
            bars,
            period=period,
            data_source=data_source,
            stock_code=bare,
            adjust_policy=adjust_policy,
        )
        meta["bar_count"] = len(bars)
        meta["cached"] = True
    return bars, meta


def _fetch_em_minute_bars(
    bare: str,
    *,
    period: str,
    lookback_days: int,
    adjust: str,
) -> Tuple[List[dict], Dict[str, Any], Optional[str]]:
    """东财分钟线；返回 (bars, meta, error)。"""
    from core.data.policy import minute_em_lookback_days
    from core.http_retry import call_with_retry
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    end = datetime.now()
    cap = minute_em_lookback_days()
    if period == "1":
        span = min(int(lookback_days or 5), 8)
    else:
        span = min(max(int(lookback_days or cap), 5), cap)
    start = end - timedelta(days=max(span, 10))
    start_s = start.strftime("%Y-%m-%d 09:30:00")
    end_s = end.strftime("%Y-%m-%d 15:00:00")
    adj = "" if period == "1" else (adjust or "qfq")
    try:
        def _once():
            return ak.stock_zh_a_hist_min_em(
                symbol=bare,
                start_date=start_s,
                end_date=end_s,
                period=period,
                adjust=adj,
            )

        df = call_with_retry(_once, retries=2, base_delay_sec=0.6, max_delay_sec=5.0)
    except Exception as e:
        logger.warning(
            "fetch_a_minute_bars em failed %s period=%s: %s", bare, period, e
        )
        return [], {}, str(e)

    records = df.to_dict(orient="records") if df is not None and hasattr(df, "to_dict") else []
    bars = normalize_minute_bars(records)
    src = f"akshare:stock_zh_a_hist_min_em:{period}"
    meta: Dict[str, Any] = {
        "data_source": src,
        "bar_count": len(bars),
        "adjust_policy": adj or None,
        "em_start": start_s,
        "em_end": end_s,
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
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
    from skills.common.baostock_minute import (
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


def _maybe_fetch_sina_tx_minute_bars(
    bare: str,
    *,
    period: str,
    lookback_days: int,
    reason: str = "em_empty",
) -> Tuple[List[dict], Dict[str, Any]]:
    from skills.common.sina_tx_minute import fetch_sina_tx_minute_bars, sina_tx_enabled

    if not sina_tx_enabled():
        return [], {}
    bars, meta = fetch_sina_tx_minute_bars(
        bare, period=period, lookback_days=lookback_days
    )
    meta = dict(meta or {})
    meta["backfill_reason"] = reason
    return list(bars or []), meta


def _throttle_minute_remote_fetch() -> None:
    """东财 / 新浪腾讯 / BaoStock 分钟远端拉取后的频控间隔。"""
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
    lookback_days: int = 30,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
    skip_em: bool = False,
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 A 股分钟线；失败返回空列表。

    period: "1"|"5"|"15"|"30"|"60"
    use_cache: True 时先读本地有效仓；False 跳过读仓直接打远端。
      远端成功后**始终** merge+save（与 use_cache 无关），避免 force refresh 不落盘。
    skip_em: True 时跳过东财（T0 回测 / 显式 skip）；新浪/腾讯有数则不再打 BaoStock。
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
        as_bars, as_meta = _maybe_fetch_sina_tx_minute_bars(
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
            bars = merge_minute_bars_by_time(bars, as_bars)

    if as_bars:
        # 新浪/腾讯已接住近端：不再打 BaoStock（避免超时空等；更长窗口仍靠东财）
        bs_bars, bs_meta = [], {"skipped": True, "reason": "sina_tx_ok"}
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
            bars = merge_minute_bars_by_time(bars, bs_bars)

    sources: List[str] = []
    if em_bars:
        sources.append(str(em_meta.get("data_source") or "em"))
    if as_bars:
        sources.append(str(as_meta.get("data_source") or "sina_tx"))
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
        hint = "东财/新浪腾讯/BaoStock 分钟源均失败；可稍后重试"
        return [], {
            "data_source": "empty",
            "error": em_error or as_meta.get("error") or bs_meta.get("error") or "no_minute_bars",
            "period": period,
            "hint": hint,
            "em_error": em_error,
            "bs_error": bs_meta.get("error"),
            "sina_tx_error": as_meta.get("error"),
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
        meta["sina_tx"] = as_meta
    if em_error and not skip_em:
        meta["em_error"] = em_error
    if skip_em:
        meta["skip_em"] = True
    return bars, meta


def fetch_minute_bars(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 30,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
    skip_em: bool = False,
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
    )
