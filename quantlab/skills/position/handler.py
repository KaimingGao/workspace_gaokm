from agent.contracts import BaseSkillHandler
from skills.position.engine import PositionEngine


class PositionHandler(BaseSkillHandler):
    error_prefix = "持仓建议失败"

    def __init__(self):
        self.engine = PositionEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.advise(params or {})
