"""质量因子（P46 / V2.1）：ROE only；增速已拆至 growth 因子。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_quality(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> tuple[float, dict]:
    roe = _to_float((fundamentals or {}).get("roe"))

    if roe is None:
        return 50.0, {"quality_roe": None}

    score = 50.0
    if roe >= 20.0:
        score += 18.0
    elif roe >= 12.0:
        score += 10.0
    elif roe >= 5.0:
        score += 3.0
    else:
        score -= 10.0

    return max(10.0, min(95.0, round(score, 1))), {"quality_roe": round(roe, 2)}
