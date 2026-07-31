"""Web / CLI 会话服务。"""

from __future__ import annotations

import uuid
from typing import Dict, Optional, Tuple

from agent.agent import InvestmentAgent


class ChatService:
    def __init__(self) -> None:
        self._sessions: Dict[str, InvestmentAgent] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> Tuple[str, InvestmentAgent]:
        sid = (session_id or "").strip() or str(uuid.uuid4())
        agent = self._sessions.get(sid)
        if agent is None:
            agent = InvestmentAgent()
            self._sessions[sid] = agent
        return sid, agent

    def reset(self, session_id: Optional[str] = None) -> Tuple[str, InvestmentAgent]:
        sid, agent = self.get_or_create(session_id)
        agent.reset()
        return sid, agent
