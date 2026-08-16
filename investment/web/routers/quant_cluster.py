"""量化研究台 API — cluster live management。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    ClusterApplyShortcutRequest,
    ClusterModeRequest,
    ClusterMultiScoreRequest,
    ClusterPaperPreviewRequest,
    ClusterPromoteRequest,
    ClusterRollbackRequest,
    FactorOlsClusterRequest,
)

router = APIRouter(tags=["quant"])


@router.post("/api/quant/factor-ols-clusters")
def quant_factor_ols_clusters(body: FactorOlsClusterRequest):
    """研究池：单票 OLS β 聚类 → 组内共用池 OLS / 小步权草案；不写 config。

    默认入队 Job（``GET /api/jobs/quant-ols-clusters``）；``sync=true`` 同步兼容单测。
    """
    kwargs = dict(
        lookback=body.lookback,
        horizon_days=body.horizon_days,
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
def quant_factor_ols_clusters_last_report():
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
def quant_cluster_paper_preview(body: ClusterPaperPreviewRequest):
    """分池候选簿 → 纸面调仓预演；confirm=true 写 paper.json（不写 signal_config）。"""
    try:
        return deps.quant.preview_cluster_paper_rebalance(
            body.book or [],
            top_k=body.top_k,
            confirm=bool(body.confirm),
            artifact=body.artifact,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-multi-score")
def quant_cluster_multi_score(body: ClusterMultiScoreRequest):
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
):
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
def quant_cluster_live_apply(body: ClusterApplyShortcutRequest):
    """一键：晋升 + 设 mode（默认 shadow）+ 刷新分池簿。"""
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
def quant_cluster_live_promote(body: ClusterPromoteRequest):
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
def quant_cluster_promote_preflight(from_draft: bool = True):
    """B3：draft vs active 晋升预检（OOS / R² / IC / 焦点票）。"""
    try:
        return deps.quant.compare_cluster_partition_vs_active(from_draft=bool(from_draft))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/rollback")
def quant_cluster_live_rollback(body: ClusterRollbackRequest):
    try:
        return deps.quant.rollback_cluster_live(to_version=body.to_version)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/mode")
def quant_cluster_live_mode(body: ClusterModeRequest):
    """设置 cluster_scoring.mode = off|shadow|active。"""
    try:
        return deps.quant.set_cluster_live_mode(
            body.mode, enabled=body.enabled, force=bool(body.force)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/refresh-book")
def quant_cluster_live_refresh_book():
    """日更：按 active map 重打分并刷新合并簿（不重聚类）。"""
    try:
        return deps.quant.refresh_cluster_live_book()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cluster-live/rank")
def quant_cluster_live_rank():
    """分池排序：组权打分 → 全局按 score 排序截断。"""
    try:
        return deps.quant.rank_cluster_live_pools()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
