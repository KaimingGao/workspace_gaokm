"""Web / CLI 会话服务。"""


import logging

logger = logging.getLogger(__name__)
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from agent.agent import MAX_TOOL_ROUNDS, InvestmentAgent
from agent.artifacts import primary_tab
from agent.prompts import SYSTEM_PROMPT
from core.job_progress import job_registry

# 会话空闲淘汰；消息滑动窗口（含 system）
_SESSION_TTL_SEC = float(os.environ.get("CHAT_SESSION_TTL_SEC", "3600") or 3600)
_MAX_MESSAGES = int(os.environ.get("CHAT_MAX_MESSAGES", "40") or 40)
_MAX_SESSIONS = int(os.environ.get("CHAT_MAX_SESSIONS", "64") or 64)

chat_job = job_registry.slot("chat")


class ChatService:
    def __init__(self) -> None:
        self._sessions: Dict[str, InvestmentAgent] = {}
        self._touched: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()

    def _touch(self, sid: str) -> None:
        self._touched[sid] = time.time()

    def _evict_unlocked(self) -> None:
        now = time.time()
        ttl = max(60.0, _SESSION_TTL_SEC)
        dead = [s for s, ts in self._touched.items() if now - ts > ttl]
        for s in dead:
            self._sessions.pop(s, None)
            self._touched.pop(s, None)
        if len(self._sessions) <= _MAX_SESSIONS:
            return
        # 超上限：按最久未用淘汰
        ordered = sorted(self._touched.items(), key=lambda kv: kv[1])
        overflow = len(self._sessions) - _MAX_SESSIONS
        for sid, _ in ordered[:overflow]:
            self._sessions.pop(sid, None)
            self._touched.pop(sid, None)

    @staticmethod
    def _trim_messages(agent: InvestmentAgent) -> None:
        msgs = agent.messages
        if not msgs or len(msgs) <= _MAX_MESSAGES:
            return
        system = msgs[0] if msgs and msgs[0].get("role") == "system" else {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
        tail = msgs[1:][-(max(2, _MAX_MESSAGES - 1)) :]
        agent.messages = [system] + list(tail)

    def get_or_create(self, session_id: Optional[str] = None) -> Tuple[str, InvestmentAgent]:
        with self._lock:
            self._evict_unlocked()
            sid = (session_id or "").strip() or str(uuid.uuid4())
            agent = self._sessions.get(sid)
            if agent is None:
                agent = InvestmentAgent()
                self._sessions[sid] = agent
            self._touch(sid)
            return sid, agent

    def reset(self, session_id: Optional[str] = None) -> Tuple[str, InvestmentAgent]:
        sid, agent = self.get_or_create(session_id)
        agent.reset()
        with self._lock:
            self._touch(sid)
        return sid, agent

    def chat_sync(self, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """同步对话（单测 / 兼容旧客户端）。"""
        sid, agent = self.get_or_create(session_id)
        reply = agent.chat(message)
        self._trim_messages(agent)
        with self._lock:
            self._touch(sid)
        artifacts = []
        if hasattr(agent, "get_last_artifacts"):
            artifacts = agent.get_last_artifacts()
        elif getattr(agent, "last_artifacts", None):
            artifacts = list(agent.last_artifacts)
        return {
            "session_id": sid,
            "reply": reply,
            "turn_usage": agent.get_last_turn_usage(),
            "session_usage": agent.get_session_usage(),
            "artifacts": artifacts,
            "primary_tab": primary_tab(artifacts),
        }

    def start_chat_job(
        self, message: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """后台跑一轮对话；前端轮询 ``/api/jobs/chat``。"""
        text = (message or "").strip()
        if not text:
            return {"ok": False, "error": "消息不能为空"}
        sid, agent = self.get_or_create(session_id)
        if not agent.llm.api_key:
            return {"ok": False, "error": "未配置 DASHSCOPE_API_KEY", "session_id": sid}
        if chat_job.is_running():
            return {
                "ok": False,
                "error": "已有对话任务在运行",
                "session_id": sid,
                "job": chat_job.get(),
            }

        job_id = chat_job.start(
            kind="chat",
            total=MAX_TOOL_ROUNDS + 2,
            message="排队中",
        )

        def _worker() -> None:
            if not self._run_lock.acquire(blocking=False):
                chat_job.finish(error="对话任务锁被占用")
                return
            stop = threading.Event()

            def _heartbeat() -> None:
                while not stop.wait(25.0):
                    chat_job.touch()

            hb = threading.Thread(target=_heartbeat, name="chat-job-hb", daemon=True)
            hb.start()
            try:
                chat_job.update(current=1, message="思考中…")
                result = self.chat_sync(text, session_id=sid)
                chat_job.update(current=MAX_TOOL_ROUNDS + 2, message="完成")
                chat_job.finish(result=result)
            except Exception as e:
                logger.exception('unexpected error in _worker')
                chat_job.finish(error=str(e))
            finally:
                stop.set()
                self._run_lock.release()

        threading.Thread(target=_worker, name=f"chat-job-{job_id}", daemon=True).start()
        return {
            "ok": True,
            "background": True,
            "session_id": sid,
            "job": chat_job.get(),
        }

    def get_job(self) -> Dict[str, Any]:
        return {"ok": True, "job": chat_job.get()}
