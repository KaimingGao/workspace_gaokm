from agent.contracts import BaseSkillHandler
from skills.quote.engine import QuoteEngine


class QuoteHandler(BaseSkillHandler):
    error_prefix = "股票查询失败"

    def __init__(self) -> None:
        self._engine = QuoteEngine()

    def handle(self, params: dict) -> dict:
        return self._engine.query(params)
