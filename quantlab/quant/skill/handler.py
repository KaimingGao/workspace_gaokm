"""Quant Skill Handler：对外暴露量化查询能力的 skill 入口。

遵循 Agent Skill 契约：把 params 转发给 QuantEngine.run()，
异常消息统一加上 error_prefix，避免把底层栈信息直接透出给用户。
"""

from agent.contracts import BaseSkillHandler
from quant.skill.engine import QuantEngine


class QuantHandler(BaseSkillHandler):
    error_prefix = "量化研究失败"

    def __init__(self):
        self.engine = QuantEngine()

    def handle(self, params: dict) -> dict:
        return self.engine.run(params or {})
