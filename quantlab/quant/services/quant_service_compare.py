"""QuantService · ③ 持仓联动摘要（只读）。"""

from typing import Any, Dict


class QuantCompareMixin:
    """③ 联动：模拟持仓 ↔ watching / quant 状态（不下单、不做不公平对照）。"""

    def build_portfolio_bridge(self, *, include_stance: bool = False) -> Dict[str, Any]:
        from quant.services.portfolio_quant_bridge import build_portfolio_quant_bridge

        return build_portfolio_quant_bridge(include_stance=include_stance)
