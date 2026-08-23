"""调仓卖出原因常量（文案与 soft-hold 判定解耦）。"""

from __future__ import annotations

# 卖出原因标识（用常量替代字符串匹配，避免文案改动导致 soft-hold 逻辑失效）
SELL_REASON_BELOW_HOLD = "below_hold"  # ŷ_trade 低于卖出门槛
SELL_REASON_NOT_IN_TOPK = "not_in_topk"  # 不在横截面 TopK
SELL_REASON_HARD_REJECT = "hard_reject"  # 硬拒绝
SELL_REASON_SENTIMENT_TRIM = "sentiment_trim"  # 舆情缩仓
SELL_REASON_MARKET_TRIM = "market_prior_trim"  # 市场 prior 缩仓

# 可被 soft-hold 豁免的卖出原因
_SOFT_HOLD_ELIGIBLE_REASONS = frozenset(
    {
        SELL_REASON_BELOW_HOLD,
        SELL_REASON_NOT_IN_TOPK,
    }
)

__all__ = [
    "SELL_REASON_BELOW_HOLD",
    "SELL_REASON_NOT_IN_TOPK",
    "SELL_REASON_HARD_REJECT",
    "SELL_REASON_SENTIMENT_TRIM",
    "SELL_REASON_MARKET_TRIM",
    "_SOFT_HOLD_ELIGIBLE_REASONS",
]
