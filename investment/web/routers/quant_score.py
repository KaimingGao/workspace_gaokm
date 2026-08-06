"""量化研究台 API — score review/ledger。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreReviewRequest,
)

router = APIRouter(tags=["quant"])


@router.get("/api/quant/score-review/dates")
def quant_score_review_dates(limit: int = 30):
    """已冻结打分账本日期列表。"""
    try:
        return deps.quant.list_score_ledger_dates(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/score-review/hit-series")
def quant_score_review_hit_series(
    horizon_days: int = 3,
    limit: int = 20,
    autofill: bool = False,
):
    """跨决策日方向命中率序列（sparkline）。"""
    try:
        return deps.quant.score_review_hit_series(
            horizon_days=horizon_days, limit=limit, autofill=autofill
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/score-ledger/series")
def quant_score_ledger_series(code: str, limit: int = 40):
    """单票 ŷ 跨日时间线。"""
    try:
        out = deps.quant.score_ledger_code_series(code, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "查询失败")
    return out


@router.get("/api/quant/score-review")
def quant_score_review(
    as_of: Optional[str] = None,
    horizon_days: int = 3,
    autofill: bool = True,
):
    """昨日复盘：ŷ 方向 vs 前瞻收益。"""
    try:
        return deps.quant.build_score_review(
            as_of=as_of,
            horizon_days=horizon_days,
            autofill=autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-review")
def quant_score_review_post(body: ScoreReviewRequest):
    try:
        return deps.quant.build_score_review(
            as_of=body.as_of,
            horizon_days=body.horizon_days,
            autofill=body.autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-ledger/freeze")
def quant_score_ledger_freeze(body: ScoreLedgerFreezeRequest):
    """从当前集群书冻结打分账本。"""
    try:
        out = deps.quant.freeze_score_ledger(as_of=body.as_of)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "冻结失败")
    return out


@router.post("/api/quant/score-outcomes/fill")
def quant_score_outcomes_fill(body: ScoreOutcomesFillRequest):
    """回填 realized / sign_hit。"""
    try:
        out = deps.quant.fill_score_outcomes(
            as_of=body.as_of, horizon_days=body.horizon_days
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "回填失败")
    return out
