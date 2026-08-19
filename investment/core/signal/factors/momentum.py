"""动量因子（基于 bars 的纯价量动量信号）。

含：
- pct_change(bars, days)：N 日涨幅 %（用于简单动量）
- 后续可加：residual_momentum、双均线斜率、ROC 平滑等
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import List, Optional


def pct_change(bars: List[dict], days: int) -> Optional[float]:
    if not bars or len(bars) <= days:
        return None
    c0 = bars[-(days + 1)]["close"]
    c1 = bars[-1]["close"]
    if not c0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def mom_score(m: Optional[float]) -> float:
    if m is None:
        return 40.0
    if 0 <= m <= 6:
        return 70 + m * 4
    if 6 < m < 12:
        return 94 - (m - 6) * 6
    if -4 <= m < 0:
        return 55 + m * 5
    if m >= 12:
        return 40.0
    return max(10.0, 40 + m)


def score_momentum(bars: List[dict]) -> tuple[float, dict]:
    mom3 = pct_change(bars, min(3, len(bars) - 1))
    mom5 = pct_change(bars, min(5, len(bars) - 1)) if len(bars) > 5 else mom3
    total = 0.6 * mom_score(mom3) + 0.4 * mom_score(mom5)
    return total, {
        "momentum_3d": None if mom3 is None else round(mom3, 2),
        "momentum_5d": None if mom5 is None else round(mom5, 2),
    }
