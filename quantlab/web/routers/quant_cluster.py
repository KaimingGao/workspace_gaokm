"""量化研究台 API — bars/minute 刷新与 L1/L2 物化。"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from core.watching.store import WATCHING_MAX_SIZE
from web import deps
from web.schemas import (
    BarsRefreshRequest,
    MaterializeRequest,
    MinuteRefreshRequest,
)

router = APIRouter(tags=["quant"])

@router.get("/api/quant/bars/status")

def quant_bars_status(watching_limit: int = WATCHING_MAX_SIZE) -> Dict[str, Any]:
    """观察池日线末 bar 覆盖（研究枢纽状态条）。"""
    try:
        return deps.quant.bars_status(watching_limit=watching_limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/bars/refresh")

def quant_bars_refresh(body: BarsRefreshRequest) -> Dict[str, Any]:
    """更新观察池日线；默认后台 Job（``GET /api/jobs/bars-refresh``）。

    ``mode=topup``：增量补齐；``mode=full``：整窗强更。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        mode=body.mode,
    )
    try:
        if body.sync:
            return deps.quant.run_bars_refresh(**kwargs)
        return deps.quant.start_bars_refresh_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/materialize/catalog")
def quant_materialize_catalog() -> Dict[str, Any]:
    """L1/L2 物化 family / clock 目录。"""
    try:
        return deps.quant.materialize_catalog()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/materialize")
def quant_materialize(body: MaterializeRequest) -> Dict[str, Any]:
    """独立物化特征（L1/L2）；默认后台 Job（``GET /api/jobs/materialize-l1l2``）。"""
    kwargs = dict(
        families=list(body.families or []),
        clocks=list(body.clocks or []),
        do_l1=bool(body.do_l1),
        do_l1_tau_grid=bool(body.do_l1_tau_grid),
        do_l2=bool(body.do_l2),
        lookback=body.lookback,
        watching_limit=body.watching_limit,
    )
    try:
        if body.sync:
            return deps.quant.run_materialize(**kwargs)
        return deps.quant.start_materialize_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/bars/integrity")

def quant_bars_integrity(
    watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
) -> Dict[str, Any]:
    """观察池日线逐日格子。只读本地仓。"""
    try:
        return deps.quant.bars_integrity(
            watching_limit=watching_limit, days=days
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/minute/integrity")

def quant_minute_integrity(
    watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
) -> Dict[str, Any]:
    """观察池 5 分钟逐日格子。只读本地仓。"""
    try:
        return deps.quant.minute_integrity(
            watching_limit=watching_limit, days=days
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/minute/integrity-day")

def quant_minute_integrity_day(code: str, date: str) -> Dict[str, Any]:
    """单票单日 48 根 5 分钟。只读本地仓。"""
    try:
        return deps.quant.minute_day_slots(code, date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/minute/status")

def quant_minute_status(
    watching_limit: int = WATCHING_MAX_SIZE,
    period: str = "5",
    min_span_days: int = 40,
    include_label_portrait: bool = False,
) -> Dict[str, Any]:
    """观察池 5m 分钟缓存覆盖（研究枢纽 UI）。

    默认不含标签画像；传 ``include_label_portrait=true`` 再算 τ(OC) 画像。
    """
    try:
        return deps.quant.minute_status(
            watching_limit=watching_limit,
            period=period,
            min_span_days=min_span_days,
            include_label_portrait=include_label_portrait,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/minute/refresh")

def quant_minute_refresh(body: MinuteRefreshRequest) -> Dict[str, Any]:
    """预热观察池 5m 分钟线；默认后台 Job（``GET /api/jobs/minute-refresh``）。

    ``mode=topup``：增量补齐（预演调仓日常用）；``mode=full``：强更全窗口。
    """
    kwargs = dict(
        watching_limit=body.watching_limit,
        period=body.period,
        mode=body.mode,
    )
    try:
        if body.sync:
            return deps.quant.run_minute_refresh(**kwargs)
        return deps.quant.start_minute_refresh_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
