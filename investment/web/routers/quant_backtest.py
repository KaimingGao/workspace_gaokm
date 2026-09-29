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

_T0_RULE_KEYS = (
    "fill_mode_sell_then_buy",
    "fill_mode_buy_then_sell",
    "y_trade_enter",
    "y_trade_floor",
    "fusion_w_τc",
    "fusion_w_tc",
    "residual_w_oc",
    "residual_w_mode",
    "y_on_allow",
    "y_on_risk",
    "y_tw_enter",
    "y_τw_enter",
    "y_oc_enter",
    "y_oc_strong",
    "y_oc_enter_amount",
    "y_oc_strong_amount",
    "y_tw_vote_margin",
    "y_τw_vote_margin",
    "y_tw_midpoint",
    "y_τw_midpoint",
    "horizon_prob_backend",
    "t0_y_oc_target_scale",
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
    "t0_lock_win_arm_bars",
    "t0_lock_win_pct_buy_then_sell",
    "t0_lock_win_pct_sell_then_buy",
)


def _t0_backtest_kwargs(body: T0BacktestRequest) -> Dict[str, Any]:
    rules = {
        "t0_ratio": body.t0_ratio,
        "must_cover_same_day": body.must_cover_same_day,
    }
    if body.fill_mode:
        rules["fill_mode"] = body.fill_mode
    if body.direction:
        rules["direction"] = body.direction
    rules["path_mode"] = "first_touch"
    for yk in _T0_RULE_KEYS:
        val = getattr(body, yk, None)
        if val is not None:
            rules[yk] = val
    rules["t0_stop_on_close"] = True

    code = (body.code or "").strip()
    from_paper = bool(body.from_paper)
    # 旧前端写死 code=茅台 且未声明 from_paper：视为「测全部模拟持仓」，勿当成单票研究
    legacy_default = code in {"茅台", "贵州茅台"} and body.codes is None
    if from_paper and legacy_default:
        code = ""
    from core.t0.config import t0_backtest_virtual_shares

    return {
        "code": code,
        "lookback": body.lookback,
        "initial_shares": t0_backtest_virtual_shares(rules, body.initial_shares),
        "initial_cash": body.initial_cash,
        "rules": rules,
        "from_paper": from_paper,
        "codes": body.codes,
        "use_minute": True,
        "score_model_role": body.score_model_role,
    }


@router.post("/api/quant/t0-backtest")
def quant_t0_backtest(body: T0BacktestRequest) -> Dict[str, Any]:
    """做 T 回测。默认入队 ``GET /api/jobs/t0-backtest``；``sync=true`` 同步（单测）。"""
    try:
        kwargs = _t0_backtest_kwargs(body)
        if body.sync:
            return deps.quant.run_t0_backtest(**kwargs)
        return deps.quant.start_t0_backtest_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _portfolio_backtest_kwargs(body: PaperReplayBacktestRequest) -> Dict[str, Any]:
    w_oo = body.fusion_w_oo if body.fusion_w_oo is not None else body.fusion_w_trade
    w_oc = body.fusion_w_oc if body.fusion_w_oc is not None else body.fusion_w_nowcast
    return {
        "codes": body.codes,
        "lookback": body.lookback,
        "apply_costs": body.apply_costs,
        "fetch_fundamentals": body.fetch_fundamentals,
        "exclude_st": body.exclude_st,
        "min_avg_amount_pctile": body.min_avg_amount_pctile,
        "include_benchmark": body.include_benchmark,
        "benchmark_code": body.benchmark_code,
        "y_on_alpha": body.y_on_alpha,
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_oc,
        "fusion_w_trade": w_oo,
        "fusion_w_nowcast": w_oc,
        "rank_enter": body.rank_enter,
        "rank_strong": body.rank_strong,
        "rank_enter_alt": body.rank_enter_alt,
        "y_enter_enabled": body.y_enter_enabled,
        "y_enter_alt_enabled": body.y_enter_alt_enabled,
        "y_oo_gt0": body.y_oo_gt0,
        "y_oc_gt0": body.y_oc_gt0,
        "initial_cash": body.initial_cash,
        "fill_clock": body.fill_clock,
        "lot_base_amount": body.lot_base_amount,
        "lot_strong_amount": body.lot_strong_amount,
        "universe_fit_tiers": body.universe_fit_tiers,
        "use_predictability_tiers": bool(body.use_predictability_tiers),
        "predictability_tiers": body.predictability_tiers,
        "holdout_trading_days": body.holdout_trading_days,
        "predictability_head": body.predictability_head,
        "price_space_gate": body.price_space_gate,
        "score_model_role": body.score_model_role,
    }


@router.post("/api/quant/portfolio-backtest")
def quant_portfolio_backtest(body: PaperReplayBacktestRequest) -> Dict[str, Any]:
    """调仓回测。默认入队 ``GET /api/jobs/portfolio-backtest``；``sync=true`` 同步（单测）。"""
    try:
        kwargs = _portfolio_backtest_kwargs(body)
        if body.sync:
            return deps.quant.run_portfolio_backtest(**kwargs)
        return deps.quant.start_portfolio_backtest_job(**kwargs)
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
            holdout_trading_days=body.holdout_trading_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/return-model/promote")
def quant_return_model_promote(body: ReturnModelPromoteRequest) -> Dict[str, Any]:
    try:
        return deps.quant.promote_return_score_model(
            note=body.note,
            persist_role=body.persist_role,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/return-model/status")
def quant_return_model_status() -> Dict[str, Any]:
    try:
        return deps.quant.return_score_model_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
