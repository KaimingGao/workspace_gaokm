from agent.contracts import BaseSkillHandler
from quant.skill.engine import QuantEngine


class QuantHandler(BaseSkillHandler):
    error_prefix = "量化研究失败"

    def __init__(self):
        self.engine = QuantEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.run(params or {})
