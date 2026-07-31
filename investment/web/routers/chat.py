"""对话 / 会话 API。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from web import deps
from web.schemas import ChatRequest, ChatResponse
from agent.artifacts import primary_tab

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
):
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    sid, agent = deps.chat.get_or_create(x_session_id)
    if not agent.llm.api_key:
        raise HTTPException(status_code=503, detail="未配置 DASHSCOPE_API_KEY")
    # 不再在发消息前做短超时探测：探测失败曾把模型永久标为不可用，且 timeout=10 易误杀

    try:
        reply = agent.chat(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    artifacts = []
    if hasattr(agent, "get_last_artifacts"):
        artifacts = agent.get_last_artifacts()
    elif getattr(agent, "last_artifacts", None):
        artifacts = list(agent.last_artifacts)

    return ChatResponse(
        session_id=sid,
        reply=reply,
        turn_usage=agent.get_last_turn_usage(),
        session_usage=agent.get_session_usage(),
        artifacts=artifacts,
        primary_tab=primary_tab(artifacts),
    )


@router.post("/api/reset")
def reset(x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id")):
    sid, agent = deps.chat.reset(x_session_id)
    return {
        "session_id": sid,
        "ok": True,
        "session_usage": agent.get_session_usage(),
    }


@router.get("/api/usage")
def usage(x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id")):
    sid, agent = deps.chat.get_or_create(x_session_id)
    return {
        "session_id": sid,
        "turn_usage": agent.get_last_turn_usage(),
        "session_usage": agent.get_session_usage(),
    }
