from agent.contracts import BaseSkillHandler
from skills.advise.engine import AdviseEngine


class AdviseHandler(BaseSkillHandler):
    error_prefix = "投顾规则评估失败"

    def __init__(self):
        self.engine = AdviseEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.evaluate(params or {})
