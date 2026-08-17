"""持仓与 stance 交叉引用（轻量，不含完整 advise 管线）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.signal.service import get_default_signal_service
from core.stance import compute_buy_stance


def attach_stance_to_advice(
    advice: dict,
    *,
    quote: dict,
    horizon_days: int = 3,
    full_context: bool = False,
) -> dict:
    """
    为持仓规则 advice 附加 stance_label（可选 full_context 走 collect_stock_facts）。
    """
    code = advice.get("stock_code") or quote.get("stock_code")
    if not code:
        return advice

    if full_context:
        from core.facts import collect_stock_facts

        facts = collect_stock_facts(
            str(code),
            horizon_days=horizon_days,
            include_peer=True,
            include_index=True,
        )
        stance = compute_buy_stance(
            quote=facts["quote"],
            signal_item=facts.get("signal_item") or {},
            kline=facts["kline"] if facts["kline"].get("success") else {},
            peer=facts["peer"] if facts["peer"].get("success") else None,
            index=facts["index"] if facts["index"].get("success") else None,
        )
        signal_item = facts.get("signal_item") or {}
    else:
        scored = get_default_signal_service().score_one(
            str(code), horizon_days=horizon_days, quote=quote
        )
        signal_item = scored.item or {}
        stance = compute_buy_stance(
            quote=quote,
            signal_item=signal_item,
        )

    advice = dict(advice)
    advice["stance_code"] = stance.get("stance_code")
    advice["stance_label"] = stance.get("stance_label")
    advice["signal_score"] = signal_item.get("score")
    advice["signal_hard_reject"] = signal_item.get("hard_reject")
    return advice
