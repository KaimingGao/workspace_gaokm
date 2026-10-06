"""短期过热因子：近窗涨幅越大分越低（风险/反转取向）。

与 ``momentum``（趋势加分）拆开；与已退役 raw ``mom_overheat`` 键名不同，避免被 taxonomy 剥离。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.signal.factors.momentum import pct_change

logger = logging.getLogger(__name__)


def overheat_raw(
    mom3: Optional[float],
    mom5: Optional[float],
    day_gain: Optional[float],
) -> Optional[float]:
    """加权过热原始幅度（%）；缺关键腿则 None。"""
    parts: List[Tuple[float, float]] = []
    if mom5 is not None:
        parts.append((0.6, max(0.0, float(mom5))))
    if mom3 is not None:
        parts.append((0.3, max(0.0, float(mom3))))
    if day_gain is not None:
        parts.append((0.1, max(0.0, float(day_gain))))
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    if wsum <= 0:
        return None
    return sum(w * v for w, v in parts) / wsum


def score_from_raw(raw: Optional[float]) -> float:
    """raw 过热幅度 → 0–100（越高越安全 / 越不过热）。"""
    if raw is None:
        return 50.0
    if raw < 4.0:
        return 70.0
    if raw < 6.0:
        return 55.0 - (raw - 4.0) * 5.0  # 55→45
    if raw < 8.0:
        return 45.0 - (raw - 6.0) * 5.0  # 45→35
    if raw < 12.0:
        return 35.0 - (raw - 8.0) * 3.75  # 35→20
    return max(10.0, 20.0 - (raw - 12.0) * 2.0)


def overheat_scale_from_raw(raw: Optional[float]) -> float:
    """ŷ / 权重软折扣：1=不打折，越热越小。"""
    if raw is None:
        return 1.0
    if raw < 4.0:
        return 1.0
    if raw < 6.0:
        return 0.85
    if raw < 8.0:
        return 0.6
    if raw < 10.0:
        return 0.4
    if raw < 12.0:
        return 0.25
    return 0.15


def score_overheat(bars: List[dict]) -> Tuple[float, Dict[str, Any]]:
    """日线过热分；bars 不足时 omit（不进 ŷ）。"""
    if not bars or len(bars) < 3:
        return 50.0, {"ok": False, "reason": "thin_bars", "omit_sub_score": True}

    mom3 = pct_change(bars, min(3, len(bars) - 1))
    mom5 = pct_change(bars, min(5, len(bars) - 1)) if len(bars) > 5 else mom3
    day_gain = None
    try:
        o = float(bars[-1].get("open") or 0)
        c = float(bars[-1].get("close") or 0)
        if o > 0:
            day_gain = (c / o - 1.0) * 100.0
    except (TypeError, ValueError):
        day_gain = None

    raw = overheat_raw(mom3, mom5, day_gain)
    score = score_from_raw(raw)
    return float(score), {
        "ok": True,
        "omit_sub_score": False,
        "overheat_raw_pct": None if raw is None else round(raw, 3),
        "overheat_scale": round(overheat_scale_from_raw(raw), 3),
        "momentum_3d": None if mom3 is None else round(mom3, 2),
        "momentum_5d": None if mom5 is None else round(mom5, 2),
        "day_gain_pct": None if day_gain is None else round(day_gain, 2),
    }
