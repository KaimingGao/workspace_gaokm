"""量化研究台 API — backtesting。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    AbCompareRequest,
    ParamGridRequest,
    PortfolioBacktestRequest,
    ReturnModelFitRequest,
    ReturnModelPromoteRequest,
    T0BacktestRequest,
)

router = APIRouter(tags=["quant"])


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
            include_cost_compare=body.include_cost_compare,
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


@router.post("/api/quant/param-grid")
def quant_param_grid(body: ParamGridRequest):
    """Top-K × lookback 参数扫描（限格）。

    默认入队 Job（``GET /api/jobs/quant-param-grid``）；``sync=true`` 同步兼容单测。
    """
    kwargs = dict(
        codes=body.codes,
        top_k_values=body.top_k_values,
        lookback_values=body.lookback_values,
        horizon_days=body.horizon_days,
        min_score=body.min_score,
        min_predicted_score=body.min_predicted_score,
        apply_costs=body.apply_costs,
        max_cells=body.max_cells,
        weight_mode=body.weight_mode,
        dropout_n=body.dropout_n,
        exclude_st=body.exclude_st,
        min_avg_amount_pctile=body.min_avg_amount_pctile,
        rank_mode=body.rank_mode,
    )
    try:
        if body.sync:
            return deps.quant.run_param_grid(**kwargs)
        return deps.quant.start_param_grid_job(**kwargs)
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
