from agent.contracts import BaseSkillHandler
from adapters.kline.engine import KlineEngine


class KlineHandler(BaseSkillHandler):
    error_prefix = "K线分析失败"

    def __init__(self):
        self.engine = KlineEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.analyze(params or {})
