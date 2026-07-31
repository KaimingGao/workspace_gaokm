from agent.contracts import BaseSkillHandler
from skills.screen.engine import StockScreener


class ScreenHandler(BaseSkillHandler):
    error_prefix = "选股失败"

    def __init__(self):
        self.screener = StockScreener()

    def handle(self, params: dict) -> dict:
        return self.screener.screen(params or {})
