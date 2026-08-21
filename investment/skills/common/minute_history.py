"""A 股分钟线拉取（东财）；本地缓存 data/store/minute/{period}/CN/{code}.json。

限量（东财常见）：1 分钟约近 5 日；5/15/30/60 分钟约近 120 交易日。
做 T 第一触达默认 period=5。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from core.data_policy import MINUTE_CACHE_HOURS
from core.numbers import to_float as _to_float
from core.store import load_minute_cache, merge_minute_bars_by_time, save_minute_cache
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


def fetch_a_minute_bars(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 90,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
    adjust: str = "qfq",
) -> Tuple[List[dict], Dict[str, Any]]:
    """拉取 A 股分钟线；失败返回空列表。

    period: "1"|"5"|"15"|"30"|"60"
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

    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    end = datetime.now()
    # 1 分钟源通常只有近 5 日；5 分钟约 120 交易日
    if period == "1":
        span = min(int(lookback_days or 5), 8)
    else:
        span = min(max(int(lookback_days or 90), 5), 150)
    start = end - timedelta(days=max(span * 2, 10))
    start_s = start.strftime("%Y-%m-%d 09:30:00")
    end_s = end.strftime("%Y-%m-%d 15:00:00")

    adj = "" if period == "1" else (adjust or "qfq")
    try:
        df = ak.stock_zh_a_hist_min_em(
            symbol=bare,
            start_date=start_s,
            end_date=end_s,
            period=period,
            adjust=adj,
        )
    except Exception as e:
        logger.exception('unexpected error in fetch_a_minute_bars')
        return [], {"data_source": "empty", "error": str(e), "period": period}

    records = df.to_dict(orient="records") if df is not None and hasattr(df, "to_dict") else []
    bars = normalize_minute_bars(records)
    src = f"akshare:stock_zh_a_hist_min_em:{period}"
    meta: Dict[str, Any] = {
        "market": market,
        "code": bare,
        "stock_code": bare,
        "period": period,
        "data_source": src,
        "from_cache": False,
        "ok": bool(bars),
        "bar_count": len(bars),
        "adjust_policy": adj or None,
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
        if use_cache:
            # 与旧缓存增量合并
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
                data_source=src,
                stock_code=bare,
                adjust_policy=adj or None,
            )
            meta["bar_count"] = len(bars)
            meta["cached"] = True
    return bars, meta


def fetch_minute_bars(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 90,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """对外入口：目前仅 A 股。"""
    return fetch_a_minute_bars(
        code,
        period=period,
        lookback_days=lookback_days,
        use_cache=use_cache,
        max_age_hours=max_age_hours,
    )
