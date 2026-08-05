"""D1–D6 平台 API：jobs / memory / decisions / feedback / schedule / order prefill。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web import deps

router = APIRouter(tags=["platform"])


class MemoryBody(BaseModel):
    preferences: Dict[str, Any] = Field(default_factory=dict)


class FeedbackBody(BaseModel):
    paper_metrics: Optional[Dict[str, Any]] = None
    backtest_metrics: Optional[Dict[str, Any]] = None
    monitor_alerts: Optional[List[Any]] = None


class ScheduleBody(BaseModel):
    kind: str = Field(
        ...,
        description=(
            "watch_alert | daily_review | sentiment_scan | bars_warmup | "
            "spot_refresh | fundamentals_warmup | paper_daily"
        ),
    )
    codes: Optional[List[str]] = None
    limit: int = 5
    simulate_buy: bool = False
    strategy: str = "short_conservative"
    force: bool = False
    # fundamentals_warmup
    ingest_history: Optional[bool] = True
    ingest_max_points: int = Field(default=8, ge=1, le=24)


class RecordAdviceBody(BaseModel):
    advice: Dict[str, Any]
    source: str = "api"
    session_id: str = ""
    persist: bool = True


class PrefillQuery(BaseModel):
    limit: int = 10
    fmt: str = "json"


@router.get("/api/jobs")
def list_jobs():
    return deps.platform.list_jobs()


@router.get("/api/jobs/{name}")
def get_job(name: str):
    out = deps.platform.get_job(name)
    return {**out, "canonical": True}


@router.post("/api/jobs/{name}/cancel")
def cancel_job(name: str):
    """协作取消运行中任务（worker 轮询 cancel_requested）。"""
    from core.job_progress import job_registry

    slot = job_registry.slot(name)
    ok = slot.request_cancel()
    return {
        "ok": ok,
        "cancelled": ok,
        "job": slot.get(),
        "note": "已请求取消" if ok else "当前无运行中任务",
    }

@router.get("/api/memory")
def get_memory():
    return deps.platform.get_memory()


@router.get("/api/prefs")
def get_prefs():
    """研究/回测默认偏好（已钳制 horizon 等）。"""
    return deps.platform.get_effective_prefs()


@router.put("/api/memory")
def put_memory(body: MemoryBody):
    return deps.platform.save_memory(body.preferences)


@router.get("/api/decisions")
def get_decisions(limit: int = 50):
    return deps.platform.list_decisions(limit=limit)


@router.post("/api/decisions/record")
def post_decision(body: RecordAdviceBody):
    out = deps.platform.record_advice(
        body.advice,
        source=body.source,
        session_id=body.session_id,
        persist=body.persist,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error") or "record failed")
    return out


@router.post("/api/feedback/suggest")
def post_feedback(body: FeedbackBody):
    return deps.platform.suggest_feedback(
        paper_metrics=body.paper_metrics,
        backtest_metrics=body.backtest_metrics,
        monitor_alerts=body.monitor_alerts,
    )


@router.post("/api/schedule/run")
def post_schedule(body: ScheduleBody):
    out = deps.platform.run_schedule(
        body.kind,
        codes=body.codes,
        limit=body.limit,
        simulate_buy=body.simulate_buy,
        strategy=body.strategy,
        force=body.force,
        ingest_history=body.ingest_history,
        ingest_max_points=body.ingest_max_points,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error") or "schedule failed")
    return out


@router.get("/api/schedule/job")
def schedule_job():
    out = deps.platform.get_job("schedule")
    return {**out, "deprecated": True, "canonical": "/api/jobs/schedule"}


@router.get("/api/schedule/last")
def schedule_last():
    return deps.platform.get_schedule_last()


@router.get("/api/audit/timeline")
def audit_timeline(limit: int = 40):
    """W2.5 · 决策 / 调度 / 告警 / promote 只读时间线。"""
    from web.audit_timeline import build_audit_timeline

    return build_audit_timeline(limit=limit)


@router.get("/api/north-star")
def north_star(refresh: bool = True):
    """R0 · 产品北极星二级指标（纸面夏普/卡玛 · 拟合 · TTM · 拦截流水）。"""
    out = deps.platform.get_north_star(refresh=refresh)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error") or "north-star failed")
    return out


@router.get("/api/ops/sample-status")
def ops_sample_status():
    """样本运营覆盖（TTM / 财务 history / 纸面快照 / 拦截标注）。"""
    return deps.platform.get_sample_status()


@router.get("/api/ops/empty-fundamentals")
def ops_empty_fundamentals():
    """V0.5 · 空财务码列表（补拉或移出验证宇宙）。"""
    return deps.platform.get_empty_fundamentals()


@router.get("/api/ops/data-quality")
def ops_data_quality():
    """D4 · 数据质量中心（覆盖率/财务多期/源审计/日历）。"""
    return deps.platform.get_data_quality()


@router.get("/api/ops/source-audit")
def ops_source_audit(lookback: int = 40):
    """D1 · live/回测源一致性审计。"""
    return deps.platform.get_source_audit(lookback=lookback)


@router.get("/api/ops/maturity-gate")
def ops_maturity_gate():
    """V5 · 策略验证成熟闸门只读评估。"""
    return deps.platform.get_maturity_gate()


@router.post("/api/ops/validation-pack")
def ops_validation_pack(body: dict = None):
    """V4.1 · 导出策略验证包（JSON + markdown）。"""
    body = body or {}
    return deps.platform.export_validation_pack(
        backtest_result=(body or {}).get("result") or (body or {}).get("backtest"),
        note=str((body or {}).get("note") or ""),
    )


@router.post("/api/ops/fit-gap")
def ops_fit_gap(body: dict = None):
    """V1.3 · 回测–纸面拟合落差启发式归因。"""
    body = body or {}
    return deps.platform.get_fit_gap(
        backtest_result=(body or {}).get("result") or (body or {}).get("backtest")
    )


@router.get("/api/alerts/last")
def alerts_last():
    """最近一次出站告警（页内铃铛 / Notification）。"""
    from web.audit_timeline import read_alerts_last

    return read_alerts_last()


@router.get("/api/orders/prefill")
def orders_prefill(limit: int = 10, fmt: str = "json"):
    if fmt not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="fmt must be json|csv")
    return deps.platform.order_prefill(limit=limit, fmt=fmt)
