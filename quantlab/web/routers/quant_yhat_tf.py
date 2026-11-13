"""模型中心 API（/yhat-tf）。四头 Holdout 研究轨；不写生产 score。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.research.yhat_tf_desk import (
    clear_slot,
    desk_status,
    promote_draft_to_research,
    save_fit_draft,
    start_holdout_fit,
)

router = APIRouter(tags=["quant-yhat-tf"])


class YhatTfFitBody(BaseModel):
    head: str = Field(..., description="oo | tc | oo_rank | tc_rank")
    params: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class YhatTfHeadBody(BaseModel):
    head: str
    params: Optional[Dict[str, Any]] = None


class YhatTfClearBody(BaseModel):
    head: str
    slot: str = Field(..., description="draft | research | last")


@router.get("/api/quant/yhat-tf/status")
def yhat_tf_status() -> Dict[str, Any]:
    try:
        return desk_status()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/yhat-tf/draft")
def yhat_tf_save_draft(body: YhatTfFitBody) -> Dict[str, Any]:
    try:
        return save_fit_draft(body.head, body.params or {}, note=body.note or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/yhat-tf/fit")
def yhat_tf_fit(body: YhatTfFitBody) -> Dict[str, Any]:
    """按 Holdout 参数后台启动拟合（研究轨；不写 live）。"""
    try:
        out = start_holdout_fit(body.head, body.params or {})
        if not out.get("success"):
            raise HTTPException(status_code=400, detail=out.get("error") or "拟合启动失败")
        return out
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/yhat-tf/promote-research")
def yhat_tf_promote_research(body: YhatTfHeadBody) -> Dict[str, Any]:
    try:
        out = promote_draft_to_research(body.head)
        if not out.get("success"):
            raise HTTPException(status_code=400, detail=out.get("error") or "晋级失败")
        return out
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/yhat-tf/clear")
def yhat_tf_clear(body: YhatTfClearBody) -> Dict[str, Any]:
    try:
        return clear_slot(body.head, body.slot)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
