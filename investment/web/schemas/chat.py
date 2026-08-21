"""Chat API 请求/响应模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    turn_usage: dict
    session_usage: dict
    artifacts: list = Field(default_factory=list)
    primary_tab: str = "reply"


class ChatAsyncResponse(BaseModel):
    ok: bool = True
    background: bool = True
    session_id: str
    job: dict = Field(default_factory=dict)

