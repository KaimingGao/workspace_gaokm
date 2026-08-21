"""量化研究台 API — score review/ledger。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    ScoreCalibrationFitRequest,
    ScoreCalibrationPersistRequest,
    ScoreLedgerDeleteRequest,
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreReviewRequest,
)

router = APIRouter(tags=["quant"])


@router.get("/api/quant/score-review/dates")
def quant_score_review_dates(limit: int = 30) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
    """跨决策日方向命中率序列（sparkline）。"""
    try:
        return deps.quant.score_review_hit_series(
            horizon_days=horizon_days, limit=limit, autofill=autofill
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
    """复盘单票三面板：收盘价 / 日涨跌% / 冻结 ŷ%（默认近 10 日）。"""
    try:
        out = deps.quant.score_ledger_stock_panel(code, lookback=lookback)
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
) -> Dict[str, Any]:
    """昨日复盘：ŷ 方向 vs 前瞻收益。"""
    try:
        return deps.quant.build_score_review(
            as_of=as_of,
            horizon_days=horizon_days,
            autofill=autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/score-review/tau-shadow")
def quant_score_review_tau_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """A2：ŷ_τ 影子簿验收（IC / 命中 / vs EOD 重叠）。"""
    try:
        return deps.quant.build_tau_shadow_review(
            as_of=as_of,
            horizon_days=horizon_days,
            autofill=autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/score-review/nowcast-shadow")
def quant_score_review_nowcast_shadow(
    as_of: Optional[str] = None,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """N3：ŷ_nowcast 影子簿验收（IC / 命中 / Nordhaus / vs EOD）。"""
    try:
        return deps.quant.build_nowcast_shadow_review(
            as_of=as_of,
            horizon_days=horizon_days,
            autofill=autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-review")
def quant_score_review_post(body: ScoreReviewRequest) -> Dict[str, Any]:
    try:
        return deps.quant.build_score_review(
            as_of=body.as_of,
            horizon_days=body.horizon_days,
            autofill=body.autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-ledger/freeze")
def quant_score_ledger_freeze(body: ScoreLedgerFreezeRequest) -> Dict[str, Any]:
    """从当前集群书冻结打分账本。"""
    try:
        out = deps.quant.freeze_score_ledger(as_of=body.as_of)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "冻结失败")
    return out


@router.post("/api/quant/score-ledger/delete")
def quant_score_ledger_delete(body: ScoreLedgerDeleteRequest) -> Dict[str, Any]:
    """删除指定日（或批量）冻结账本与 outcomes。"""
    try:
        out = deps.quant.delete_score_ledger(
            as_of=body.as_of,
            dates=body.dates,
            include_outcomes=body.include_outcomes,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "删除失败")
    return out


@router.post("/api/quant/score-outcomes/fill")
def quant_score_outcomes_fill(body: ScoreOutcomesFillRequest) -> Dict[str, Any]:
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


@router.post("/api/quant/score-calibration/fit")
def quant_score_calibration_fit(body: ScoreCalibrationFitRequest) -> Dict[str, Any]:
    """拟合单调 g(ŷ)；EOD 默认分组同源 panel，不足回退账本。不自动写盘。"""
    try:
        return deps.quant.fit_score_calibration(
            lookback_dates=body.lookback_dates,
            train_frac=body.train_frac,
            sample_source=body.sample_source,
            lookback_bars=body.lookback_bars,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-calibration/persist")
def quant_score_calibration_persist(body: ScoreCalibrationPersistRequest) -> Dict[str, Any]:
    """人审写入 live/score_calibration.json。"""
    try:
        out = deps.quant.persist_score_calibration(
            note=body.note, enable=body.enable
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.get("/api/quant/score-calibration/model")
def quant_score_calibration_model() -> Dict[str, Any]:
    """读取已 promote / 上次拟合的校准映射。"""
    try:
        return deps.quant.get_score_calibration_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
