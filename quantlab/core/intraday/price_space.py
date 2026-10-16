"""Pure price-space helpers shared by t0 and paper replay."""

from __future__ import annotations

from typing import Any, Optional

# |日线开/分钟开 − 1| 超此百分比 → price_space_mismatch（可配）
DEFAULT_PRICE_SPACE_MAX_DEV_PCT = 5.0


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def price_space_scale(
    daily_open: Optional[float],
    minute_open: Optional[float],
) -> Optional[float]:
    """S = O_d / O_m；缺一侧或非正 → None。"""
    od, om = _f(daily_open), _f(minute_open)
    if od is None or om is None or od <= 0 or om <= 0:
        return None
    return float(od) / float(om)
