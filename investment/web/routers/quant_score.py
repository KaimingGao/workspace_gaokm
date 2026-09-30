"""量化研究台 API — score review/ledger（已下线，统一 stub）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter

from web.schemas import (
    ScoreLedgerDeleteRequest,
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreReviewRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quant"])

_REVIEW_OFFLINE = {
    "success": False,
    "deprecated": True,
    "error": "score_ledger / 昨日复盘已下线（分档改 ŷ_oo Holdout OOS）",
}


def _review_offline(**extra: Any) -> Dict[str, Any]:
    return {**_REVIEW_OFFLINE, **extra}


@router.get("/api/quant/score-review/dates")
def quant_score_review_dates(limit: int = 30) -> Dict[str, Any]:
    _ = limit
    return _review_offline()


@router.get("/api/quant/score-review/hit-series")
def quant_score_review_hit_series(
    horizon_days: int = 3,
    limit: int = 20,
    autofill: bool = False,
) -> Dict[str, Any]:
    _ = horizon_days, limit, autofill
    return _review_offline()


@router.get("/api/quant/score-ledger/series")
def quant_score_ledger_series(code: str, limit: int = 40) -> Dict[str, Any]:
    """单票 ŷ 时间线已下线。"""
    _ = code, limit
    return _review_offline(points=[], n=0)


@router.get("/api/quant/score-ledger/stock-panel")
def quant_score_ledger_stock_panel(code: str, lookback: int = 10) -> Dict[str, Any]:
    _ = code, lookback
    return _review_offline()


@router.get("/api/quant/score-review")
def quant_score_review(
    as_of: Optional[str] = None,
    horizon_days: int = 3,
    autofill: bool = True,
) -> Dict[str, Any]:
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.get("/api/quant/score-review/tau-shadow")
def quant_score_review_tau_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.get("/api/quant/score-review/nowcast-shadow")
def quant_score_review_nowcast_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    _ = as_of, horizon_days, autofill
    return _review_offline()


@router.post("/api/quant/score-review")
def quant_score_review_post(body: ScoreReviewRequest) -> Dict[str, Any]:
    _ = body
    return _review_offline()


@router.post("/api/quant/score-ledger/freeze")
def quant_score_ledger_freeze(body: ScoreLedgerFreezeRequest) -> Dict[str, Any]:
    _ = body
    return _review_offline(n_rows=0)


@router.post("/api/quant/score-ledger/delete")
def quant_score_ledger_delete(body: ScoreLedgerDeleteRequest) -> Dict[str, Any]:
    _ = body
    return _review_offline()


@router.post("/api/quant/score-outcomes/fill")
def quant_score_outcomes_fill(body: ScoreOutcomesFillRequest) -> Dict[str, Any]:
    _ = body
    return _review_offline()
