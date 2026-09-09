"""量化研究台 API — backtesting。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    AbCompareRequest,
    PaperReplayBacktestRequest,
    ReturnModelFitRequest,
    ReturnModelPromoteRequest,
    T0BacktestRequest,
)

router = APIRouter(tags=["quant"])


@router.post("/api/quant/t0-backtest")
def quant_t0_backtest(body: T0BacktestRequest) -> Dict[str, Any]:
    try:
        rules = {
            "t0_ratio": body.t0_ratio,
            "must_cover_same_day": body.must_cover_same_day,
        }
        if body.fill_mode:
            rules["fill_mode"] = body.fill_mode
        if body.direction:
            rules["direction"] = body.direction
        rules["path_mode"] = "first_touch"
        if body.dir_enter is not None:
            rules["dir_enter"] = body.dir_enter
        if body.min_range_pct is not None:
            rules["min_range_pct"] = body.min_range_pct
        if body.use_atr is not None:
            rules["use_atr"] = body.use_atr
        for yk in (
            "fill_mode_sell_then_buy",
            "fill_mode_buy_then_sell",
            "min_range_pct_sell_then_buy",
            "min_range_pct_buy_then_sell",
            "y_trade_enter",
            "y_trade_strong",
            "y_trade_floor",
            "y_tau_enter",
            "y_tau_enter_strong",
            "y_tau_enter_sell_then_buy",
            "y_tau_enter_buy_then_sell",
            "y_enter_enabled",
            "y_enter_alt_enabled",
            "r_tau_enter",
            "r_tau_enter_alt",
            "y_tau_enter_alt",
            "y_path_enter_alt",
            "y_ratio_cut",
            "y_ratio_boost_cap",
            "y_eod_prior",
            "y_eod_enter",
            "y_eod_strong",
            "y_eod_tau_sign_gate",
            "y_trade_tau_sign_gate",
            "y_on_allow",
            "y_on_risk",
            "y_block_tau_nowcast_sign",
            "y_tau_leg1_prior",
            "y_tau_leg1_prior_mode",
            "y_tau_leg1_prior_risk",
            "y_tau_leg1_prior_shift_scale",
            "y_nc_enter",
            "y_nc_strong",
            "y_nowcast_enter",
            "y_tau_map",
            "y_use_path",
            "y_path_enter",
            "y_path_enter_sell_then_buy",
            "y_path_enter_buy_then_sell",
            "y_path_strong",
            "y_complexity_max",
            "y_cx_max",
            "y_tpd_max",
            "y_complexity_max_alt",
            "y_tpd_max_alt",
            "y_path_required",
            "y_gap_tier_mode",
            "y_gap_tier_pct",
            "y_nowcast_oc_gate",
            "t0_close_band_delta_pct",
            "t0_price_space_gate",
            "t0_price_space_max_dev_pct",
            "t0_price_space_prev_dev_pct",
            "t0_round_ratio",
            "t0_max_position_pct",
            "t0_slots_max_rounds",
            "t0_slots_enabled",
            "y_tau_exit_price_skip",
            "y_tau_exit_price_mult",
            "y_tau_exit_price_skip_buy_then_sell",
            "y_tau_exit_price_mult_buy_then_sell",
            "y_tau_exit_price_skip_sell_then_buy",
            "y_tau_exit_price_mult_sell_then_buy",
            "y_tau_exit_price_bias",
            "y_tau_exit_price_move_min",
            "y_tau_exit_price_move_max",
            "y_tau_exit_price_bias_buy_then_sell",
            "y_tau_exit_price_move_min_buy_then_sell",
            "y_tau_exit_price_move_max_buy_then_sell",
            "y_tau_exit_price_bias_sell_then_buy",
            "y_tau_exit_price_move_min_sell_then_buy",
            "y_tau_exit_price_move_max_sell_then_buy",
            "must_cover_same_day_sell_then_buy",
            "must_cover_same_day_buy_then_sell",
            "t0_pm_degrade_sell_then_buy",
            "t0_pm_degrade_buy_then_sell",
            "t0_pm_chase_interval_min",
            "t0_pm_chase_interval_min_sell_then_buy",
            "t0_pm_chase_interval_min_buy_then_sell",
            "t0_stop_pct_buy_then_sell",
            "t0_stop_pct_sell_then_buy",
            "t0_stop_arm_bars",
            "t0_stop_on_close",
        ):
            val = getattr(body, yk, None)
            if val is not None:
                rules[yk] = val

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
            use_minute=True,
            compare_daily=False,
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
def quant_portfolio_backtest(body: PaperReplayBacktestRequest) -> Dict[str, Any]:
    try:
        return deps.quant.run_portfolio_backtest(
            codes=body.codes,
            lookback=body.lookback,
            apply_costs=body.apply_costs,
            fetch_fundamentals=body.fetch_fundamentals,
            exclude_st=body.exclude_st,
            min_avg_amount_pctile=body.min_avg_amount_pctile,
            include_benchmark=body.include_benchmark,
            benchmark_code=body.benchmark_code,
            y_on_alpha=body.y_on_alpha,
            fusion_w_trade=body.fusion_w_trade,
            fusion_w_nowcast=body.fusion_w_nowcast,
            rank_enter=body.rank_enter,
            rank_strong=body.rank_strong,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/last-portfolio-backtest")
def quant_last_portfolio_backtest() -> Dict[str, Any]:
    """最近一次成功产品回测（``/replay`` 刷新恢复，不重跑）。"""
    try:
        from core.signal.score_display import json_safe

        return json_safe(deps.quant.load_last_portfolio_backtest())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/last-t0-backtest")
def quant_last_t0_backtest() -> Dict[str, Any]:
    """最近一次成功做 T 回测（``/follow`` 刷新恢复，不重跑）。"""
    try:
        from core.signal.score_display import json_safe

        return json_safe(deps.quant.load_last_t0_backtest())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/param-grid")
def quant_param_grid() -> Dict[str, Any]:
    """参数网格已下线（lookback×K 属 TopK 研究探针；产品回测只认 paper_replay）。"""
    raise HTTPException(
        status_code=410,
        detail="参数网格已下线；产品回测只认 paper_replay / rank_lots",
    )


@router.post("/api/quant/portfolio-neutral-compare")
def quant_portfolio_neutral_compare() -> Dict[str, Any]:
    """中性化对照研究口已下线（ŷ 路径开关空转；日报也不再嵌入）。"""
    raise HTTPException(
        status_code=410,
        detail="中性化对照研究口已下线；产品回测只认 paper_replay / rank_lots",
    )


@router.post("/api/quant/ab-compare")
def quant_ab_compare(body: AbCompareRequest) -> Dict[str, Any]:
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
def quant_return_model_fit(body: ReturnModelFitRequest) -> Dict[str, Any]:
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
def quant_return_model_promote(body: ReturnModelPromoteRequest) -> Dict[str, Any]:
    try:
        return deps.quant.promote_return_score_model(note=body.note)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/return-model/status")
def quant_return_model_status() -> Dict[str, Any]:
    try:
        return deps.quant.return_score_model_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
