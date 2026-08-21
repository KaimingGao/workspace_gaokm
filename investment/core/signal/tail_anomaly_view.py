"""tail_anomaly 分钟尾盘视图（供 API / UI 迷你图）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.signal.factors.tail_anomaly import (
    _tail_bars,
    score_tail_anomaly,
    tail_price_slope,
    tail_volume_ratio,
)

logger = logging.getLogger(__name__)


def _last_day_bars(minute_bars: List[dict]) -> List[dict]:
    if not minute_bars:
        return []
    by_date: dict = {}
    for b in minute_bars:
        d = str(b.get("date") or str(b.get("datetime") or "")[:10])
        if d:
            by_date.setdefault(d, []).append(b)
    if not by_date:
        return list(minute_bars)
    last_day = sorted(by_date.keys())[-1]
    return sorted(by_date[last_day], key=lambda x: str(x.get("datetime") or ""))


def _bar_time_label(b: dict) -> str:
    dt = str(b.get("datetime") or b.get("time") or "")
    if len(dt) >= 16:
        return dt[11:16]
    if len(dt) >= 5:
        return dt[-5:]
    return str(b.get("date") or "")[-5:] or "—"


def build_minute_tail_view(
    stock_code: str,
    *,
    tail_minutes: int = 30,
    period: str = "5",
    max_age_hours: float = 12.0,
) -> Dict[str, Any]:
    """从分钟缓存构建尾盘迷你图 payload；无缓存时 ok=False。"""
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

        packed = load_minute_cache(
            market,
            bare,
            period=str(period or "5"),
            min_bars=4,
            max_age_hours=float(max_age_hours),
        )
        if not packed:
            return {
                "ok": False,
                "reason": "no_minute_cache",
                "stock_code": code,
                "hint": "运行 minute_warmup 或 pre_market_ingest",
            }
        bars, meta = packed
        day_bars = _last_day_bars(bars)
        tail = _tail_bars(bars, tail_minutes=tail_min)
        score, fac_meta = score_tail_anomaly([], minute_bars=bars, tail_minutes=tail_min)
        omit = bool((fac_meta or {}).get("omit_sub_score"))
        chart_tail = [
            {
                "label": _bar_time_label(b),
                "close": float(b["close"]) if b.get("close") is not None else None,
                "volume": float(b["volume"]) if b.get("volume") is not None else None,
            }
            for b in tail
            if isinstance(b, dict)
        ]
        chart_day = [
            {
                "label": _bar_time_label(b),
                "close": float(b["close"]) if b.get("close") is not None else None,
                "volume": float(b["volume"]) if b.get("volume") is not None else None,
            }
            for b in day_bars[-min(len(day_bars), 48) :]
            if isinstance(b, dict)
        ]
        return {
            "ok": True,
            "stock_code": code,
            "tail_minutes": tail_min,
            "tail_bars": chart_tail,
            "day_bars": chart_day,
            "tail_volume_ratio": tail_volume_ratio(bars, tail_minutes=tail_min),
            "tail_price_slope_pct": tail_price_slope(
                bars, tail_minutes=min(15, tail_min)
            ),
            "sub_score": None if omit else round(float(score), 1),
            "meta": {
                "period": meta.get("period"),
                "fetched_at": meta.get("fetched_at"),
                "bar_count": meta.get("bar_count"),
                "date_max": meta.get("date_max"),
            },
        }
    except Exception as exc:
        logger.exception('unexpected error in build_minute_tail_view')
        return {"ok": False, "reason": "error", "stock_code": code, "error": str(exc)}
