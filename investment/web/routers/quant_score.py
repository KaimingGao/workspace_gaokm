"""量化研究台 API — score review/ledger。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    ScoreLedgerDeleteRequest,
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreReviewRequest,
)

router = APIRouter(tags=["quant"])

_REVIEW_OFFLINE = {
    "success": False,
    "deprecated": True,
    "error": "昨日复盘 HTTP 已下线；账本 IO / ŷ 序列仍可用",
}


def _review_offline(**extra: Any) -> Dict[str, Any]:
    return {**_REVIEW_OFFLINE, **extra}


@router.get("/api/quant/score-review/dates")
def quant_score_review_dates(limit: int = 30) -> Dict[str, Any]:
    """昨日复盘 UI 已下线。"""
    _ = limit
    return _review_offline()


@router.get("/api/quant/score-review/hit-series")
def quant_score_review_hit_series(
    horizon_days: int = 3,
    limit: int = 20,
    autofill: bool = False,
) -> Dict[str, Any]:
    """昨日复盘 UI 已下线。"""
    _ = horizon_days, limit, autofill
    return _review_offline()


@router.get("/api/quant/score-ledger/series")
def quant_score_ledger_series(code: str, limit: int = 40) -> Dict[str, Any]:
    """单票 ŷ 跨日时间线。"""
    try:
        out = deps.quant.score_ledger_code_series(code, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "查询失败")
    return out


@router.get("/api/quant/score-ledger/stock-panel")
def quant_score_ledger_stock_panel(code: str, lookback: int = 10) -> Dict[str, Any]:
    """昨日复盘 UI 已下线。"""
    _ = code, lookback
    return _review_offline()


@router.get("/api/quant/score-review")
def quant_score_review(
    as_of: Optional[str] = None,
    horizon_days: int = 3,
    autofill: bool = True,
) -> Dict[str, Any]:
    """昨日复盘 UI 已下线。"""
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.get("/api/quant/score-review/tau-shadow")
def quant_score_review_tau_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """ŷ_τ 单日验收 UI 已下线。"""
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.get("/api/quant/score-review/nowcast-shadow")
def quant_score_review_nowcast_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """ŷ_nowcast 单日验收 UI 已下线。"""
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.post("/api/quant/score-review")
def quant_score_review_post(body: ScoreReviewRequest) -> Dict[str, Any]:
    """昨日复盘 UI 已下线。"""
    _ = body
    return _review_offline()


@router.post("/api/quant/score-ledger/freeze")
def quant_score_ledger_freeze(body: ScoreLedgerFreezeRequest) -> Dict[str, Any]:
    """分池簿冻结已停用（不再 404）。"""
    _ = body
    try:
        return deps.quant.freeze_score_ledger()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-ledger/delete")
def quant_score_ledger_delete(body: ScoreLedgerDeleteRequest) -> Dict[str, Any]:
    """昨日复盘 UI 已下线；不经 HTTP 删账本。"""
    _ = body
    return _review_offline()


@router.post("/api/quant/score-outcomes/fill")
def quant_score_outcomes_fill(body: ScoreOutcomesFillRequest) -> Dict[str, Any]:
    """昨日复盘 UI 已下线；日更仍走 fill_outcomes。"""
    _ = body
    return _review_offline()
