"""规模因子（V2.1）：log(market_cap) 适中区间加分。"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def score_size(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """缺市值 → 50；中盘偏好，过大/过小略降分。"""
    _ = bars
    cap = _to_float((fundamentals or {}).get("market_cap"))
    if cap is None or cap <= 0:
        return 50.0, {"size_market_cap": None, "size_log_cap": None}

    log_cap = math.log(cap)
    # A 股量级粗分：log 约 22～28（约 40 亿～1.5 万亿）中性偏正
    if 23.5 <= log_cap <= 26.5:
        score = 68.0
    elif 22.0 <= log_cap < 23.5 or 26.5 < log_cap <= 28.0:
        score = 58.0
    elif 20.5 <= log_cap < 22.0 or 28.0 < log_cap <= 29.5:
        score = 48.0
    else:
        score = 40.0

    return float(score), {
        "size_market_cap": round(cap, 2),
        "size_log_cap": round(log_cap, 3),
    }
