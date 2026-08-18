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


def _metrics_from_backtest(
    bt: Dict[str, Any],
    *,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
) -> Dict[str, Any]:
    from core.backtest.oos_report import split_oos_summary

    enriched = bt
    try:
        from core.research.bt_excess_attach import attach_benchmark_excess

        enriched = attach_benchmark_excess(bt, stock_bars or {})
    except Exception:
        enriched = bt

    m = enriched.get("metrics") or {}
    curve = enriched.get("equity_curve") or []
    oos = split_oos_summary(curve, oos_ratio=0.3)
    bench = enriched.get("benchmark") or {}
    legs = enriched.get("alpha_beta_legs") or {}
    return {
        "success": bool(enriched.get("success")),
        "error": enriched.get("error"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "trade_count": m.get("trade_count") or len(enriched.get("trades") or []),
        "oos": oos,
        "rank_mode": (enriched.get("params") or {}).get("rank_mode")
        or enriched.get("rank_mode"),
        "benchmark": bench if isinstance(bench, dict) else {},
        "excess_pct": bench.get("excess_pct") if isinstance(bench, dict) else None,
        "ann_ir": bench.get("ann_ir") if isinstance(bench, dict) else None,
        "alpha_beta_legs": legs if isinstance(legs, dict) else {},
    }


def _shared_oos_backtest_kwargs(
    *,
    top_k: int,
    horizon_days: int,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """两臂共用：成本、权重模式、限额、剔 ST，避免对照口径偏袒。"""
    try:
        from core.strategy import backtest_portfolio_defaults

        d = backtest_portfolio_defaults()
    except Exception:
        d = {
            "weight_mode": "equal",
            "max_position_pct": 25.0,
            "max_sector_pct": 40.0,
            "exclude_st": True,
        }
    # OOS 对照固定 equal：相对 Δ 更干净；限额仍对齐 Spec
    return {
        "top_k": top_k,
        "horizon_days": horizon_days,
        "apply_costs": True,
        "fundamentals_by_code": fundamentals_by_code,
        "weight_mode": "equal",
        "max_position_pct": float(d.get("max_position_pct") or 25.0),
        "max_sector_pct": float(d.get("max_sector_pct") or 40.0),
        "apply_tau_buy_gate": False,  # 历史日线两臂都不套 live τ 闸
        "use_live_cluster_models": False,
    }


def _heuristic_oos_floor(min_score: float) -> float:
    """基线臂 0–100 门槛：显式参数优先，否则 tracks heuristic_buy_floor。"""
    try:
        ms = float(min_score)
    except (TypeError, ValueError):
        ms = 55.0
    if ms > 0:
        return ms
    try:
        from core.signal.rebalance_tracks import heuristic_floors

        buy, _ = heuristic_floors()
        return float(buy)
    except Exception:
        return 55.0


def _predicted_oos_floor() -> float:
    """研究臂 ŷ% 门槛：与生产 scoring.min_predicted_score 同源。"""
    try:
        from core.signal.score_display import resolve_buy_floor

        return float(resolve_buy_floor())
    except Exception:
        return 1.0


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

    shared = _shared_oos_backtest_kwargs(
        top_k=top_k,
        horizon_days=horizon_days,
        fundamentals_by_code=fundamentals_by_code,
    )
    floor = _heuristic_oos_floor(min_score)
    with signal_config_overlay({"weights": weights}):
        return backtest_topk_equal_weight(
            stock_bars,
            min_score=floor,
            rank_mode="heuristic_score",
            allow_heuristic_baseline=True,
            return_models_by_code={},
            min_predicted_score=None,
            **shared,
        )


def _run_topk_predicted(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    horizon_days: int,
    return_models_by_code: Optional[Dict[str, Any]],
    ridge_lambda: float = 0.0,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    min_predicted_score: Optional[float] = None,
) -> Dict[str, Any]:
    """研究臂：注入组/全局 return_model → predicted_score 排序。

    与基线臂共用限额/成本/剔 ST；ŷ 门槛用生产 buy floor（非 0）。
    关闭 ŷ_τ 买入闸（与基线对称：历史日线无可靠 τ）。
    """
    from core.backtest.topk_backtest import backtest_topk_equal_weight

    shared = _shared_oos_backtest_kwargs(
        top_k=top_k,
        horizon_days=horizon_days,
        fundamentals_by_code=fundamentals_by_code,
    )
    floor = (
        float(min_predicted_score)
        if min_predicted_score is not None
        else _predicted_oos_floor()
    )
    return backtest_topk_equal_weight(
        stock_bars,
        min_score=0.0,  # predicted 路径不用 0–100 门
        rank_mode="predicted_score",
        min_predicted_score=floor,
        return_models_by_code=return_models_by_code
        if return_models_by_code is not None
        else {},
        return_model_ridge_lambda=float(ridge_lambda or 0.0),
        allow_heuristic_baseline=False,
        **shared,
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
    require_clean_is_oos: bool = True,
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
    skipped = False
    reason = "insufficient_oos"
    delta_oos = None
    if base_oos is not None and res_oos is not None:
        delta_oos = round(float(res_oos) - float(base_oos), 2)
        res_failed = bool((research_m.get("oos") or {}).get("failed"))
        base_failed = bool((baseline_m.get("oos") or {}).get("failed"))
        gap_blocks = bool(require_clean_is_oos and res_failed and not base_failed)
        if delta_oos >= -tol and not gap_blocks:
            passed = True
            reason = "oos_not_worse"
            if res_failed and not base_failed:
                reason = "oos_not_worse_is_gap"
        elif delta_oos < -tol:
            reason = f"oos_worse_{delta_oos}pp"
        else:
            reason = (research_m.get("oos") or {}).get("fail_reason") or "research_oos_failed"
    else:
        # 无成交 / 曲线过短：样本不足，不算 α 失败
        res_oos_meta = research_m.get("oos") or {}
        skipped = True
        if int(research_m.get("trade_count") or 0) <= 0:
            reason = "research_no_trades"
        elif res_oos is None:
            reason = str(
                res_oos_meta.get("reason")
                or res_oos_meta.get("fail_reason")
                or "insufficient_oos"
            )
        elif base_oos is None:
            reason = "baseline_insufficient_oos"

    try:
        from core.research.bt_excess_attach import compare_arms_excess

        excess_cmp = compare_arms_excess(baseline_m, research_m)
    except Exception:
        excess_cmp = {
            "baseline_excess_pct": baseline_m.get("excess_pct"),
            "research_excess_pct": research_m.get("excess_pct"),
            "delta_excess_pp": None,
        }

    return {
        "ok": True,
        "passed": passed,
        "skipped": skipped,
        "reason": reason,
        "delta_oos_pp": delta_oos,
        "delta_excess_pp": excess_cmp.get("delta_excess_pp"),
        "excess_compare": excess_cmp,
        "oos_tol_pp": tol,
        "lookback": lookback,
        "top_k": top_k,
        "horizon_days": horizon_days,
        "stock_count": stock_count,
        "codes": codes,
        "baseline_rank_mode": "heuristic_score",
        "research_rank_mode": "predicted_score",
        "gate_symmetry": {
            "baseline_floor_scale": "heuristic_0_100",
            "research_floor_scale": "predicted_pct",
            "apply_tau_buy_gate": False,
            "weight_mode": "equal",
            "note": (
                "两臂同 top_k/成本/限额/equal；基线用 heuristic 门槛，"
                "研究臂用生产 ŷ 门槛；历史路径均关 τ 闸。"
            ),
        },
        "baseline": baseline_m,
        "research": research_m,
        "current": baseline_m,
        "suggested": research_m,
        "note": oos_semantics_note(oos_tol_pp=tol)
        + " 另附相对指数超额差（delta_excess_pp），与总收益 ΔOOS 分列。",
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
    respect_regime: bool = True,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    min_predicted_score: Optional[float] = None,
    rank_only: bool = False,
    require_clean_is_oos: bool = True,
) -> Dict[str, Any]:
    """heuristic 基线 vs predicted（研究模型）同一宇宙 Top-K OOS 对照。

    B5：``respect_regime=True``（默认）表示研究臂模型应按 regime 对齐因子集拟合；
    本函数消费已拟合的 return_model，并在结果中戳记该约定。

    ``stock_bars`` 若已由上游分组流水线拉好，直接复用，避免每组重复 IO。
    ``rank_only``：两臂都不设买入门槛，只比组内 Top-K 排序（组 OOS 用；
    避免生产 ŷ≥1% 把研究臂买成 0 笔再记失败）。
    ``require_clean_is_oos``：False 时，ŷ 相对 heuristic 已过门则不再被
    「自身 IS≫OOS」旗标否决（小组 Top-K 曲线噪声大）。
    """
    from core.signal.config import load_signal_config
    from core.watching_store import read_watching
    from core.research.portfolio_bars import load_portfolio_stock_bars

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

    failures: List[str] = []
    fund_map: Dict[str, dict] = dict(fundamentals_by_code or {})
    if stock_bars:
        need = max(16, min(40, int(lookback or 90) // 2))
        filtered: Dict[str, List[dict]] = {}
        for raw in use_codes:
            bars = stock_bars.get(raw) or stock_bars.get(str(raw))
            if bars and len(bars) >= need:
                filtered[str(raw)] = bars
            elif bars:
                failures.append(f"{raw}(日线{len(bars)}<{need})")
            else:
                failures.append(str(raw))
        stock_bars = filtered
    else:
        stock_bars, failures, loaded_fund = load_portfolio_stock_bars(
            use_codes,
            lookback=lookback,
            fetch_fundamentals=False,
        )
        if loaded_fund:
            fund_map.update(loaded_fund)
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

    heur_floor = 0.0 if rank_only else min_score
    pred_floor = -999.0 if rank_only else min_predicted_score
    base_bt = _run_topk_heuristic(
        stock_bars,
        base_w,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        min_score=heur_floor,
        fundamentals_by_code=fund_map or None,
    )
    res_bt = _run_topk_predicted(
        stock_bars,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        return_models_by_code=models_eff,
        ridge_lambda=ridge_lambda,
        fundamentals_by_code=fund_map or None,
        min_predicted_score=pred_floor,
    )
    out = _compare_arms(
        _metrics_from_backtest(base_bt, stock_bars=stock_bars),
        _metrics_from_backtest(res_bt, stock_bars=stock_bars),
        tol=float(oos_tol_pp),
        lookback=lookback,
        top_k=top_k_eff,
        horizon_days=horizon_days,
        stock_count=len(stock_bars),
        codes=list(stock_bars.keys()),
        require_clean_is_oos=bool(require_clean_is_oos) and not rank_only,
    )
    out["respect_regime"] = bool(respect_regime)
    if out.get("note"):
        out["note"] = (
            str(out["note"])
            + (
                " 研究臂约定 respect_regime（与 live enabled_factors 对齐）。"
                if respect_regime
                else " 研究臂为全因子模型（未强制 regime）。"
            )
        )
    return out


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
    respect_regime: bool = True,
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
            respect_regime=respect_regime,
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
