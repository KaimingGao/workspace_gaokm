"""量化研究台 API — cluster live management。"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    ClusterApplyShortcutRequest,
    ClusterBarsRefreshRequest,
    ClusterMinuteRefreshRequest,
    ClusterModeRequest,
    ClusterMultiScoreRequest,
    ClusterPaperPreviewRequest,
    ClusterPromoteRequest,
    ClusterRollbackRequest,
    ClusterUniverseFitTiersRequest,
    FactorOlsClusterRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quant"])


@router.get("/api/quant/cluster-bars/status")
def quant_cluster_bars_status(watching_limit: int = 100) -> Dict[str, Any]:
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


@router.get("/api/quant/cluster-minute/status")
def quant_cluster_minute_status(
    watching_limit: int = 100,
    period: str = "5",
    min_span_days: int = 30,
    include_label_portrait: bool = True,
) -> Dict[str, Any]:
    """观察池 5m 分钟缓存覆盖（研究枢纽 UI）。"""
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
def quant_factor_ols_clusters(body: FactorOlsClusterRequest) -> Dict[str, Any]:
    """研究池：单票 OLS β 聚类 → 组内共用池 OLS / 小步权草案；不写 config。

    默认入队 Job（``GET /api/jobs/quant-ols-clusters``）；``sync=true`` 同步兼容单测。
    """
    kwargs = dict(
        lookback=body.lookback,
        horizon_days=body.horizon_days,
        holdout_trading_days=body.holdout_trading_days,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        n_clusters=body.n_clusters,
        pit_fundamentals=body.pit_fundamentals,
        l2_normalize_betas=body.l2_normalize_betas,
        beta_scale=body.beta_scale,
        cluster_method=body.cluster_method,
        cluster_linkage=body.cluster_linkage,
        within_dist_quantile=body.within_dist_quantile,
        run_oos_gate=body.run_oos_gate,
        oos_tol_pp=body.oos_tol_pp,
        run_group_score=body.run_group_score,
        run_pool_merge=body.run_pool_merge,
        top_n_per_group=body.top_n_per_group,
        respect_regime=body.respect_regime,
        select_ridge=body.select_ridge,
        collinearity_policy=body.collinearity_policy,
        refresh_bars=bool(body.refresh_bars),
    )
    try:
        if body.sync:
            return deps.quant.run_factor_ols_cluster_experiment(**kwargs)
        return deps.quant.start_factor_ols_cluster_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/factor-ols-clusters/last-report")
def quant_factor_ols_clusters_last_report() -> Dict[str, Any]:
    """最近一次成功分组报告（优先 ``cluster_last_report``；供进页恢复 / Job 水合）。"""
    try:
        from core.signal.score_display import json_safe
        from quant.services.quant_service_factors import _load_latest_cluster_report

        report = _load_latest_cluster_report()
        if not report:
            return {
                "success": False,
                "error": "尚无落盘分组报告或研究草稿（请先点「跑分组」）",
            }
        return json_safe(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-paper-preview")
def quant_cluster_paper_preview(body: ClusterPaperPreviewRequest) -> Dict[str, Any]:
    """分池簿→纸面调仓已停用；请改用 Follow「手动预演」（观察池 + rank_lots）。"""
    _ = body
    return {
        "success": False,
        "ok": False,
        "error": "分池簿纸面调仓已停用；请到交易执行页用观察池 rank_lots 预演/确认",
        "deprecated": True,
        "redirect": "/follow",
    }


@router.post("/api/quant/cluster-multi-score")
def quant_cluster_multi_score(body: ClusterMultiScoreRequest) -> Dict[str, Any]:
    """code_map 多权复打分：仅组内排序，不写 signal_config。"""
    try:
        return deps.quant.run_cluster_multi_score(
            artifact=body.artifact,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-live/status")
def quant_cluster_live_status(
    audit_rotate: bool = False,
    audit_offset: Optional[int] = None,
    light: bool = False,
    run_auto_demote: bool = False,
) -> Dict[str, Any]:
    """分组 live 状态：active / draft / health / mode。

    ``light=1``：只读 mode/簿长（交易执行状态条）；不跑证据包/自动降级。
    ``run_auto_demote``：默认关；日更请走 prepare 路径。
    """
    try:
        return deps.quant.cluster_live_status(
            audit_rotate=bool(audit_rotate),
            audit_offset=audit_offset,
            light=bool(light),
            run_auto_demote=bool(run_auto_demote),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/apply")
def quant_cluster_live_apply(body: ClusterApplyShortcutRequest) -> Dict[str, Any]:
    """一键：晋升 + 设 mode（默认 shadow）。分池簿已停用。"""
    try:
        return deps.quant.apply_cluster_live_shortcut(
            body.artifact,
            from_draft=bool(body.from_draft),
            note=body.note,
            mode=body.mode,
            force=bool(body.force),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/promote")
def quant_cluster_live_promote(body: ClusterPromoteRequest) -> Dict[str, Any]:
    """研究产物/草稿 → live active（不写全局 weights）。"""
    try:
        return deps.quant.promote_cluster_live(
            body.artifact,
            note=body.note,
            force=bool(body.force),
            from_draft=bool(body.from_draft),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-live/promote-preflight")
def quant_cluster_promote_preflight(from_draft: bool = True) -> Dict[str, Any]:
    """B3：draft vs active 晋升预检（OOS / R² / IC / 焦点票）。"""
    try:
        return deps.quant.compare_cluster_partition_vs_active(from_draft=bool(from_draft))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/rollback")
def quant_cluster_live_rollback(body: ClusterRollbackRequest) -> Dict[str, Any]:
    try:
        return deps.quant.rollback_cluster_live(to_version=body.to_version)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/mode")
def quant_cluster_live_mode(body: ClusterModeRequest) -> Dict[str, Any]:
    """设置 cluster_scoring.mode = off|shadow|active。"""
    try:
        return deps.quant.set_cluster_live_mode(
            body.mode, enabled=body.enabled, force=bool(body.force)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/universe-fit-tiers")
def quant_cluster_live_universe_fit_tiers(
    body: ClusterUniverseFitTiersRequest,
) -> Dict[str, Any]:
    """设置观察池 live 宇宙拟合档（A/B/C）。不改 weights / mode。"""
    try:
        return deps.quant.set_cluster_universe_fit_tiers(body.universe_fit_tiers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/cluster-live/fit-tiers")
def quant_cluster_live_fit_tiers() -> Dict[str, Any]:
    """code → A/B/C，给四页股票名徽标。"""
    try:
        return deps.quant.cluster_live_fit_tiers()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/refresh-book")
def quant_cluster_live_refresh_book() -> Dict[str, Any]:
    """分池簿已停用。"""
    return {
        "success": False,
        "ok": False,
        "deprecated": True,
        "error": "分池簿已停用；调仓请用 /follow 观察池 rank_lots",
        "signal_config_touched": False,
    }


@router.post("/api/quant/cluster-live/rank")
def quant_cluster_live_rank() -> Dict[str, Any]:
    """分池排序：组权打分 → 全局按 score 排序截断。"""
    try:
        return deps.quant.rank_cluster_live_pools()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
