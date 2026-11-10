"""研究 OOS 门禁：predicted_score（ŷ）单臂相对「平坦 0%」对照（只读）。

产品定位：
- 研究臂：研究枢纽回归模型 → predicted_score（ŷ）
- 隐含基线：OOS 收益相对 0%（不持仓/现金），不再跑 heuristic 规则分臂
- 目的：样本外 ŷ 曲线可评估且不显著劣于平坦；过门 ≠ 自动 promote
"""

from typing import Any, Dict, List, Optional

# 产品语义（UI / API note 同源）
OOS_PRODUCT_SEMANTICS = (
    "产品语义：predicted_score（研究/执行套 ŷ_oo LightGBM）= 选股实现方案；"
    "OOS 对照隐含基线为平坦 0%（已移除 heuristic 规则分臂）。"
    "固定注入启用套打分器，不滚动拟 Ridge；过门≠自动 promote。"
)

# 合成基线：不跑第二套回测，只作 ΔOOS 对照零点
_FLAT_BASELINE_METRICS: Dict[str, Any] = {
    "success": True,
    "error": None,
    "total_return_pct": 0.0,
    "max_drawdown_pct": 0.0,
    "win_rate_pct": None,
    "trade_count": 1,
    "oos": {"oos_return_pct": 0.0, "failed": False},
    "rank_mode": "flat_zero",
    "benchmark": {},
    "excess_pct": None,
    "ann_ir": None,
    "alpha_beta_legs": {},
}


def oos_semantics_note(*, oos_tol_pp: float = 1.0) -> str:
    """门禁 note：切分规则 + 过门条件 + 产品语义。"""
    tol = float(oos_tol_pp)
    return (
        "同一研究池轻量 Top-K 曲线（等权硬 TopK）：权益曲线后 30% 为 OOS。"
        f"过门条件：研究臂（ŷ）OOS ≥ 0% − {tol}pp，"
        "且不新增 OOS 失败旗标。"
        + " "
        + OOS_PRODUCT_SEMANTICS
    )


def _metrics_from_backtest(
    bt: Dict[str, Any],
    *,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    attach_excess: bool = True,
) -> Dict[str, Any]:
    from core.backtest.oos_report import split_oos_summary

    enriched = bt
    if attach_excess:
        try:
            from core.research.bt_excess_attach import attach_benchmark_excess

            enriched = attach_benchmark_excess(bt, stock_bars or {})
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
    """研究臂：成本、权重模式、限额、剔 ST。"""
    try:
        from core.strategy import backtest_portfolio_defaults

        d = backtest_portfolio_defaults()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        from core.risk.limits import DEFAULT_RISK_LIMITS

        d = {
            "weight_mode": "equal",
            "max_position_pct": float(DEFAULT_RISK_LIMITS["max_position_pct"]),
            "max_sector_pct": float(DEFAULT_RISK_LIMITS["max_sector_pct"]),
            "exclude_st": True,
        }
    _ = d, fundamentals_by_code
    return {
        "top_k": top_k,
        "horizon_days": horizon_days,
        "neutralize": False,
        "apply_tau_buy_gate": False,
        "weight_mode": "equal",
    }


def _clip_stock_bars_lookback(
    stock_bars: Dict[str, List[dict]],
    lookback: int,
) -> Dict[str, List[dict]]:
    keep_n = max(40, min(int(lookback or 80), 80))
    return {
        str(c): list(b)[-keep_n:]
        for c, b in (stock_bars or {}).items()
        if b
    }


def _predicted_oos_floor() -> float:
    """研究臂 ŷ% 门槛：与生产 scoring.min_predicted_score 同源。"""
    try:
        from core.signal.score_display import resolve_buy_floor

        return float(resolve_buy_floor())
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return 1.0


def _run_topk_predicted(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    horizon_days: int,
    return_model: Optional[Any] = None,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    min_predicted_score: Optional[float] = None,
    progress_cb: Optional[Any] = None,
    cancel_check: Optional[Any] = None,
    neutralize: Optional[bool] = None,
) -> Dict[str, Any]:
    """研究臂：固定注入 return_model（ŷ_oo LightGBM）→ predicted_score 排序。"""
    from core.backtest.topk_probe import run_oos_topk_curve

    shared = _shared_oos_backtest_kwargs(
        top_k=top_k,
        horizon_days=horizon_days,
        fundamentals_by_code=fundamentals_by_code,
    )
    if neutralize is not None:
        shared["neutralize"] = bool(neutralize)
    floor = (
        float(min_predicted_score)
        if min_predicted_score is not None
        else _predicted_oos_floor()
    )
    return run_oos_topk_curve(
        stock_bars,
        min_score=0.0,
        rank_mode="predicted_score",
        min_predicted_score=floor,
        return_model=return_model,
        freeze_return_model=True,
        allow_heuristic_baseline=False,
        progress_cb=progress_cb,
        cancel_check=cancel_check,
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
            "stock_count": stock_count,
            "codes": codes,
            "note": "轻量 Top-K 曲线失败，跳过门禁判定。",
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
        "baseline_rank_mode": "flat_zero",
        "research_rank_mode": "predicted_score",
        "gate_symmetry": {
            "baseline_floor_scale": "flat_zero",
            "research_floor_scale": "predicted_pct",
            "apply_tau_buy_gate": False,
            "weight_mode": "equal",
            "note": (
                "研究臂轻量 Top-K；隐含基线 OOS=0%（已移除 heuristic 臂）；"
                "历史路径关 τ 闸。"
            ),
        },
        "baseline": baseline_m,
        "research": research_m,
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
    progress_cb: Optional[Any] = None,
    cancel_check: Optional[Any] = None,
    attach_excess: bool = True,
    fast_gate: bool = False,
) -> Dict[str, Any]:
    """ŷ 研究臂 Top-K OOS，相对平坦 0% 过门。

    ``baseline_weights`` / ``min_score`` / ``ridge_lambda`` 已忽略（兼容旧调用方；
    打分器由 ``research_models_by_code`` 注入，冻结 LightGBM）。
    B5：``respect_regime`` 仅戳记约定（模型由上游拟合）。
    """
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.signal.config import signal_config_overlay
    from core.watching.store import read_watching

    _ = baseline_weights, min_score, ridge_lambda

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

    top_k_eff = max(1, min(int(top_k or 1), len(stock_bars)))
    models_eff = {
        str(c): models[str(c)]
        for c in stock_bars
        if str(c) in models
    }
    if not models_eff:
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

    pred_floor = -999.0 if rank_only else min_predicted_score
    neut = False if fast_gate else None
    if fast_gate:
        stock_bars = _clip_stock_bars_lookback(stock_bars, lookback)

    def _emit(msg: str, cur: int = 0, tot: int = 1) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(msg, int(cur or 0), int(tot or 1))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass

    def _arm_cb(arm: str):
        def _cb(msg: str, cur: int = 0, tot: int = 0) -> None:
            inner = str(msg or "").strip()
            _emit(f"{arm} · {inner}" if inner else arm, cur, tot)

        return _cb

    overlay = (
        {
            "fundamentals": {"use_in_backtest": False},
            "cross_section": {"neutralize": False},
            "weights": {"tail_anomaly": 0.0},
            "dual_score": {"enable_minute_tau": False},
        }
        if fast_gate
        else None
    )
    with signal_config_overlay(overlay):
        _emit("研究臂", 0, 1)
        # 同池共用一份全局 ŷ（研究套 LightGBM）；不再按 code 分裂 / 滚动 Ridge
        global_model = next(iter(models_eff.values()), None)
        res_bt = _run_topk_predicted(
            stock_bars,
            top_k=top_k_eff,
            horizon_days=horizon_days,
            return_model=global_model,
            fundamentals_by_code=fund_map or None,
            min_predicted_score=pred_floor,
            progress_cb=_arm_cb("研究臂"),
            cancel_check=cancel_check,
            neutralize=neut,
        )
        if cancel_check:
            try:
                if cancel_check():
                    return {
                        "ok": False,
                        "passed": False,
                        "skipped": True,
                        "reason": "cancelled",
                        "note": "已取消",
                        "stock_count": len(stock_bars),
                    }
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                pass

    out = _compare_arms(
        dict(_FLAT_BASELINE_METRICS),
        _metrics_from_backtest(
            res_bt, stock_bars=stock_bars, attach_excess=bool(attach_excess)
        ),
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


# 权重补丁与 ŷ_oo LightGBM 正交：weights 不进 predicted_score（入模靠 feature_names）
YHAT_WEIGHTS_DECOUPLED = "yhat_weights_decoupled"


def evaluate_weight_suggestion_oos(
    current_weights: Optional[Dict[str, float]] = None,
    suggested_weights: Optional[Dict[str, float]] = None,
    **_ignored: Any,
) -> Dict[str, Any]:
    """权重建议门禁：ŷ_oo 已切 LightGBM 后不再用 TopK ŷ 曲线审批 weights 补丁。

    ``signal_config.weights`` 不进入 predicted_score；请看 IC / 因子健康后人审合并。
    若仍要验 ŷ 曲线，请走 ``evaluate_research_oos``（注入研究套 LightGBM）。
    旧调用方 kwargs（lookback / ridge_lambda 等）一律忽略。
    """
    _ = current_weights, suggested_weights
    tol = _ignored.get("oos_tol_pp", 1.0)
    try:
        tol_f = float(tol)
    except (TypeError, ValueError):
        tol_f = 1.0
    return {
        "ok": True,
        "passed": True,
        "skipped": True,
        "reason": YHAT_WEIGHTS_DECOUPLED,
        "note": (
            "ŷ_oo 正式路径为 LightGBM；signal_config.weights 不进入 predicted_score，"
            "TopK ŷ 曲线不能审批权重补丁。请依据 IC/健康后人审合并。"
        ),
        "oos_tol_pp": tol_f,
    }
