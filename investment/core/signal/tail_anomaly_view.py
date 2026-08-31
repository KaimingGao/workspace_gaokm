"""tail_anomaly 分钟尾盘视图（供 API / UI 迷你图）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.signal.factors.tail_anomaly import (
    _tail_bars,
    score_tail_anomaly,
    tail_price_slope,
    tail_volume_ratio,
)

logger = logging.getLogger(__name__)


def _bars_by_date(minute_bars: List[dict]) -> Dict[str, List[dict]]:
    by_date: Dict[str, List[dict]] = {}
    for b in minute_bars or []:
        d = str(b.get("date") or str(b.get("datetime") or "")[:10])
        if d:
            by_date.setdefault(d, []).append(b)
    for d in by_date:
        by_date[d] = sorted(by_date[d], key=lambda x: str(x.get("datetime") or ""))
    return by_date


def _session_day_bars(
    minute_bars: List[dict],
    as_of: Optional[str],
) -> Tuple[List[dict], str, str]:
    """取涨跌会话日的分钟线。

    返回 (bars, used_day, resolve_note)：
    - 优先 as_of 当天；
    - 无则取 ≤ as_of 的最近有数据交易日；
    - 再无则回退缓存最后一天。
    """
    by_date = _bars_by_date(minute_bars)
    if not by_date:
        return [], "", "empty"
    target = str(as_of or "").strip()[:10]
    days = sorted(by_date.keys())
    if target and target in by_date:
        return by_date[target], target, "exact"
    if target:
        earlier = [d for d in days if d <= target]
        if earlier:
            d = earlier[-1]
            return by_date[d], d, "leq_asof"
    d = days[-1]
    return by_date[d], d, "cache_last"


def _bar_time_label(b: dict) -> str:
    dt = str(b.get("datetime") or b.get("time") or "")
    if len(dt) >= 16:
        return dt[11:16]
    if len(dt) >= 5:
        return dt[-5:]
    return str(b.get("date") or "")[-5:] or "—"


def _parse_bar_datetime(b: dict) -> Optional[datetime]:
    raw = str((b or {}).get("datetime") or (b or {}).get("time") or "").strip()
    if not raw:
        return None
    raw = raw.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19] if len(raw) >= 19 else raw[:16], fmt)
        except ValueError:
            continue
    return None


def _session_day_needs_refresh(
    day_bars: List[dict],
    *,
    session_asof: str,
    now: Optional[datetime] = None,
) -> bool:
    """当前会话日分钟线是否明显落后盘面（有仓不刷新会让涨跌 tip K 停在上午）。"""
    from core.market.calendar import is_trading_day, resolve_session_date
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    sess = str(resolve_session_date(now=n) or "")[:10]
    target = str(session_asof or "")[:10]
    if not target or target != sess:
        return False
    if not is_trading_day(sess):
        return False
    if not day_bars:
        return True
    last = day_bars[-1] if isinstance(day_bars[-1], dict) else {}
    last_dt = _parse_bar_datetime(last)
    if last_dt is None:
        return True
    # 收盘后：应至少看到午后末段 5m
    if (n.hour, n.minute) >= (15, 5):
        return (last_dt.hour, last_dt.minute) < (14, 55)
    # 开盘前不逼刷
    if (n.hour, n.minute) < (9, 30):
        return False
    # 盘中：末 bar 落后超过约两根 5m
    lag_sec = (n.replace(tzinfo=None) - last_dt).total_seconds()
    return lag_sec > 12 * 60


def _resolve_change_asof(as_of: Optional[str] = None) -> str:
    """涨跌数据对应的交易日：显式 as_of，否则当前会话日（周末回退上一交易日）。"""
    day = str(as_of or "").strip()[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return day
    try:
        from core.market.calendar import resolve_session_date
        from core.signal.session_pit import shanghai_now

        return str(resolve_session_date(now=shanghai_now()) or "")[:10]
    except Exception:
        logger.debug("resolve_session_date failed", exc_info=True)
        return ""


def build_minute_tail_view(
    stock_code: str,
    *,
    tail_minutes: int = 30,
    period: str = "5",
    max_age_hours: float = 12.0,
    fetch_if_missing: bool = True,
    lookback_days: int = 5,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """构建涨跌会话日 5m 迷你图；缺新鲜缓存时可按需拉取（默认开）。

    as_of：涨跌数据对应交易日；缺省用 resolve_session_date（周末→上一交易日）。
    """
    code = str(stock_code or "").strip()
    if not code:
        return {"ok": False, "reason": "missing_code"}

    try:
        from core.market import resolve_market_code
        from core.signal.config import load_signal_config
        from core.store import load_minute_cache

        cfg = load_signal_config() or {}
        tail_cfg = (cfg.get("tail_anomaly") or {}) if isinstance(cfg, dict) else {}
        tail_min = int(tail_cfg.get("tail_minutes") or tail_minutes or 30)
        market, bare = resolve_market_code(code)
        if market != "CN" or not bare:
            return {"ok": False, "reason": "unsupported_market", "stock_code": code}

        session_asof = _resolve_change_asof(as_of)
        period_s = str(period or "5")
        packed = load_minute_cache(
            market,
            bare,
            period=period_s,
            min_bars=4,
            max_age_hours=float(max_age_hours),
        )
        source = "cache"
        # 周末/盘后常见：缓存超 max_age 但仍是最近交易日，tip 可先用过期仓
        if not packed:
            packed = load_minute_cache(
                market,
                bare,
                period=period_s,
                min_bars=4,
                max_age_hours=float(max_age_hours),
                ignore_age=True,
            )
            if packed:
                source = "stale_cache"
        if not packed and fetch_if_missing:
            try:
                from core.ports.market import fetch_minute_bars

                bars, meta = fetch_minute_bars(
                    code,
                    period=period_s,
                    use_cache=False,
                    lookback_days=max(1, int(lookback_days or 5)),
                    max_age_hours=float(max_age_hours),
                )
                if bars:
                    packed = (list(bars), dict(meta or {}))
                    source = str((meta or {}).get("data_source") or "fetch")
            except Exception:
                logger.debug("minute-tail on-demand fetch failed for %s", code, exc_info=True)
        if not packed:
            return {
                "ok": False,
                "reason": "no_minute_cache",
                "stock_code": code,
                "hint": "分钟线暂不可用 · 稍后重试或跑 minute_warmup",
                "as_of": session_asof or None,
            }
        bars, meta = packed
        day_bars, used_day, day_note = _session_day_bars(bars, session_asof)
        # 仅缓存路径：会话日明显落后（如只到上午）时强制补拉，避免 tip 与实时涨跌脱节。
        # 本请求已 on-demand fetch 则不再二次拉。
        if (
            fetch_if_missing
            and source in ("cache", "stale_cache")
            and _session_day_needs_refresh(
                day_bars, session_asof=session_asof or used_day
            )
        ):
            try:
                from core.ports.market import fetch_minute_bars

                fresh_bars, fresh_meta = fetch_minute_bars(
                    code,
                    period=period_s,
                    use_cache=False,
                    lookback_days=max(1, int(lookback_days or 5)),
                    max_age_hours=0.01,
                )
                if fresh_bars:
                    bars = list(fresh_bars)
                    meta = dict(fresh_meta or meta or {})
                    source = str((fresh_meta or {}).get("data_source") or "fetch_refresh")
                    packed = (bars, meta)
                    day_bars, used_day, day_note = _session_day_bars(bars, session_asof)
            except Exception:
                logger.debug(
                    "minute-tail session refresh failed for %s", code, exc_info=True
                )
        # 尾盘指标：优先用会话日条；无则退回全样本尾部（兼容旧 tip）
        score_bars = day_bars if day_bars else bars
        tail = _tail_bars(score_bars, tail_minutes=tail_min)
        score, fac_meta = score_tail_anomaly(
            [], minute_bars=score_bars, tail_minutes=tail_min
        )
        omit = bool((fac_meta or {}).get("omit_sub_score"))

        def _chart_bar(b: dict) -> Dict[str, Any]:
            close = float(b["close"]) if b.get("close") is not None else None
            if close is None:
                return {}
            o = float(b["open"]) if b.get("open") is not None else close
            h = float(b["high"]) if b.get("high") is not None else close
            l = float(b["low"]) if b.get("low") is not None else close
            return {
                "label": _bar_time_label(b),
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": float(b["volume"]) if b.get("volume") is not None else None,
            }

        chart_tail = []
        for b in tail or []:
            if not isinstance(b, dict):
                continue
            row = _chart_bar(b)
            if row:
                chart_tail.append(row)
        chart_day = []
        for b in day_bars[-min(len(day_bars), 48) :]:
            if not isinstance(b, dict):
                continue
            row = _chart_bar(b)
            if row:
                chart_day.append(row)
        return {
            "ok": True,
            "stock_code": code,
            "as_of": session_asof or used_day or None,
            "tail_minutes": tail_min,
            "tail_bars": chart_tail,
            "day_bars": chart_day,
            "tail_volume_ratio": tail_volume_ratio(score_bars, tail_minutes=tail_min),
            "tail_price_slope_pct": tail_price_slope(
                score_bars, tail_minutes=min(15, tail_min)
            ),
            "sub_score": None if omit else round(float(score), 1),
            "meta": {
                "period": meta.get("period") or period_s,
                "fetched_at": meta.get("fetched_at"),
                "bar_count": meta.get("bar_count") or len(bars),
                "date_max": used_day or meta.get("date_max"),
                "session_asof": session_asof or None,
                "day_resolve": day_note,
                "source": source,
                "day_bar_count": len(chart_day),
                "refreshed": source.startswith("fetch"),
            },
        }
    except Exception as exc:
        logger.exception("unexpected error in build_minute_tail_view")
        return {"ok": False, "reason": "error", "stock_code": code, "error": str(exc)}
