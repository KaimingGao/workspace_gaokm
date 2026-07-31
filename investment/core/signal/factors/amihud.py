"""Amihud 非流动性因子（V2.1）：高价格冲击降分，相对 liquidity（活跃度）正交。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _bar_amount(bar: dict) -> float:
    amt = bar.get("amount")
    try:
        if amt is not None:
            return max(0.0, float(amt))
    except (TypeError, ValueError):
        pass
    vol = float(bar.get("volume") or 0.0)
    close = float(bar.get("close") or 0.0)
    return max(0.0, vol * close)


def _amihud_mean(bars: List[dict], window: int = 10) -> Optional[float]:
    if not bars or len(bars) < 2:
        return None
    window = min(window, len(bars) - 1)
    vals: List[float] = []
    for i in range(len(bars) - window, len(bars)):
        prev = float(bars[i - 1].get("close") or 0.0)
        cur = float(bars[i].get("close") or 0.0)
        if prev <= 0 or cur <= 0:
            continue
        ret = abs(cur / prev - 1.0)
        amt = _bar_amount(bars[i])
        if amt <= 0:
            continue
        vals.append(ret / amt)
    if not vals:
        return None
    return sum(vals) / len(vals)


def score_amihud(bars: List[dict], **_kw) -> Tuple[float, Dict[str, Any]]:
    """缺数据 → 50。冲击越大分越低。"""
    raw = _amihud_mean(bars or [])
    if raw is None:
        return 50.0, {"amihud": None, "ok": False}

    # 用数量级粗映射：A 股 volume×price 量级差异大，取 log 友好阈值
    # raw 通常很小；放大后分段
    scaled = raw * 1e9
    if scaled <= 0.5:
        score = 72.0
    elif scaled <= 2.0:
        score = 62.0
    elif scaled <= 8.0:
        score = 52.0
    elif scaled <= 20.0:
        score = 42.0
    else:
        score = 32.0

    return float(score), {
        "amihud": round(raw, 12),
        "amihud_scaled": round(scaled, 4),
        "ok": True,
    }
