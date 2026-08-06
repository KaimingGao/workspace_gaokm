"""量化研究台 API。

产品动作（URL 不变）：
  策略 — /api/signal/config/* · /api/quant/config
  历史验证 — /api/watching/* · portfolio-backtest · cross-section · neutral-compare
  前瞻验证 — /api/paper/* · /api/quant/t0-backtest
  联动摘要 — /api/portfolio/quant-bridge（paper 持仓，只读）
映射：GET /api/quant/actions
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web import deps
from web.schemas import (
    AbCompareRequest,
    ClusterApplyShortcutRequest,
    ClusterModeRequest,
    ClusterMultiScoreRequest,
    ClusterPaperPreviewRequest,
    ClusterPromoteRequest,
    ClusterRollbackRequest,
    CrossSectionRequest,
    FactorCsIcRequest,
    FactorExperimentRequest,
    FactorOlsClusterRequest,
    ScoringFloorsRequest,
    SentimentPriorRequest,
    FactorOlsPoolRequest,
    ParamGridRequest,
    PortfolioBacktestRequest,
    QuantInterpretRequest,
    QuantReportRequest,
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreReviewRequest,
    ReturnModelFitRequest,
    ReturnModelPromoteRequest,
    T0BacktestRequest,
    ThresholdSuggestRequest,
    WeightSuggestRequest,
)

router = APIRouter(tags=["quant"])


class PortfolioBacktestExportBody(BaseModel):
    """R4.4 · 回溯页一键导出机构报告。"""

    result: Dict[str, Any]
    format: str = "markdown"


@router.get("/api/quant/actions")
def quant_actions():
    """策略 · 历史/前瞻验证 · 对比 归属图（机器可读）。"""
    return deps.quant.action_map()


@router.post("/api/quant/t0-backtest")
def quant_t0_backtest(body: T0BacktestRequest):
    try:
        rules = {
            "t0_ratio": body.t0_ratio,
            "sell_trigger_pct": body.sell_trigger_pct,
            "buy_trigger_pct": body.buy_trigger_pct,
            "must_cover_same_day": body.must_cover_same_day,
        }
        if body.fill_mode:
            rules["fill_mode"] = body.fill_mode
        if body.direction:
            rules["direction"] = body.direction
        if body.path_mode:
            rules["path_mode"] = body.path_mode
        if body.dir_enter is not None:
            rules["dir_enter"] = body.dir_enter
        if body.min_range_pct is not None:
            rules["min_range_pct"] = body.min_range_pct

        code = (body.code or "").strip()
        from_paper = bool(body.from_paper)
        # 旧前端写死 code=茅台 且未声明 from_paper：视为「测全部模拟持仓」，勿当成单票研究
        legacy_default = code in {"茅台", "贵州茅台"} and body.codes is None
        if from_paper and legacy_default:
            code = ""

        out = deps.quant.run_t0_backtest(
            code,
            lookback=body.lookback,
            initial_shares=body.initial_shares,
            rules=rules,
            from_paper=from_paper,
            codes=body.codes,
            compare_optimistic=body.compare_optimistic,
            use_minute=body.use_minute,
            compare_daily=body.compare_daily,
        )
        if isinstance(out, dict) and out.get("success"):
            if out.get("from_holdings") and int(out.get("ok_count") or 0) > 1:
                out["scope_label"] = f"模拟持仓 {out.get('ok_count')} 只"
            elif out.get("from_holdings"):
                out["scope_label"] = f"模拟持仓 · {out.get('stock_name') or out.get('stock_code') or '单票'}"
            else:
                out["scope_label"] = f"研究标的 · {out.get('stock_name') or code or '—'}"
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
            bearish_score_min=body.bearish_score_min,
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


@router.get("/api/quant/last")
def quant_last():
    return deps.quant.load_last_daily()


@router.post("/api/quant/report")
def quant_report(body: QuantReportRequest):
    try:
        report = deps.quant.build_daily_report(
            body.code,
            include_cross_section=body.include_cross_section,
            include_portfolio_backtest=body.include_portfolio_backtest,
        )
        if body.save:
            deps.quant.save_daily_report(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return report


@router.get("/api/quant/strategies")
def quant_strategies():
    return deps.quant.list_strategies()


@router.get("/api/quant/factors")
def quant_factors():
    return deps.quant.list_factors()


@router.get("/api/quant/factor-panel")
def quant_factor_panel(
    code: str = "茅台",
    with_experiment: bool = False,
    lookback: int = 120,
    horizon_days: int = 3,
):
    try:
        return deps.quant.build_factor_panel(
            code,
            lookback=lookback,
            horizon_days=horizon_days,
            with_experiment=with_experiment,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/cross-section")
def quant_cross_section(body: CrossSectionRequest):
    try:
        return deps.quant.run_cross_section(
            codes=body.codes,
            limit=body.limit,
            min_score=body.min_score,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-experiment")
def quant_factor_experiment(body: FactorExperimentRequest):
    try:
        return deps.quant.run_factor_experiment(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-ols")
def quant_factor_ols(body: FactorExperimentRequest):
    try:
        return deps.quant.run_factor_ols_experiment(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-ols-pool")
def quant_factor_ols_pool(body: FactorOlsPoolRequest):
    """研究池堆叠时序 OLS；显式触发，默认不进页自动跑。"""
    try:
        return deps.quant.run_factor_ols_pool_experiment(
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
):
    """分组 live 状态：active / draft / health / mode。"""
    try:
        return deps.quant.cluster_live_status(
            audit_rotate=bool(audit_rotate),
            audit_offset=audit_offset,
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


@router.post("/api/quant/factor-cs-ic")
def quant_factor_cs_ic(body: FactorCsIcRequest):
    """S1 · 研究池逐因子日频截面 IC（Pearson + Spearman）；不写 config。"""
    try:
        return deps.quant.run_factor_cs_ic_experiment(
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
            min_names=body.min_names,
            pit_fundamentals=body.pit_fundamentals,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/ab-compare")
def quant_ab_compare(body: AbCompareRequest):
    """S2 · A/B 对照指纹。"""
    try:
        from core.ab_compare import build_ab_compare

        return build_ab_compare(
            label_a=body.label_a,
            label_b=body.label_b,
            result_a=body.result_a,
            result_b=body.result_b,
            config_a=body.config_a,
            config_b=body.config_b,
            note=body.note,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/weight-suggest")
def quant_weight_suggest(body: WeightSuggestRequest):
    try:
        return deps.quant.suggest_weights(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            use_cs_ic=body.use_cs_ic,
            watching_limit=body.watching_limit,
            run_oos_gate=body.run_oos_gate,
            oos_tol_pp=body.oos_tol_pp,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/threshold-suggest")
def quant_threshold_suggest(body: ThresholdSuggestRequest):
    try:
        return deps.quant.suggest_thresholds(
            body.code,
            lookback=body.lookback,
            use_watching=body.use_watching,
            watching_limit=body.watching_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/interpret")
def quant_interpret(body: QuantInterpretRequest):
    try:
        report = None
        if body.save_before_interpret:
            report = deps.quant.build_daily_report(body.code, include_portfolio_backtest=False)
            deps.quant.save_daily_report(report)
        elif body.use_saved:
            saved = deps.quant.load_last_daily()
            if not saved.get("empty"):
                report = saved
        result = deps.quant.interpret_report(
            report,
            use_saved=False if report else body.use_saved,
            offline=body.offline,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result.get("error") or "解读失败")
    return result


@router.get("/api/quant/score-review/dates")
def quant_score_review_dates(limit: int = 30):
    """已冻结打分账本日期列表。"""
    try:
        return deps.quant.list_score_ledger_dates(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/score-review")
def quant_score_review(
    as_of: Optional[str] = None,
    horizon_days: int = 3,
    autofill: bool = True,
):
    """昨日复盘：ŷ 方向 vs 前瞻收益。"""
    try:
        return deps.quant.build_score_review(
            as_of=as_of,
            horizon_days=horizon_days,
            autofill=autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-review")
def quant_score_review_post(body: ScoreReviewRequest):
    try:
        return deps.quant.build_score_review(
            as_of=body.as_of,
            horizon_days=body.horizon_days,
            autofill=body.autofill,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/score-ledger/freeze")
def quant_score_ledger_freeze(body: ScoreLedgerFreezeRequest):
    """从当前集群书冻结打分账本。"""
    try:
        out = deps.quant.freeze_score_ledger(as_of=body.as_of)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "冻结失败")
    return out


@router.post("/api/quant/score-outcomes/fill")
def quant_score_outcomes_fill(body: ScoreOutcomesFillRequest):
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


@router.get("/api/quant/export")
def quant_export(format: str = "markdown", use_saved: bool = True):
    fmt = (format or "markdown").strip().lower()
    if fmt not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="仅支持 format=markdown|html")
    result = deps.quant.export_report(use_saved=use_saved, fmt=fmt)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "导出失败")
    return result


@router.post("/api/quant/export/backtest")
def quant_export_backtest(body: PortfolioBacktestExportBody):
    """R4.4 · 导出单次 Top-K 回测机构报告（与页内块序对齐）。"""
    fmt = (body.format or "markdown").strip().lower()
    if fmt not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="仅支持 format=markdown|html")
    result = deps.quant.export_portfolio_backtest_report(body.result, fmt=fmt)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "导出失败")
    return result


@router.get("/api/quant/export/summary")
def quant_export_summary(use_saved: bool = True):
    result = deps.quant.export_executive_summary(use_saved=use_saved)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "无摘要")
    return result


@router.get("/api/quant/reports")
def quant_reports(limit: int = 20):
    limit = max(1, min(int(limit or 20), 100))
    return deps.quant.list_report_archive(limit=limit)


@router.get("/api/quant/reports/{filename}")
def quant_report_file(filename: str):
    result = deps.quant.read_report_archive(filename)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "未找到报告")
    return result


@router.post("/api/quant/portfolio-backtest")
def quant_portfolio_backtest(body: PortfolioBacktestRequest):
    try:
        return deps.quant.run_portfolio_backtest(
            codes=body.codes,
            lookback=body.lookback,
            top_k=body.top_k,
            horizon_days=body.horizon_days,
            min_score=body.min_score,
            apply_costs=body.apply_costs,
            include_wf_slices=body.include_wf_slices,
            wf_n_splits=body.wf_n_splits,
            fetch_fundamentals=body.fetch_fundamentals,
            weight_mode=body.weight_mode,
            max_position_pct=body.max_position_pct,
            max_sector_pct=body.max_sector_pct,
            dropout_n=body.dropout_n,
            exclude_st=body.exclude_st,
            min_avg_amount_pctile=body.min_avg_amount_pctile,
            include_score_ic=body.include_score_ic,
            include_quantile=body.include_quantile,
            include_benchmark=body.include_benchmark,
            benchmark_code=body.benchmark_code,
            rank_mode=body.rank_mode,
            min_predicted_score=body.min_predicted_score,
            return_model_min_samples=body.return_model_min_samples,
            return_model_ridge_lambda=body.return_model_ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/return-model/fit")
def quant_return_model_fit(body: ReturnModelFitRequest):
    try:
        return deps.quant.fit_return_score_model(
            codes=body.codes,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            min_samples=body.min_samples,
            save_draft=body.save_draft,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/return-model/promote")
def quant_return_model_promote(body: ReturnModelPromoteRequest):
    try:
        return deps.quant.promote_return_score_model(note=body.note)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/return-model/status")
def quant_return_model_status():
    try:
        return deps.quant.return_score_model_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/param-grid")
def quant_param_grid(body: ParamGridRequest):
    """W3.3 · Top-K × lookback 参数扫描（限格）。"""
    try:
        return deps.quant.run_param_grid(
            codes=body.codes,
            top_k_values=body.top_k_values,
            lookback_values=body.lookback_values,
            horizon_days=body.horizon_days,
            min_score=body.min_score,
            apply_costs=body.apply_costs,
            max_cells=body.max_cells,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/portfolio-neutral-compare")
def quant_portfolio_neutral_compare(body: PortfolioBacktestRequest):
    try:
        return deps.quant.run_portfolio_neutral_compare(
            codes=body.codes,
            lookback=body.lookback,
            top_k=body.top_k,
            horizon_days=body.horizon_days,
            min_score=body.min_score,
            min_predicted_score=body.min_predicted_score,
            apply_costs=body.apply_costs,
            fetch_fundamentals=body.fetch_fundamentals,
            weight_mode=body.weight_mode,
            max_position_pct=body.max_position_pct,
            max_sector_pct=body.max_sector_pct,
            dropout_n=body.dropout_n,
            exclude_st=body.exclude_st,
            min_avg_amount_pctile=body.min_avg_amount_pctile,
            benchmark_code=body.benchmark_code,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/quant-bridge")
def paper_quant_bridge(include_stance: bool = False):
    """模拟持仓联动摘要（canonical；持仓取自 paper）。"""
    try:
        out = deps.quant.build_portfolio_bridge(include_stance=include_stance)
        if isinstance(out, dict):
            out = dict(out)
            out["canonical"] = True
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/portfolio/quant-bridge")
def portfolio_quant_bridge(include_stance: bool = False):
    """已弃用别名 → 请用 ``GET /api/paper/quant-bridge``（对照仓已下线）。"""
    try:
        out = deps.quant.build_portfolio_bridge(include_stance=include_stance)
        if isinstance(out, dict):
            out = dict(out)
            out["deprecated"] = True
            out["canonical"] = "/api/paper/quant-bridge"
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
