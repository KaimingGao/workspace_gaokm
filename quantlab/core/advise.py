"""买卖建议领域管线：facts → stance → 结构化输出。"""

import os
from typing import Any, Dict

from core.facts import collect_stock_facts, facts_summary
from core.stance import compute_buy_stance


def evaluate_buy_advice(params: dict) -> Dict[str, Any]:
    stock_code = (params.get("stock_code") or "").strip()
    if not stock_code:
        return {"success": False, "error": "请提供 stock_code 或股票名称"}

    horizon = int(params.get("horizon_days") or 3)
    include_peer = params.get("include_peer", True)
    include_index = params.get("include_index", True)

    facts = collect_stock_facts(
        stock_code,
        horizon_days=horizon,
        include_peer=bool(include_peer),
        include_index=bool(include_index),
    )

    quote = facts["quote"]
    kline = facts["kline"]
    peer = facts["peer"]
    index = facts["index"]
    signal_item = facts["signal_item"]

    stance = compute_buy_stance(
        quote=quote,
        signal_item=signal_item,
        kline=kline if kline.get("success") else {},
        peer=peer if peer.get("success") else None,
        index=index if index.get("success") else None,
    )

    out = {
        "success": True,
        "stock_code": facts["stock_code"],
        "stock_name": facts["stock_name"],
        "horizon_days": facts["horizon_days"],
        "stance_code": stance["stance_code"],
        "stance_label": stance["stance_label"],
        "confidence": stance.get("confidence"),
        "reasons": stance.get("reasons"),
        "invalidation": stance.get("invalidation"),
        "facts": facts_summary(facts),
        "note": (
            "stance_label 为规则引擎唯一结论来源；解读须引用此字段，不得自行调整买卖倾向。"
            "市场有风险，不保证收益，不代客下单。"
        ),
    }
    # D3：可选落盘 DecisionRecord（默认开启；测试可设 QUANTLAB_RECORD_DECISIONS=0）
    if os.environ.get("QUANTLAB_RECORD_DECISIONS", "1") not in ("0", "false", "False"):
        try:
            from core.decision_record import record_from_advice

            rec = record_from_advice(out, source="advise", persist=True)
            if rec.get("ok"):
                out["decision_id"] = (rec.get("record") or {}).get("id")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass
    return out
