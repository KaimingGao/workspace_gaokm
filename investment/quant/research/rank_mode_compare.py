"""rank-mode 对照：已退役（系统仅认收益分）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def compare_rank_modes(
    stock_bars: Dict[str, List[dict]],
    **_kwargs: Any,
) -> Dict[str, Any]:
    """规则分对照已全局退役；请用 predicted_score / 拟合ŷ 回测。"""
    del stock_bars
    return {
        "success": False,
        "deprecated": True,
        "promote_ready": False,
        "error": "规则分已全局退役；系统仅认收益分 predicted_score",
        "note": "rank-mode-compare 不再跑 heuristic 臂。",
    }
