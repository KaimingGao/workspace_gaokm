"""估值因子（P46）：PE/PB 适中区间加分，极端估值降分。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def _pick_pe(fundamentals: Optional[dict]) -> Optional[float]:
    if not fundamentals:
        return None
    pe = fundamentals.get("pe")
    if pe is None:
        pe = fundamentals.get("pe_ttm")
    if pe is None:
        return None
    try:
        val = float(pe)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val


def _pick_pb(fundamentals: Optional[dict]) -> Optional[float]:
    if not fundamentals:
        return None
    pb = fundamentals.get("pb")
    if pb is None:
        return None
    try:
        val = float(pb)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val


def score_value(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> tuple[float, dict]:
    """缺 PE/PB → 不进 ŷ（omit）；有则适中区间加分。"""
    _ = bars
    pe = _pick_pe(fundamentals)
    pb = _pick_pb(fundamentals)
    if pe is None and pb is None:
        return 50.0, {
            "value_pe": None,
            "value_pb": None,
            "omit_sub_score": True,
        }

    score = 50.0
    if pe is not None:
        if 8.0 <= pe <= 25.0:
            score += 12.0
        elif 25.0 < pe <= 40.0:
            score += 4.0
        elif pe > 60.0:
            score -= 12.0
        elif pe < 5.0:
            score -= 6.0

    if pb is not None:
        if 0.8 <= pb <= 3.0:
            score += 8.0
        elif pb > 6.0:
            score -= 8.0
        elif pb < 0.5:
            score -= 4.0

    meta: Dict[str, Any] = {"omit_sub_score": False}
    if pe is not None:
        meta["value_pe"] = round(pe, 2)
    if pb is not None:
        meta["value_pb"] = round(pb, 2)

    return max(10.0, min(95.0, round(score, 1))), meta
