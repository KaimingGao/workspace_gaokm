"""规模因子（V2.1）：log(market_cap) 适中区间加分。"""

from __future__ import annotations
from core.numbers import to_float as _to_float

import math
from typing import Any, Dict, Optional, Tuple


def score_size(
    bars=None,
    *,
    fundamentals: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """缺市值 → 不进 ŷ（``omit_sub_score``）；有市值则中盘偏好，过大/过小略降分。

    占位返回值仍为 50，仅兼容旧调用；``compute_configured_factors`` 见
    ``omit_sub_score`` 后不会写入 ``sub_scores``，避免 z-score 把「不知道」
    当成相对训练集均值偏低 3σ。
    """
    _ = bars
    cap = _to_float((fundamentals or {}).get("market_cap"))
    if cap is None or cap <= 0:
        return 50.0, {
            "size_market_cap": None,
            "size_log_cap": None,
            "size_missing": True,
            "omit_sub_score": True,
        }

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
        "size_missing": False,
        "omit_sub_score": False,
    }
