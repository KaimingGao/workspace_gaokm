"""DecisionRecord：一次可审计的建议快照（输入 → stance → 失效条件）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from core.paths import DECISIONS_PATH


def build_decision_record(
    advice: Dict[str, Any],
    *,
    source: str = "advise",
    session_id: str = "",
) -> Dict[str, Any]:
    """从 evaluate_buy_advice / position 行构造标准决策记录。"""
    return {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "source": source,
        "session_id": session_id or None,
        "stock_code": advice.get("stock_code"),
        "stock_name": advice.get("stock_name"),
        "horizon_days": advice.get("horizon_days"),
        "stance_code": advice.get("stance_code"),
        "stance_label": advice.get("stance_label"),
        "confidence": advice.get("confidence"),
        "reasons": advice.get("reasons") or [],
        "invalidation": advice.get("invalidation"),
        "facts": advice.get("facts"),
        "action": advice.get("action"),  # position 规则动作（可选）
        "note": (
            "DecisionRecord 仅供研究与审计；非实盘委托。"
            "市场有风险，不保证收益，不代客下单。"
        ),
    }


def append_decision(record: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DECISIONS_PATH
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "path": p, "id": record.get("id")}


def list_decisions(*, limit: int = 50, path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DECISIONS_PATH
    if not os.path.isfile(p):
        return {"ok": True, "path": p, "count": 0, "items": []}
    rows: List[Dict[str, Any]] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    items = rows[-max(1, int(limit)) :]
    items.reverse()
    return {"ok": True, "path": p, "count": len(rows), "items": items}


def record_from_advice(
    advice: Dict[str, Any],
    *,
    source: str = "advise",
    session_id: str = "",
    persist: bool = True,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    if not advice.get("success", True) and advice.get("stance_label") is None:
        return {"ok": False, "error": advice.get("error") or "advice unsuccessful"}
    rec = build_decision_record(advice, source=source, session_id=session_id)
    if persist:
        append_decision(rec, path=path)
    return {"ok": True, "record": rec}
