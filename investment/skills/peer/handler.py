from agent.contracts import BaseSkillHandler
from skills.peer.engine import build_peer_compare


class PeerHandler(BaseSkillHandler):
    error_prefix = "同行对比失败"

    def handle(self, params: dict) -> dict:
        code = (params.get("stock_code") or "").strip()
        if not code:
            return {"success": False, "error": "请提供 stock_code"}
        return build_peer_compare(code, sector=params.get("sector"))
