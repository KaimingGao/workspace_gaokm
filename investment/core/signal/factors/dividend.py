"""股息因子（V2.1）：dividend_yield 适中加分。"""

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional, Tuple

from core.numbers import to_float as _to_float


def score_dividend(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """缺股息率 → 不进 ŷ（omit）；适中股息加分，过高可能不可持续。"""
    _ = bars
    dy = _to_float((fundamentals or {}).get("dividend_yield"))
    if dy is None:
        return 50.0, {"dividend_yield": None, "omit_sub_score": True}

    # 兼容 0.03 与 3.0 两种口径
    if 0 < dy < 0.5:
        dy_pct = dy * 100.0
    else:
        dy_pct = dy

    if 2.0 <= dy_pct <= 5.0:
        score = 68.0
    elif 1.0 <= dy_pct < 2.0 or 5.0 < dy_pct <= 7.0:
        score = 58.0
    elif 0.3 <= dy_pct < 1.0:
        score = 50.0
    elif dy_pct > 7.0:
        score = 45.0
    else:
        score = 48.0

    return float(score), {
        "dividend_yield": round(dy_pct, 3),
        "omit_sub_score": False,
    }
