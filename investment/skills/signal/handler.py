from agent.contracts import BaseSkillHandler
from adapters.signal.engine import SignalEngine


class SignalHandler(BaseSkillHandler):
    error_prefix = "短线信号失败"

    def __init__(self):
        self.engine = SignalEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.build_pool(params or {})
