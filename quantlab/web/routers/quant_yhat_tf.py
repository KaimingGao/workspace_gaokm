"""模型工厂 API：/yhat-l（Arm·L）与 /yhat-t（Arm·T）；旧 /yhat-tf 别名落 T。"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.research.yhat_factory_versions import (
    delete_factory_version,
    list_factory_versions,
    preview_factory_version,
)
from core.research.yhat_tf_desk import desk_status, start_holdout_fit

router = APIRouter(tags=["quant-yhat-factory"])

Factory = Literal["L", "T"]


class YhatFactoryFitBody(BaseModel):
    head: str = Field(..., description="oo | tc | oo_rank | tc_rank")
    params: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


def _status(factory: Factory) -> Dict[str, Any]:
    try:
        return desk_status(factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


def _fit(factory: Factory, body: YhatFactoryFitBody) -> Dict[str, Any]:
    try:
        out = start_holdout_fit(body.head, body.params or {}, factory=factory)
        if not out.get("success"):
            raise HTTPException(status_code=400, detail=out.get("error") or "拟合启动失败")
        return out
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


def _versions(factory: Factory, head: str) -> Dict[str, Any]:
    try:
        out = list_factory_versions(head, factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "无法列出版本")
    return out


def _delete(factory: Factory, head: str, run_id: str) -> Dict[str, Any]:
    try:
        out = delete_factory_version(head, run_id, factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        code = 400 if out.get("error") == "invalid run_id" else 404
        raise HTTPException(status_code=code, detail=out.get("error") or "版本不存在")
    return out


def _preview(factory: Factory, head: str, run_id: str) -> Dict[str, Any]:
    try:
        out = preview_factory_version(head, run_id, factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        code = 400 if out.get("error") == "invalid run_id" else 404
        raise HTTPException(status_code=code, detail=out.get("error") or "版本不存在")
    return out


@router.get("/api/quant/yhat-l/status")
def yhat_l_status() -> Dict[str, Any]:
    return _status("L")


@router.post("/api/quant/yhat-l/fit")
def yhat_l_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("L", body)


@router.get("/api/quant/yhat-l/versions")
def yhat_l_versions(head: str) -> Dict[str, Any]:
    return _versions("L", head)


@router.get("/api/quant/yhat-l/versions/preview")
def yhat_l_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("L", head, run_id)


@router.delete("/api/quant/yhat-l/versions")
def yhat_l_versions_delete(head: str, run_id: str) -> Dict[str, Any]:
    return _delete("L", head, run_id)


@router.get("/api/quant/yhat-t/status")
def yhat_t_status() -> Dict[str, Any]:
    return _status("T")


@router.post("/api/quant/yhat-t/fit")
def yhat_t_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("T", body)


@router.get("/api/quant/yhat-t/versions")
def yhat_t_versions(head: str) -> Dict[str, Any]:
    return _versions("T", head)


@router.get("/api/quant/yhat-t/versions/preview")
def yhat_t_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("T", head, run_id)


@router.delete("/api/quant/yhat-t/versions")
def yhat_t_versions_delete(head: str, run_id: str) -> Dict[str, Any]:
    return _delete("T", head, run_id)


# 旧路径兼容 → Arm·T
@router.get("/api/quant/yhat-tf/status")
def yhat_tf_status() -> Dict[str, Any]:
    return _status("T")


@router.post("/api/quant/yhat-tf/fit")
def yhat_tf_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("T", body)


@router.get("/api/quant/yhat-tf/versions")
def yhat_tf_versions(head: str) -> Dict[str, Any]:
    return _versions("T", head)


@router.get("/api/quant/yhat-tf/versions/preview")
def yhat_tf_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("T", head, run_id)


@router.delete("/api/quant/yhat-tf/versions")
def yhat_tf_versions_delete(head: str, run_id: str) -> Dict[str, Any]:
    return _delete("T", head, run_id)
