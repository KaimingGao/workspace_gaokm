from agent.contracts import BaseSkillHandler
from skills.backtest.engine import BacktestEngine


class BacktestHandler(BaseSkillHandler):
    error_prefix = "回测失败"

    def __init__(self):
        self.engine = BacktestEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.run(params or {})
