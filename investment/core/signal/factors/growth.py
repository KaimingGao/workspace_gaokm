"""成长因子（V2.1）：盈利/营收增速；从 quality 拆出以免与 ROE 双计。"""

from __future__ import annotations
from core.numbers import to_float as _to_float

from typing import Any, Dict, Optional, Tuple


def score_growth(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """缺增速 → 50。优先 profit_growth，否则 revenue_growth。"""
    _ = bars
    fund = fundamentals or {}
    profit_growth = _to_float(fund.get("profit_growth"))
    revenue_growth = _to_float(fund.get("revenue_growth"))
    growth = profit_growth if profit_growth is not None else revenue_growth

    if growth is None:
        return 50.0, {
            "growth_profit": None,
            "growth_revenue": None,
            "growth_used": None,
        }

    if growth >= 30.0:
        score = 78.0
    elif growth >= 20.0:
        score = 70.0
    elif growth >= 8.0:
        score = 60.0
    elif growth >= 0.0:
        score = 52.0
    elif growth >= -10.0:
        score = 42.0
    else:
        score = 32.0

    meta: Dict[str, Any] = {
        "growth_used": round(growth, 2),
        "growth_profit": round(profit_growth, 2) if profit_growth is not None else None,
        "growth_revenue": round(revenue_growth, 2) if revenue_growth is not None else None,
    }
    return float(score), meta
