from agent.contracts import BaseSkillHandler
from services.agent_ops import AgentOpsService


class OpsHandler(BaseSkillHandler):
    error_prefix = "运维工具失败"

    def __init__(self):
        self._svc = AgentOpsService()

    def handle(self, params: dict) -> dict:
        return self._svc.run(params or {})
