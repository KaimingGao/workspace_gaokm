"""质量因子（P46 / V2.1）：ROE only；增速已拆至 growth 因子。"""

from __future__ import annotations
from core.numbers import to_float as _to_float

from typing import Any, Dict, Optional


def score_quality(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> tuple[float, dict]:
    """缺 ROE → 不进 ŷ（omit）。"""
    _ = bars
    roe = _to_float((fundamentals or {}).get("roe"))

    if roe is None:
        return 50.0, {"quality_roe": None, "omit_sub_score": True}

    score = 50.0
    if roe >= 20.0:
        score += 18.0
    elif roe >= 12.0:
        score += 10.0
    elif roe >= 5.0:
        score += 3.0
    else:
        score -= 10.0

    return max(10.0, min(95.0, round(score, 1))), {
        "quality_roe": round(roe, 2),
        "omit_sub_score": False,
    }
