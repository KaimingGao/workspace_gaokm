from agent.contracts import BaseSkillHandler
from skills.compare.engine import CompareEngine


class CompareHandler(BaseSkillHandler):
    error_prefix = "股票对比失败"

    def __init__(self) -> None:
        self._engine = CompareEngine()

    def handle(self, params: dict) -> dict:
        return self._engine.compare(params)
