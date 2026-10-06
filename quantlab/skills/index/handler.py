from agent.contracts import BaseSkillHandler
from adapters.index.engine import build_relative


class IndexHandler(BaseSkillHandler):
    error_prefix = "相对强弱失败"

    def handle(self, params: dict) -> dict:
        code = (params.get("stock_code") or "").strip()
        if not code:
            return {"success": False, "error": "请提供 stock_code"}
        days = params.get("days", params.get("window_days", 20))
        return build_relative(code, params.get("benchmark"), days)
