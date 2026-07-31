from agent.contracts import BaseSkillHandler
from skills.fundamentals.engine import build_fundamentals


class FundamentalsHandler(BaseSkillHandler):
    error_prefix = "基本面查询失败"

    def handle(self, params: dict) -> dict:
        code = (params.get("stock_code") or "").strip()
        if not code:
            return {"success": False, "error": "请提供 stock_code"}
        return build_fundamentals(code)
