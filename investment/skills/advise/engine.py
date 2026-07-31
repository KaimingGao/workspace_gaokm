"""advise Skill：薄适配层，委托 core.advise.evaluate_buy_advice。"""

from __future__ import annotations

from core.advise import evaluate_buy_advice


class AdviseEngine:
    def evaluate(self, params: dict) -> dict:
        return evaluate_buy_advice(params)
