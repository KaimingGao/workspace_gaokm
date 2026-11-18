"""模型工厂 API：/yhat-l（Arm·L）、/yhat-t（Arm·T）、/yhat-r（Arm·R）；旧 /yhat-tf 别名落 T。"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.research.yhat_factory_backtest import (
    delete_factory_backtest,
    factory_backtest_status,
    save_factory_rules,
    start_factory_backtest,
)
from core.research.yhat_factory_versions import (
    delete_factory_version,
    enable_factory_version,
    list_factory_versions,
    preview_factory_version,
)
from core.research.yhat_tf_desk import desk_status, start_holdout_fit

router = APIRouter(tags=["quant-yhat-factory"])

Factory = Literal["L", "T", "R"]


class YhatFactoryFitBody(BaseModel):
    head: str = Field(..., description="oo | tc | oo_rank | tc_rank")
    params: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class YhatFactoryBacktestBody(BaseModel):
    versions: Dict[str, str] = Field(default_factory=dict)
    rules: Optional[Dict[str, Any]] = None


class YhatFactoryRulesBody(BaseModel):
    rules: Dict[str, Any] = Field(default_factory=dict)


class YhatFactoryEnableBody(BaseModel):
    head: str
    run_id: str
    suite: str = "research"


def _status(factory: Factory) -> Dict[str, Any]:
    try:
        return desk_status(factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


def _fit(factory: Factory, body: YhatFactoryFitBody) -> Dict[str, Any]:
    try:
        params = dict(body.params or {})
        note = str(body.note or "").strip()
        suite = ""
        # 研究枢纽「拟合执行」进执行套，不进研究套。表单里的 Holdout 只给「拟合研究」。
        if note == "hub arm-t live":
            params["holdout_trading_days"] = 0
            suite = "live"
        elif note == "hub arm-t research":
            suite = "research"
        out = start_holdout_fit(body.head, params, factory=factory, suite=suite or None)
        if not out.get("success"):
            raise HTTPException(status_code=400, detail=out.get("error") or "拟合启动失败")
        return out
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


def _enable(factory: Factory, body: YhatFactoryEnableBody) -> Dict[str, Any]:
    try:
        out = enable_factory_version(
            body.head,
            body.run_id,
            suite=body.suite,
            factory=factory,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "启用失败")
    return out


def _versions(factory: Factory, head: str, suite: Optional[str] = None) -> Dict[str, Any]:
    try:
        out = list_factory_versions(head, factory=factory, suite=suite)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "无法列出版本")
    return out


def _delete(
    factory: Factory,
    head: str,
    run_id: str,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        out = delete_factory_version(head, run_id, factory=factory)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        code = 400 if out.get("error") == "invalid run_id" else 404
        raise HTTPException(status_code=code, detail=out.get("error") or "版本不存在")
    if factory == "T":
        listed = list_factory_versions(head, factory="T", suite=suite or "research")
        if listed.get("success"):
            out["items"] = listed["items"]
    return out


def _backtest_get(factory: Factory, run: str = "") -> Dict[str, Any]:
    try:
        return factory_backtest_status(factory, run_id=run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


def _backtest_rules(factory: Factory, body: YhatFactoryRulesBody) -> Dict[str, Any]:
    try:
        rules = save_factory_rules(factory, body.rules or {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "ok": True, "rules": rules}


def _backtest_delete(factory: Factory, run_id: str) -> Dict[str, Any]:
    try:
        out = delete_factory_backtest(factory, run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        err = str(out.get("error") or "记录不存在")
        if err == "删除失败":
            code = 500
        elif err in ("invalid run_id", "进行中的回测不能删除"):
            code = 400
        else:
            code = 404
        raise HTTPException(status_code=code, detail=err)
    return out


def _backtest_start(factory: Factory, body: YhatFactoryBacktestBody) -> Dict[str, Any]:
    try:
        out = start_factory_backtest(factory, body.versions or {}, body.rules)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=400, detail=out.get("error") or "离线回测未能启动")
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


@router.get("/api/quant/yhat-l/backtest")
def yhat_l_backtest_status(
    run: str = Query("", description="回看某次离线回测的 console"),
) -> Dict[str, Any]:
    return _backtest_get("L", run)


@router.delete("/api/quant/yhat-l/backtest")
def yhat_l_backtest_delete(run_id: str) -> Dict[str, Any]:
    return _backtest_delete("L", run_id)


@router.post("/api/quant/yhat-l/backtest")
def yhat_l_backtest_start(body: YhatFactoryBacktestBody) -> Dict[str, Any]:
    return _backtest_start("L", body)


@router.put("/api/quant/yhat-l/backtest/rules")
def yhat_l_backtest_rules(body: YhatFactoryRulesBody) -> Dict[str, Any]:
    return _backtest_rules("L", body)


@router.get("/api/quant/yhat-t/status")
def yhat_t_status() -> Dict[str, Any]:
    return _status("T")


@router.post("/api/quant/yhat-t/fit")
def yhat_t_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("T", body)


@router.get("/api/quant/yhat-t/versions")
def yhat_t_versions(
    head: str,
    suite: str = Query("research", description="research=与模型中心同一列表；live=仅 Holdout 0"),
) -> Dict[str, Any]:
    return _versions("T", head, suite=suite)


@router.get("/api/quant/yhat-t/versions/preview")
def yhat_t_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("T", head, run_id)


@router.post("/api/quant/yhat-t/versions/enable")
def yhat_t_versions_enable(body: YhatFactoryEnableBody) -> Dict[str, Any]:
    return _enable("T", body)


@router.delete("/api/quant/yhat-t/versions")
def yhat_t_versions_delete(
    head: str,
    run_id: str,
    suite: str = Query("research", description="research | live"),
) -> Dict[str, Any]:
    return _delete("T", head, run_id, suite=suite)


@router.get("/api/quant/yhat-t/backtest")
def yhat_t_backtest_status(
    run: str = Query("", description="回看某次离线回测的 console"),
) -> Dict[str, Any]:
    return _backtest_get("T", run)


@router.delete("/api/quant/yhat-t/backtest")
def yhat_t_backtest_delete(run_id: str) -> Dict[str, Any]:
    return _backtest_delete("T", run_id)


@router.post("/api/quant/yhat-t/backtest")
def yhat_t_backtest_start(body: YhatFactoryBacktestBody) -> Dict[str, Any]:
    return _backtest_start("T", body)


@router.put("/api/quant/yhat-t/backtest/rules")
def yhat_t_backtest_rules(body: YhatFactoryRulesBody) -> Dict[str, Any]:
    return _backtest_rules("T", body)


@router.get("/api/quant/yhat-r/status")
def yhat_r_status() -> Dict[str, Any]:
    return _status("R")


@router.post("/api/quant/yhat-r/fit")
def yhat_r_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("R", body)


@router.get("/api/quant/yhat-r/versions")
def yhat_r_versions(head: str) -> Dict[str, Any]:
    return _versions("R", head)


@router.get("/api/quant/yhat-r/versions/preview")
def yhat_r_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("R", head, run_id)


@router.delete("/api/quant/yhat-r/versions")
def yhat_r_versions_delete(head: str, run_id: str) -> Dict[str, Any]:
    return _delete("R", head, run_id)


@router.get("/api/quant/yhat-r/backtest")
def yhat_r_backtest_status(
    run: str = Query("", description="回看某次离线回测的 console"),
) -> Dict[str, Any]:
    return _backtest_get("R", run)


@router.delete("/api/quant/yhat-r/backtest")
def yhat_r_backtest_delete(run_id: str) -> Dict[str, Any]:
    return _backtest_delete("R", run_id)


@router.post("/api/quant/yhat-r/backtest")
def yhat_r_backtest_start(body: YhatFactoryBacktestBody) -> Dict[str, Any]:
    return _backtest_start("R", body)


@router.put("/api/quant/yhat-r/backtest/rules")
def yhat_r_backtest_rules(body: YhatFactoryRulesBody) -> Dict[str, Any]:
    return _backtest_rules("R", body)


# 旧路径兼容 → Arm·T
@router.get("/api/quant/yhat-tf/status")
def yhat_tf_status() -> Dict[str, Any]:
    return _status("T")


@router.post("/api/quant/yhat-tf/fit")
def yhat_tf_fit(body: YhatFactoryFitBody) -> Dict[str, Any]:
    return _fit("T", body)


@router.get("/api/quant/yhat-tf/versions")
def yhat_tf_versions(
    head: str,
    suite: str = Query("research", description="research=与模型中心同一列表；live=仅 Holdout 0"),
) -> Dict[str, Any]:
    return _versions("T", head, suite=suite)


@router.get("/api/quant/yhat-tf/versions/preview")
def yhat_tf_versions_preview(head: str, run_id: str) -> Dict[str, Any]:
    return _preview("T", head, run_id)


@router.delete("/api/quant/yhat-tf/versions")
def yhat_tf_versions_delete(
    head: str,
    run_id: str,
    suite: str = Query("research", description="research | live"),
) -> Dict[str, Any]:
    return _delete("T", head, run_id, suite=suite)


@router.get("/api/quant/yhat-tf/backtest")
def yhat_tf_backtest_status(
    run: str = Query("", description="回看某次离线回测的 console"),
) -> Dict[str, Any]:
    return _backtest_get("T", run)


@router.delete("/api/quant/yhat-tf/backtest")
def yhat_tf_backtest_delete(run_id: str) -> Dict[str, Any]:
    return _backtest_delete("T", run_id)


@router.post("/api/quant/yhat-tf/backtest")
def yhat_tf_backtest_start(body: YhatFactoryBacktestBody) -> Dict[str, Any]:
    return _backtest_start("T", body)


@router.put("/api/quant/yhat-tf/backtest/rules")
def yhat_tf_backtest_rules(body: YhatFactoryRulesBody) -> Dict[str, Any]:
    return _backtest_rules("T", body)
