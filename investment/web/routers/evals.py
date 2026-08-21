"""Evals / 黄金用例 API。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from agent.llm_client import LLMClient
from web import deps
from web.schemas import EvalRunRequest

router = APIRouter(tags=["evals"])


@router.get("/api/evals/cases")
def evals_cases() -> Dict[str, Any]:
    return {"ok": True, "cases": deps.evals.list_cases()}


@router.get("/api/evals/summary")
def evals_summary() -> Dict[str, Any]:
    return deps.evals.summary()


@router.get("/api/evals/presets")
def evals_presets() -> Any:
    out = deps.evals.check_presets()
    if not out.get("ok"):
        return JSONResponse(status_code=422, content=out)
    return out


@router.get("/api/evals/readme")
def evals_readme() -> Any:
    out = deps.evals.check_readme()
    if not out.get("ok"):
        return JSONResponse(status_code=422, content=out)
    return out


@router.get("/api/evals/routing")
def evals_routing() -> Dict[str, Any]:
    return deps.evals.list_routing()


@router.get("/api/evals/last")
def evals_last() -> Dict[str, Any]:
    report = deps.evals.load_last_report()
    if not report:
        return {"ok": True, "exists": False}
    return {"ok": True, "exists": True, "report": report}


@router.get("/api/evals/job")
def evals_job() -> Dict[str, Any]:
    return {"ok": True, "job": deps.evals.get_job()}


def _evals_background_task(
    case_id: Optional[str],
    use_mock: bool,
    with_agent: bool,
    with_presets: bool,
    quant_only: bool,
) -> None:
    try:
        deps.evals.run_and_finalize_job(
            case_id=case_id,
            use_mock=use_mock,
            with_agent=with_agent,
            with_presets=with_presets,
            quant_only=quant_only,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in evals.py", exc_info=True)
        pass


@router.post("/api/evals/run")
def evals_run(body: EvalRunRequest, background_tasks: BackgroundTasks) -> Any:
    if body.with_agent:
        llm = LLMClient()
        if not llm.api_key:
            raise HTTPException(status_code=503, detail="未配置 DASHSCOPE_API_KEY，无法跑 Agent 对照")
        if not llm.is_available():
            raise HTTPException(
                status_code=503,
                detail=llm.get_last_error() or "大模型不可用",
            )

    use_bg = body.background or (body.with_agent and not body.case_id)
    if use_bg:
        started = deps.evals.start_background_run(
            case_id=body.case_id,
            use_mock=body.use_mock,
            with_agent=body.with_agent,
            with_presets=body.with_presets,
            quant_only=body.quant_only,
        )
        if not started.get("ok"):
            raise HTTPException(status_code=409, detail=started.get("error") or "任务冲突")
        background_tasks.add_task(
            _evals_background_task,
            body.case_id,
            body.use_mock,
            body.with_agent,
            body.with_presets,
            body.quant_only,
        )
        return {
            "ok": True,
            "background": True,
            "status": "running",
            "message": "校验已在后台运行，请轮询 /api/evals/job",
        }

    try:
        report = deps.evals.run(
            case_id=body.case_id,
            use_mock=body.use_mock,
            with_agent=body.with_agent,
            with_presets=body.with_presets,
            quant_only=body.quant_only,
            save=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if report.get("error"):
        raise HTTPException(status_code=404, detail=report["error"])
    if not report.get("ok"):
        return JSONResponse(status_code=422, content=report)
    return report
