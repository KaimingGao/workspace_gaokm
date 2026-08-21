"""量化研究台 API — config/signal-config。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    DualScoreRequest,
    MarketPriorRequest,
    ScoringFloorsRequest,
    SentimentPriorRequest,
    StanceThresholdsRequest,
)

router = APIRouter(tags=["quant"])


@router.get("/api/quant/actions")
def quant_actions() -> Dict[str, Any]:
    """策略 · 历史/前瞻验证 · 对比 归属图（机器可读）。"""
    return deps.quant.action_map()


@router.get("/api/quant/config")
def quant_config() -> Dict[str, Any]:
    return deps.quant.config_summary()


@router.get("/api/quant/package")
def quant_package() -> Dict[str, Any]:
    return deps.quant.build_package_info()


@router.get("/api/signal/config")
def signal_config() -> Dict[str, Any]:
    """Canonical signal config read."""
    out = deps.quant.read_signal_config_file()
    return {**out, "canonical": True}


@router.get("/api/signal/config/file")
def signal_config_file() -> Dict[str, Any]:
    """Legacy wrapper; prefer GET /api/signal/config (canonical)."""
    out = deps.quant.read_signal_config_file()
    return {
        "ok": True,
        "exists": out.get("exists"),
        "path": out.get("path"),
        "readonly": True,
        "config": out.get("config"),
        "note": out.get("note"),
        "deprecated": True,
        "canonical": "/api/signal/config",
    }


@router.post("/api/signal/config/scoring")
def signal_config_scoring_floors(body: ScoringFloorsRequest) -> Dict[str, Any]:
    """Y0：人审写入 ŷ 买卖门槛；永不改 weights。"""
    try:
        out = deps.quant.save_scoring_floors(
            min_predicted_score=body.min_predicted_score,
            min_hold_predicted_score=body.min_hold_predicted_score,
            note=body.note or "策略中心人审",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.post("/api/signal/config/stance")
def signal_config_stance_thresholds(body: StanceThresholdsRequest) -> Dict[str, Any]:
    """人审写入 stance_thresholds；永不改 weights / scoring。"""
    try:
        out = deps.quant.save_stance_thresholds(
            avoid=body.avoid,
            wait=body.wait,
            probe=body.probe,
            note=body.note or "研究枢纽人审·阈值",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.get("/api/signal/config/sentiment-prior")
def signal_config_sentiment_prior_get() -> Dict[str, Any]:
    """舆情先验配置只读。"""
    try:
        return deps.quant.read_sentiment_prior()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/signal/config/sentiment-prior")
def signal_config_sentiment_prior_save(body: SentimentPriorRequest) -> Dict[str, Any]:
    """人审写入 prior.mode 等；强制不进 ŷ；不改 weights。"""
    try:
        out = deps.quant.save_sentiment_prior(
            mode=body.mode,
            block_new_buys=body.block_new_buys,
            scale_buy_pct=body.scale_buy_pct,
            scale_holds=body.scale_holds,
            note=body.note or "策略中心人审·舆情先验",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.get("/api/signal/config/market-prior")
def signal_config_market_prior_get() -> Dict[str, Any]:
    """M 层 prior（cross_market）配置只读。"""
    try:
        return deps.quant.read_market_prior()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/signal/config/market-prior")
def signal_config_market_prior_save(body: MarketPriorRequest) -> Dict[str, Any]:
    """人审写入 cross_market.mode 等；强制不进 ŷ；不改 weights。"""
    try:
        out = deps.quant.save_market_prior(
            cross_market_mode=body.cross_market_mode,
            tech_drag_trigger_pct=body.tech_drag_trigger_pct,
            scale_buy_pct=body.scale_buy_pct,
            scale_holds=body.scale_holds,
            market_sentiment_mode=body.market_sentiment_mode,
            market_sentiment_scale_buy_pct=body.market_sentiment_scale_buy_pct,
            market_sentiment_scale_holds=body.market_sentiment_scale_holds,
            regulatory_mode=body.regulatory_mode,
            regulatory_scale_buy_pct=body.regulatory_scale_buy_pct,
            ipo_drain_mode=body.ipo_drain_mode,
            ipo_drain_scale_buy_pct=body.ipo_drain_scale_buy_pct,
            ipo_drain_ratio_high=body.ipo_drain_ratio_high,
            merge_mode=body.merge_mode,
            note=body.note or "策略中心人审·市场 prior",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.get("/api/signal/config/dual-score")
def signal_config_dual_score_get() -> Dict[str, Any]:
    """双层 ŷ fusion 只读。"""
    try:
        return deps.quant.read_dual_score()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/signal/config/dual-score")
def signal_config_dual_score_save(body: DualScoreRequest) -> Dict[str, Any]:
    """人审写入 dual_score.fusion_mode 等；不改 weights / scoring。"""
    try:
        out = deps.quant.save_dual_score(
            fusion_mode=body.fusion_mode,
            min_predicted_score_tau=body.min_predicted_score_tau,
            w_eod=body.w_eod,
            w_tau=body.w_tau,
            w_mode=body.w_mode,
            block_buy_if_tau_missing=body.block_buy_if_tau_missing,
            note=body.note or "策略中心人审·双层ŷ",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "写入失败")
    return out


@router.get("/api/signal/config/diff-preview")
def signal_config_diff_preview(code: str = "茅台", use_saved: bool = True) -> Dict[str, Any]:
    try:
        return deps.quant.build_config_diff_preview(code=code, use_saved=use_saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/signal/config/diff-export")
def signal_config_diff_export(code: str = "茅台", use_saved: bool = True) -> Dict[str, Any]:
    try:
        result = deps.quant.export_config_diff_bundle(code=code, use_saved=use_saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "无 diff 可导出")
    return result
