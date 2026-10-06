"""健康检查与 README 浏览。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from agent.llm_client import LLMClient
from agent.prompts import DISCLAIMER
from agent.registry import TOOL_NAMES
from core.readme_index import build_readme_index, read_repo_readme

router = APIRouter(tags=["meta"])


@router.get("/api/health")
def health() -> Dict[str, Any]:
    llm = LLMClient()
    available = bool(llm.api_key) and llm.is_available()
    return {
        "ok": True,
        "llm_configured": bool(llm.api_key),
        "llm_available": available,
        "model": llm.model,
        "model_source": getattr(llm, "model_source", "default"),
        "tools": list(TOOL_NAMES),
        "disclaimer": DISCLAIMER,
        "llm_error": None if available else llm.get_last_error(),
    }


@router.get("/api/readme-index")
def readme_index() -> Dict[str, Any]:
    return build_readme_index()


@router.get("/api/readme")
def readme_content(dir: str) -> Dict[str, Any]:
    try:
        out = read_repo_readme(dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "README not found")
    return out
