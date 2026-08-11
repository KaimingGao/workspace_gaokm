"""对话 / 会话 API。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from web import deps
from web.schemas import ChatRequest, ChatResponse, ChatAsyncResponse
from agent.artifacts import primary_tab

router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
):
    """同步对话（兼容单测 / 旧客户端）。UI 请优先 ``/api/chat/async``。"""
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")

    sid, agent = deps.chat.get_or_create(x_session_id)
    if not agent.llm.api_key:
        raise HTTPException(status_code=503, detail="未配置 DASHSCOPE_API_KEY")

    try:
        out = deps.chat.chat_sync(text, session_id=sid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ChatResponse(
        session_id=out["session_id"],
        reply=out["reply"],
        turn_usage=out["turn_usage"],
        session_usage=out["session_usage"],
        artifacts=out.get("artifacts") or [],
        primary_tab=out.get("primary_tab") or primary_tab(out.get("artifacts") or []),
    )


@router.post("/api/chat/async", response_model=ChatAsyncResponse)
def chat_async(
    body: ChatRequest,
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-Id"),
):
    """立即返回；轮询 ``GET /api/jobs/chat`` 取结果。"""
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    out = deps.chat.start_chat_job(text, session_id=x_session_id)
    if not out.get("ok"):
        status = 503 if "API_KEY" in str(out.get("error") or "") else 409
        raise HTTPException(status_code=status, detail=out.get("error") or "启动失败")
    return ChatAsyncResponse(
        ok=True,
        background=True,
        session_id=out["session_id"],
        job=out.get("job") or {},
    )


@router.get("/api/chat/job")
def chat_job():
    """兼容别名；规范入口 ``/api/jobs/chat``。"""
    out = deps.chat.get_job()
    return {**out, "deprecated": True, "canonical": "/api/jobs/chat"}


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
