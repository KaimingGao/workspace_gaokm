"""统一观测信封（Skill / Job / Agent 共用）。"""


import logging

logger = logging.getLogger(__name__)
import time
import uuid
from typing import Any, Dict, Optional


def make_observation(
    *,
    source: str,
    kind: str,
    success: bool,
    data: Optional[Dict[str, Any]] = None,
    summary: str = "",
    error: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """标准 Observation：下游 UI / Memory / Decision 只消费此形状。"""
    return {
        "ok": True,
        "observation_id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "source": source,
        "kind": kind,
        "success": bool(success),
        "summary": summary or (error or ""),
        "error": error,
        "data": data or {},
        "meta": meta or {},
    }


def wrap_skill_result(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """把既有 Skill JSON 包一层 Observation，不破坏原字段。"""
    success = bool(payload.get("success", payload.get("ok", True)))
    err = payload.get("error") or payload.get("detail")
    summary = str(payload.get("summary") or payload.get("stance_label") or err or tool)
    return make_observation(
        source=f"skill:{tool}",
        kind="skill_result",
        success=success,
        data=payload,
        summary=summary,
        error=None if success else str(err or "failed"),
        meta={"tool": tool},
    )
