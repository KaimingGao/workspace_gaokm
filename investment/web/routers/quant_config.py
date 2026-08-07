"""量化研究台 API — config/signal-config。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import ScoringFloorsRequest, SentimentPriorRequest

router = APIRouter(tags=["quant"])


@router.get("/api/quant/actions")
def quant_actions():
    """策略 · 历史/前瞻验证 · 对比 归属图（机器可读）。"""
    return deps.quant.action_map()


@router.get("/api/quant/config")
def quant_config():
    return deps.quant.config_summary()


@router.get("/api/quant/package")
def quant_package():
    return deps.quant.build_package_info()


@router.get("/api/signal/config")
def signal_config():
    """Canonical signal config read."""
    out = deps.quant.read_signal_config_file()
    return {**out, "canonical": True}


@router.get("/api/signal/config/file")
def signal_config_file():
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
def signal_config_scoring_floors(body: ScoringFloorsRequest):
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


@router.get("/api/signal/config/sentiment-prior")
def signal_config_sentiment_prior_get():
    """舆情先验配置只读。"""
    try:
        return deps.quant.read_sentiment_prior()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/signal/config/sentiment-prior")
def signal_config_sentiment_prior_save(body: SentimentPriorRequest):
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


@router.get("/api/signal/config/diff-preview")
def signal_config_diff_preview(code: str = "茅台", use_saved: bool = True):
    try:
        return deps.quant.build_config_diff_preview(code=code, use_saved=use_saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/signal/config/diff-export")
def signal_config_diff_export(code: str = "茅台", use_saved: bool = True):
    try:
        result = deps.quant.export_config_diff_bundle(code=code, use_saved=use_saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "无 diff 可导出")
    return result
