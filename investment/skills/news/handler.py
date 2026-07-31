from agent.contracts import BaseSkillHandler
from skills.news.engine import build_news


class NewsHandler(BaseSkillHandler):
    error_prefix = "资讯查询失败"

    def handle(self, params: dict) -> dict:
        code = (params.get("stock_code") or "").strip()
        if not code:
            return {"success": False, "error": "请提供 stock_code"}
        return build_news(code, params.get("limit", 8))
