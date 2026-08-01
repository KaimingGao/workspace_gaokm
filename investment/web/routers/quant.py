"""量化研究台 API。

产品动作（URL 不变）：
  策略 — /api/signal/config/* · /api/quant/config
  历史验证 — /api/watching/* · portfolio-backtest · cross-section · neutral-compare
  前瞻验证 — /api/paper/* · /api/quant/t0-backtest
  联动摘要 — /api/portfolio/quant-bridge（paper 持仓，只读）
映射：GET /api/quant/actions
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web import deps
from web.schemas import (
    AbCompareRequest,
    CrossSectionRequest,
    FactorCsIcRequest,
    FactorExperimentRequest,
    FactorOlsPoolRequest,
    NextDayTrendRequest,
    ParamGridRequest,
    PortfolioBacktestRequest,
    QuantInterpretRequest,
    QuantReportRequest,
    T0BacktestRequest,
    ThresholdSuggestRequest,
    WeightSuggestRequest,
)

router = APIRouter(tags=["quant"])


class SignalConfigDraftBody(BaseModel):
    config: Dict[str, Any]
    note: str = ""


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
    return deps.quant.read_signal_config_file()


@router.get("/api/signal/config/file")
def signal_config_file():
    out = deps.quant.read_signal_config_file()
    return {
        "ok": True,
        "exists": out.get("exists"),
        "path": out.get("path"),
        "readonly": True,
        "config": out.get("config"),
        "note": out.get("note"),
    }


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


@router.post("/api/signal/config/draft/validate")
def signal_config_draft_validate(body: SignalConfigDraftBody):
    """R2 · 校验草稿并相对生产算 diff（不写盘）。"""
    return deps.quant.validate_signal_config_draft(body.config)


@router.post("/api/signal/config/draft/save")
def signal_config_draft_save(body: SignalConfigDraftBody):
    """R2 · 保存草稿到 signal_config_draft.json（非生产）。"""
    out = deps.quant.save_signal_config_draft(body.config, note=body.note or "")
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("errors") or out.get("error") or "save failed")
    return out


@router.get("/api/signal/config/draft")
def signal_config_draft_get():
    return deps.quant.load_signal_config_draft()


@router.post("/api/signal/config/draft/diff")
def signal_config_draft_diff(body: SignalConfigDraftBody):
    return deps.quant.diff_signal_config_draft(body.config)


@router.post("/api/signal/config/draft/promote")
def signal_config_draft_promote(body: SignalConfigDraftBody):
    """R2 · 人审晋升草稿 → signal_config.json（先备份）。"""
    out = deps.quant.promote_signal_config_draft(body.config, note=body.note or "")
    if not out.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=out.get("errors") or out.get("error") or "promote failed",
        )
    return out


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


@router.post("/api/quant/next-day-trend")
def quant_next_day_trend(body: NextDayTrendRequest):
    """观察池日频+1：收盘→次日方向；研究探针，不写 config。"""
    try:
        return deps.quant.run_next_day_trend(
            lookback=body.lookback,
            lookback_eval_days=body.lookback_eval_days,
            flat_band_pct=body.flat_band_pct,
            watching_limit=body.watching_limit,
            codes=body.codes,
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
        )
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


@router.get("/api/portfolio/quant-bridge")
def portfolio_quant_bridge(include_stance: bool = False):
    """模拟持仓联动摘要（路径保留；对照仓已下线，持仓取自 paper）。"""
    try:
        return deps.quant.build_portfolio_bridge(include_stance=include_stance)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
