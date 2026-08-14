"""盈利收益率因子（V2.1）：EP = 1/PE_TTM，与 PE/PB 分段的 value 正交。"""

from __future__ import annotations
from core.numbers import to_float as _to_float

from typing import Any, Dict, Optional, Tuple


def score_earnings_yield(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """缺 PE → 不进 ŷ（omit）；EP 适中加分，极端低估/高估降分。"""
    _ = bars
    fund = fundamentals or {}
    pe = _to_float(fund.get("pe_ttm"))
    if pe is None:
        pe = _to_float(fund.get("pe"))
    if pe is None or pe <= 0:
        return 50.0, {
            "earnings_yield": None,
            "earnings_yield_pe": None,
            "omit_sub_score": True,
        }

    ep = 1.0 / pe  # 例如 PE=20 → 0.05
    ep_pct = ep * 100.0

    if 4.0 <= ep_pct <= 8.0:
        score = 70.0
    elif 2.5 <= ep_pct < 4.0 or 8.0 < ep_pct <= 12.0:
        score = 58.0
    elif 1.5 <= ep_pct < 2.5 or 12.0 < ep_pct <= 18.0:
        score = 48.0
    elif ep_pct < 1.5:
        score = 38.0  # 极贵
    else:
        score = 42.0  # 极便宜，可能价值陷阱

    return float(score), {
        "earnings_yield": round(ep, 5),
        "earnings_yield_pct": round(ep_pct, 3),
        "earnings_yield_pe": round(pe, 2),
        "omit_sub_score": False,
    }
