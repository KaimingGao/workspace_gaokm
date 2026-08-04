"""研究 OOS 门禁：heuristic_score 基线 vs predicted_score 研究臂（只读）。

产品定位：
- 基线：全局人工预定义线性加权（heuristic_score）
- 研究臂：研究枢纽回归模型 → predicted_score（ŷ）
- 目的：验证枢纽方案是否在样本外不劣于人工基线；过门 ≠ 自动 promote
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 产品语义（UI / API note 同源）
OOS_PRODUCT_SEMANTICS = (
    "产品语义：heuristic_score（人工线性加权）= 研究对照基线；"
    "predicted_score（研究枢纽回归 ŷ）= 选股实现方案。"
    "研究枢纽用于寻找更优 ŷ，以提升回测收益与交易选股；过门≠自动 promote。"
)


def oos_semantics_note(*, oos_tol_pp: float = 1.0) -> str:
    """门禁 note：切分规则 + 过门条件 + 产品语义。"""
    tol = float(oos_tol_pp)
    return (
        "同一研究池 Top-K：权益曲线后 30% 为 OOS。"
        f"过门条件：研究臂（ŷ）OOS ≥ 基线（heuristic）OOS − {tol}pp，"
        "且不新增 OOS 失败旗标。"
        + " "
        + OOS_PRODUCT_SEMANTICS
    )


def _metrics_from_backtest(bt: Dict[str, Any]) -> Dict[str, Any]:
    from core.backtest.oos_report import split_oos_summary

    m = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    oos = split_oos_summary(curve, oos_ratio=0.3)
    return {
        "success": bool(bt.get("success")),
        "error": bt.get("error"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "trade_count": m.get("trade_count") or len(bt.get("trades") or []),
        "oos": oos,
        "rank_mode": (bt.get("params") or {}).get("rank_mode")
        or bt.get("rank_mode"),
    }


def _run_topk_heuristic(
    stock_bars: Dict[str, List[dict]],
    weights: Dict[str, float],
    *,
    top_k: int,
    horizon_days: int,
    min_score: float,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """基线臂：全局人工权 → heuristic_score 排序。"""
    from core.backtest.topk_backtest import backtest_topk_equal_weight
    from core.signal.config import signal_config_overlay

    with signal_config_overlay({"weights": weights}):
        return backtest_topk_equal_weight(
            stock_bars,
            top_k=top_k,
            horizon_days=horizon_days,
            min_score=min_score,
            apply_costs=True,
            fundamentals_by_code=fundamentals_by_code,
            weight_mode="equal",
            rank_mode="heuristic_score",
            allow_heuristic_baseline=True,
            use_live_cluster_models=False,
            return_models_by_code={},
        )


def _run_topk_predicted(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    horizon_days: int,
    return_models_by_code: Optional[Dict[str, Any]],
    ridge_lambda: float = 0.0,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """研究臂：注入组/全局 return_model → predicted_score 排序。"""
    from core.backtest.topk_backtest import backtest_topk_equal_weight

    return backtest_topk_equal_weight(
        stock_bars,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=0.0,
        apply_costs=True,
        fundamentals_by_code=fundamentals_by_code,
        weight_mode="equal",
        rank_mode="predicted_score",
        min_predicted_score=None,
        use_live_cluster_models=False,
        return_models_by_code=return_models_by_code
        if return_models_by_code is not None
        else {},
        return_model_ridge_lambda=float(ridge_lambda or 0.0),
        allow_heuristic_baseline=False,
    )


def _compare_arms(
    baseline_m: Dict[str, Any],
    research_m: Dict[str, Any],
    *,
    tol: float,
    lookback: int,
    top_k: int,
    horizon_days: int,
    stock_count: int,
    codes: List[str],
) -> Dict[str, Any]:
    if not baseline_m["success"] or not research_m["success"]:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "backtest_failed",
            "baseline": baseline_m,
            "research": research_m,
            # 兼容旧字段名
            "current": baseline_m,
            "suggested": research_m,
            "stock_count": stock_count,
            "codes": codes,
            "note": "Top-K 回测失败，跳过门禁判定。",
        }

    base_oos = (baseline_m.get("oos") or {}).get("oos_return_pct")
    res_oos = (research_m.get("oos") or {}).get("oos_return_pct")
    passed = False
    reason = "insufficient_oos"
    delta_oos = None
    if base_oos is not None and res_oos is not None:
        delta_oos = round(float(res_oos) - float(base_oos), 2)
        res_failed = bool((research_m.get("oos") or {}).get("failed"))
        base_failed = bool((baseline_m.get("oos") or {}).get("failed"))
        if delta_oos >= -tol and not (res_failed and not base_failed):
            passed = True
            reason = "oos_not_worse"
        elif delta_oos < -tol:
            reason = f"oos_worse_{delta_oos}pp"
        else:
            reason = (research_m.get("oos") or {}).get("fail_reason") or "research_oos_failed"

    return {
        "ok": True,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "delta_oos_pp": delta_oos,
        "oos_tol_pp": tol,
        "lookback": lookback,
        "top_k": top_k,
        "horizon_days": horizon_days,
        "stock_count": stock_count,
        "codes": codes,
        "baseline_rank_mode": "heuristic_score",
        "research_rank_mode": "predicted_score",
        "baseline": baseline_m,
        "research": research_m,
        "current": baseline_m,
        "suggested": research_m,
        "note": oos_semantics_note(oos_tol_pp=tol),
    }


def evaluate_research_oos(
    *,
    codes: Optional[List[str]] = None,
    research_models_by_code: Optional[Dict[str, Any]] = None,
    baseline_weights: Optional[Dict[str, float]] = None,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    watching_limit: int = 10,
    oos_tol_pp: float = 1.0,
    min_names: int = 3,
    ridge_lambda: float = 0.0,
) -> Dict[str, Any]:
    """heuristic 基线 vs predicted（研究模型）同一宇宙 Top-K OOS 对照。"""
    from core.signal.config import load_signal_config
    from core.watching_store import read_watching
    from quant.research.portfolio_data import load_portfolio_stock_bars

    if codes:
        use_codes = [str(c).strip() for c in codes if str(c).strip()]
    else:
        uni = read_watching()
        use_codes = list(uni.get("watchlist") or [])
    min_n = max(2, min(int(min_names or 3), 10))
    limit = max(min_n, min(int(watching_limit or 10), 40))
    use_codes = use_codes[:limit]
    if len(use_codes) < min_n:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "watching_too_small",
            "note": f"有效标的不足 {min_n} 只，跳过 OOS 门禁。"
            + " "
            + OOS_PRODUCT_SEMANTICS,
            "stock_count": len(use_codes),
        }

    models = research_models_by_code or {}
    if not models:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "no_return_model",
            "note": "无研究臂 return_model（ŷ），跳过 OOS。"
            + " "
            + OOS_PRODUCT_SEMANTICS,
            "stock_count": len(use_codes),
        }

    stock_bars, failures, fund_map = load_portfolio_stock_bars(
        use_codes,
        lookback=lookback,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < min_n:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "bars_too_few",
            "note": f"有效日线不足 {min_n} 只（失败 {len(failures)}）",
            "failures": failures[:8],
            "stock_count": len(stock_bars),
        }

    cfg = load_signal_config() or {}
    base_w = dict(baseline_weights or cfg.get("weights") or {})
    if not base_w:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "no_baseline_weights",
            "note": "无全局人工权重，无法跑 heuristic 基线。",
            "stock_count": len(stock_bars),
        }

    top_k_eff = max(1, min(int(top_k or 1), len(stock_bars)))
    # 研究模型只覆盖本组 codes
    models_eff = {
        str(c): models[str(c)]
        for c in stock_bars
        if str(c) in models
    }
    if not models_eff:
        # 允许同一模型广播到全部成员
        only = next(iter(models.values()), None)
        if only is not None:
            models_eff = {str(c): only for c in stock_bars}
        else:
            return {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "no_return_model",
                "note": "研究模型未覆盖有效日线标的。",
                "stock_count": len(stock_bars),
            }

    base_bt = _run_topk_heuristic(
        stock_bars,
        base_w,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fund_map or None,
    )
    res_bt = _run_topk_predicted(
        stock_bars,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        return_models_by_code=models_eff,
        ridge_lambda=ridge_lambda,
        fundamentals_by_code=fund_map or None,
    )
    return _compare_arms(
        _metrics_from_backtest(base_bt),
        _metrics_from_backtest(res_bt),
        tol=float(oos_tol_pp),
        lookback=lookback,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        stock_count=len(stock_bars),
        codes=list(stock_bars.keys()),
    )


def evaluate_weight_suggestion_oos(
    current_weights: Dict[str, float],
    suggested_weights: Dict[str, float],
    *,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    watching_limit: int = 10,
    oos_tol_pp: float = 1.0,
    codes: Optional[List[str]] = None,
    min_names: int = 3,
    research_models_by_code: Optional[Dict[str, Any]] = None,
    ridge_lambda: float = 0.0,
) -> Dict[str, Any]:
    """兼容入口：优先走 heuristic vs predicted；无模型时跳过。

    ``current_weights`` 用作 heuristic 基线权；``suggested_weights`` 已退役为选股权，
    仅在未提供 ``research_models_by_code`` 时无法构成研究臂。
    """
    if research_models_by_code:
        return evaluate_research_oos(
            codes=codes,
            research_models_by_code=research_models_by_code,
            baseline_weights=current_weights,
            lookback=lookback,
            top_k=top_k,
            horizon_days=horizon_days,
            min_score=min_score,
            watching_limit=watching_limit,
            oos_tol_pp=oos_tol_pp,
            min_names=min_names,
            ridge_lambda=ridge_lambda,
        )
    return {
        "ok": False,
        "passed": False,
        "skipped": True,
        "reason": "no_return_model",
        "note": (
            "OOS 已改为 heuristic_score 基线 vs predicted_score 研究臂；"
            "未提供 return_model，跳过（不再用建议权 overlay 当研究臂）。"
            + " "
            + OOS_PRODUCT_SEMANTICS
        ),
        "oos_tol_pp": float(oos_tol_pp),
    }
