"""Golden case 路由预期与推断对照表（P26.1 / P31 归位 quant.ops）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List

from agent.routing import (
    is_buy_question,
    is_quant_question,
    prepare_tool_params,
    wants_position_stance,
)
from evals.run_checklist import check_routing_expect, load_cases


def build_eval_routing_map() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    ok_count = 0
    with_expect = 0

    for case in load_cases():
        question = case.get("question") or ""
        expect = case.get("routing_expect") or {}
        failures = check_routing_expect(case)

        inferred: Dict[str, Any] = {
            "is_buy_question": is_buy_question(question),
            "is_quant_question": is_quant_question(question),
            "wants_position_stance": wants_position_stance(question),
        }
        if inferred["is_quant_question"]:
            inferred["quant_task"] = prepare_tool_params("quant", {}, question).get("task")

        row = {
            "id": case["id"],
            "question": question,
            "intent": case.get("intent"),
            "has_routing_expect": bool(expect),
            "routing_expect": expect,
            "inferred": inferred,
            "ok": not failures,
            "failures": failures,
        }
        if expect:
            with_expect += 1
            if row["ok"]:
                ok_count += 1
        rows.append(row)

    return {
        "success": True,
        "count": len(rows),
        "with_expect": with_expect,
        "ok_count": ok_count,
        "cases": rows,
    }
