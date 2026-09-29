"""量化研究台 API — bars/minute 刷新；分组 OLS / live 已退役（410）。"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from core.watching.store import WATCHING_MAX_SIZE
from web import deps
from web.schemas import (
    ClusterBarsRefreshRequest,
    ClusterMinuteRefreshRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quant"])

_CLUSTER_RETIRED_DETAIL = "cluster_retired"


def _cluster_retired() -> None:
    raise HTTPException(status_code=410, detail=_CLUSTER_RETIRED_DETAIL)


@router.get("/api/quant/cluster-bars/status")
def quant_cluster_bars_status(watching_limit: int = WATCHING_MAX_SIZE) -> Dict[str, Any]:
    """观察池日线末 bar 覆盖（研究枢纽状态条）。"""
    try:
        return deps.quant.cluster_bars_status(watching_limit=watching_limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-bars/refresh")
def quant_cluster_bars_refresh(body: ClusterBarsRefreshRequest) -> Dict[str, Any]:
    """更新观察池日线；默认后台 Job（``GET /api/jobs/cluster-bars-refresh``）。

    ``mode=topup``：增量补齐；``mode=full``：整窗强更。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        mode=body.mode,
    )
    try:
        if body.sync:
            return deps.quant.run_cluster_bars_refresh(**kwargs)
        return deps.quant.start_cluster_bars_refresh_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-bars/integrity")
def quant_cluster_bars_integrity(
    watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
) -> Dict[str, Any]:
    """观察池日线逐日格子。只读本地仓。"""
    try:
        return deps.quant.cluster_bars_integrity(
            watching_limit=watching_limit, days=days
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-minute/integrity")
def quant_cluster_minute_integrity(
    watching_limit: int = WATCHING_MAX_SIZE, days: int = 22
) -> Dict[str, Any]:
    """观察池 5 分钟逐日格子。只读本地仓。"""
    try:
        return deps.quant.cluster_minute_integrity(
            watching_limit=watching_limit, days=days
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-minute/integrity-day")
def quant_cluster_minute_integrity_day(code: str, date: str) -> Dict[str, Any]:
    """单票单日 48 根 5 分钟。只读本地仓。"""
    try:
        return deps.quant.cluster_minute_day_slots(code, date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-minute/status")
def quant_cluster_minute_status(
    watching_limit: int = WATCHING_MAX_SIZE,
    period: str = "5",
    min_span_days: int = 40,
    include_label_portrait: bool = False,
) -> Dict[str, Any]:
    """观察池 5m 分钟缓存覆盖（研究枢纽 UI）。

    默认不含标签画像；传 ``include_label_portrait=true`` 再算 τ/path 画像。
    """
    try:
        return deps.quant.cluster_minute_status(
            watching_limit=watching_limit,
            period=period,
            min_span_days=min_span_days,
            include_label_portrait=include_label_portrait,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-minute/refresh")
def quant_cluster_minute_refresh(body: ClusterMinuteRefreshRequest) -> Dict[str, Any]:
    """预热观察池 5m 分钟线；默认后台 Job（``GET /api/jobs/cluster-minute-refresh``）。

    ``mode=topup``：增量补齐（预演调仓日常用）；``mode=full``：强更全窗口。
    """
    kwargs = dict(
        watching_limit=body.watching_limit,
        period=body.period,
        lookback_days=body.lookback_days,
        mode=body.mode,
        topup_lookback_days=body.topup_lookback_days,
    )
    try:
        if body.sync:
            return deps.quant.run_cluster_minute_refresh(**kwargs)
        return deps.quant.start_cluster_minute_refresh_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-ols-clusters")
def quant_factor_ols_clusters() -> Dict[str, Any]:
    """分组 OLS 已退役。"""
    _cluster_retired()


@router.get("/api/quant/factor-ols-clusters/last-report")
def quant_factor_ols_clusters_last_report() -> Dict[str, Any]:
    """分组报告已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-paper-preview")
def quant_cluster_paper_preview() -> Dict[str, Any]:
    """分池簿纸面调仓已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-multi-score")
def quant_cluster_multi_score() -> Dict[str, Any]:
    """分组多权复打分已退役。"""
    _cluster_retired()


@router.get("/api/quant/cluster-live/status")
def quant_cluster_live_status(
    audit_rotate: bool = False,
    audit_offset: Optional[int] = None,
    light: bool = False,
    run_auto_demote: bool = False,
) -> Dict[str, Any]:
    """分组 live 状态已退役（软响应，避免轻量轮询硬 410）。"""
    _ = (audit_rotate, audit_offset, light, run_auto_demote)
    return deps.quant.cluster_live_status(
        audit_rotate=False,
        audit_offset=None,
        light=True,
        run_auto_demote=False,
    )


@router.post("/api/quant/cluster-live/apply")
def quant_cluster_live_apply() -> Dict[str, Any]:
    """分组 live 一键应用已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-live/promote")
def quant_cluster_live_promote() -> Dict[str, Any]:
    """分组 promote 已退役。"""
    _cluster_retired()


@router.get("/api/quant/cluster-live/promote-preflight")
def quant_cluster_promote_preflight(from_draft: bool = True) -> Dict[str, Any]:
    """分组 promote 预检已退役。"""
    _ = from_draft
    _cluster_retired()


@router.post("/api/quant/cluster-live/rollback")
def quant_cluster_live_rollback() -> Dict[str, Any]:
    """分组 live 回滚已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-live/mode")
def quant_cluster_live_mode() -> Dict[str, Any]:
    """分组 live mode 已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-live/universe-fit-tiers")
def quant_cluster_live_universe_fit_tiers() -> Dict[str, Any]:
    """宇宙拟合档设置已退役。"""
    _cluster_retired()


@router.get("/api/quant/cluster-live/fit-tiers")
def quant_cluster_live_fit_tiers() -> Dict[str, Any]:
    """拟合档查询已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-live/refresh-book")
def quant_cluster_live_refresh_book() -> Dict[str, Any]:
    """分池簿刷新已退役。"""
    _cluster_retired()


@router.post("/api/quant/cluster-live/rank")
def quant_cluster_live_rank() -> Dict[str, Any]:
    """分池排序已退役。"""
    _cluster_retired()
