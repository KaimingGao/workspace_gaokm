"""权重坐标网格搜索：直接优化样本内 Top-K 收益，作 OLS/IC 建议对照（研究只读）。

不写 ``signal_config``；``promote_ready`` 恒为 false。
搜索目标用权益曲线前段（IS）收益，终评并列报告 IS / OOS。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.signal.config import load_signal_config
from core.signal.weight_oos_gate import _metrics_from_backtest, _run_topk_with_weights
from core.signal.weight_suggest import apply_group_caps


DEFAULT_GRID = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)


def _round_norm(weights: Dict[str, float], keys: Sequence[str]) -> Dict[str, float]:
    w = {k: max(0.0, float(weights.get(k) or 0.0)) for k in keys}
    total = sum(w.values())
    if total <= 1e-12:
        eq = 1.0 / max(1, len(keys))
        return {k: round(eq, 4) for k in keys}
    out = {k: round(v / total, 4) for k, v in w.items()}
    drift = round(1.0 - sum(out.values()), 4)
    if abs(drift) >= 0.0001 and keys:
        top = max(keys, key=lambda k: out[k])
        out[top] = round(out[top] + drift, 4)
    return out


def set_coordinate_weight(
    weights: Dict[str, float],
    key: str,
    value: float,
    *,
    free_keys: Sequence[str],
    frozen_keys: Sequence[str],
    all_keys: Sequence[str],
) -> Dict[str, float]:
    """固定 ``key=value``，其余自由因子按相对比例瓜分剩余权重；冻结项保持 0。"""
    free = [k for k in free_keys if k in all_keys]
    frozen = {k for k in frozen_keys if k in all_keys}
    if key not in free:
        return _round_norm(weights, all_keys)

    v = max(0.0, min(0.95, float(value)))
    base = {k: float(weights.get(k) or 0.0) for k in all_keys}
    for k in frozen:
        base[k] = 0.0
    others = [k for k in free if k != key]
    base[key] = v
    rest = max(0.0, 1.0 - v)
    other_sum = sum(max(0.0, base[k]) for k in others)
    if not others:
        base[key] = 1.0
    elif other_sum <= 1e-12:
        eq = rest / len(others)
        for k in others:
            base[k] = eq
    else:
        scale = rest / other_sum
        for k in others:
            base[k] = max(0.0, base[k]) * scale
    for k in frozen:
        base[k] = 0.0
    return _round_norm(base, all_keys)


def _is_objective(metrics: Dict[str, Any]) -> float:
    oos = metrics.get("oos") or {}
    is_ret = oos.get("is_return_pct")
    if is_ret is not None:
        return float(is_ret)
    tr = metrics.get("total_return_pct")
    if tr is not None:
        return float(tr)
    return -1e18


def _arm_payload(
    label: str,
    weights: Dict[str, float],
    metrics: Dict[str, Any],
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    oos = metrics.get("oos") or {}
    row = {
        "label": label,
        "weights": dict(weights),
        "success": bool(metrics.get("success")),
        "error": metrics.get("error"),
        "total_return_pct": metrics.get("total_return_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "trade_count": metrics.get("trade_count"),
        "is_return_pct": oos.get("is_return_pct"),
        "oos_return_pct": oos.get("oos_return_pct"),
        "oos_failed": bool(oos.get("failed")),
        "oos_fail_reason": oos.get("fail_reason"),
        "search_objective_is_pct": round(_is_objective(metrics), 4)
        if metrics.get("success")
        else None,
    }
    if extra:
        row.update(extra)
    return row


def evaluate_weights_on_bars(
    stock_bars: Dict[str, List[dict]],
    weights: Dict[str, float],
    *,
    top_k: int,
    horizon_days: int,
    min_score: float,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    bt = _run_topk_with_weights(
        stock_bars,
        weights,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fundamentals_by_code,
    )
    return _metrics_from_backtest(bt)


def coordinate_grid_search(
    stock_bars: Dict[str, List[dict]],
    base_weights: Dict[str, float],
    *,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    grid: Optional[Sequence[float]] = None,
    n_sweeps: int = 2,
    max_evals: int = 60,
    freeze_zero_weights: bool = True,
    apply_caps: bool = True,
    max_group_share: float = 0.45,
) -> Dict[str, Any]:
    """一维坐标网格：每次改一个自由因子，按 IS 收益选优。"""
    cfg = load_signal_config()
    keys = list(base_weights.keys()) or list((cfg.get("weights") or {}).keys())
    if not keys:
        return {"success": False, "error": "无权重键", "evals": 0}

    base = _round_norm(base_weights, keys)
    frozen = [
        k
        for k in keys
        if freeze_zero_weights and abs(float(base.get(k) or 0.0)) < 1e-12
    ]
    free = [k for k in keys if k not in frozen]
    if len(free) < 2:
        return {
            "success": False,
            "error": "自由因子不足 2 个，无法坐标搜索",
            "evals": 0,
            "frozen_keys": frozen,
        }

    grid_vals = [float(x) for x in (grid or DEFAULT_GRID)]
    sweeps = max(1, min(int(n_sweeps or 1), 5))
    budget = max(5, min(int(max_evals or 60), 200))

    def _polish(w: Dict[str, float]) -> Tuple[Dict[str, float], List[str]]:
        polished = dict(w)
        warns: List[str] = []
        if apply_caps:
            groups = cfg.get("factor_groups") or {}
            polished, warns = apply_group_caps(
                polished, groups, max_group_share=max_group_share
            )
            polished = _round_norm(polished, keys)
            for k in frozen:
                polished[k] = 0.0
            polished = _round_norm(polished, keys)
        return polished, warns

    best, cap_warns = _polish(base)
    best_m = evaluate_weights_on_bars(
        stock_bars,
        best,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fundamentals_by_code,
    )
    best_score = _is_objective(best_m)
    evals = 1
    history: List[Dict[str, Any]] = [
        {
            "eval": 1,
            "factor": None,
            "trial": None,
            "is_return_pct": best_m.get("oos", {}).get("is_return_pct")
            if isinstance(best_m.get("oos"), dict)
            else None,
            "accepted": True,
            "note": "seed",
        }
    ]

    for sweep in range(sweeps):
        improved = False
        for fac in free:
            if evals >= budget:
                break
            for trial in grid_vals:
                if evals >= budget:
                    break
                cand0 = set_coordinate_weight(
                    best,
                    fac,
                    trial,
                    free_keys=free,
                    frozen_keys=frozen,
                    all_keys=keys,
                )
                cand, warns = _polish(cand0)
                m = evaluate_weights_on_bars(
                    stock_bars,
                    cand,
                    top_k=top_k,
                    horizon_days=horizon_days,
                    min_score=min_score,
                    fundamentals_by_code=fundamentals_by_code,
                )
                evals += 1
                score = _is_objective(m)
                accepted = bool(m.get("success")) and score > best_score + 1e-9
                history.append(
                    {
                        "eval": evals,
                        "sweep": sweep,
                        "factor": fac,
                        "trial": trial,
                        "is_return_pct": (m.get("oos") or {}).get("is_return_pct"),
                        "accepted": accepted,
                    }
                )
                if accepted:
                    best = cand
                    best_m = m
                    best_score = score
                    cap_warns = warns
                    improved = True
        if not improved or evals >= budget:
            break

    return {
        "success": True,
        "weights": best,
        "metrics": best_m,
        "search_objective": "is_return_pct",
        "best_is_return_pct": round(best_score, 4),
        "evals": evals,
        "max_evals": budget,
        "n_sweeps": sweeps,
        "grid": list(grid_vals),
        "free_keys": free,
        "frozen_keys": frozen,
        "constraint_warnings": cap_warns,
        "history_tail": history[-12:],
        "promote_ready": False,
        "note": (
            "坐标网格在样本内（权益前段）收益上选优；终评 IS/OOS 并列。"
            "不写 signal_config；promote_ready=false。"
        ),
    }


def compare_weight_arms(
    *,
    codes: Optional[List[str]] = None,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    watching_limit: int = 10,
    ols_suggested_weights: Optional[Dict[str, float]] = None,
    ols_arm_meta: Optional[Dict[str, Any]] = None,
    n_sweeps: int = 2,
    max_evals: int = 48,
    grid: Optional[Sequence[float]] = None,
    start_from: str = "current",
) -> Dict[str, Any]:
    """三臂对照：全局权 / OLS·IC 建议权（可选）/ 坐标搜索权（同一宇宙、同一 bars）。

    ``ols_suggested_weights`` 由上层注入（避免本模块依赖 QuantService）。
    """
    from quant.research.portfolio_data import load_portfolio_stock_bars
    from core.watching_store import read_watching

    if codes:
        use_codes = [str(c).strip() for c in codes if str(c).strip()]
    else:
        uni = read_watching()
        use_codes = list(uni.get("watchlist") or [])
    limit = max(3, min(int(watching_limit or 10), 40))
    use_codes = use_codes[:limit]
    if len(use_codes) < 3:
        return {
            "success": False,
            "error": "有效标的不足 3 只",
            "stock_count": len(use_codes),
            "promote_ready": False,
        }

    stock_bars, failures, fund_map = load_portfolio_stock_bars(
        use_codes,
        lookback=lookback,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 3:
        return {
            "success": False,
            "error": f"有效日线不足 3 只（失败 {len(failures)}）",
            "failures": failures[:8],
            "stock_count": len(stock_bars),
            "promote_ready": False,
        }

    cfg = load_signal_config()
    current = dict(cfg.get("weights") or {})
    if not current:
        return {"success": False, "error": "无当前权重配置", "promote_ready": False}

    top_k_eff = max(1, min(int(top_k or 1), len(stock_bars)))
    fund = fund_map or None
    common = dict(
        top_k=top_k_eff,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fund,
    )

    arms: List[Dict[str, Any]] = []
    cur_m = evaluate_weights_on_bars(stock_bars, current, **common)
    arms.append(_arm_payload("global", current, cur_m))

    ols_w: Optional[Dict[str, float]] = None
    if ols_suggested_weights:
        ols_w = _round_norm(ols_suggested_weights, list(current.keys()))
        ols_m = evaluate_weights_on_bars(stock_bars, ols_w, **common)
        arms.append(
            _arm_payload(
                "ols_ic_suggest",
                ols_w,
                ols_m,
                extra=dict(ols_arm_meta or {}),
            )
        )
    else:
        arms.append(
            {
                "label": "ols_ic_suggest",
                "success": False,
                "skipped": True,
                "error": "未注入建议权重（include_ols_arm=false 或 suggest 失败）",
                "weights": None,
            }
        )

    seed = current
    start = str(start_from or "current").strip().lower()
    if start in ("equal", "uniform"):
        free_n = sum(1 for v in current.values() if abs(float(v or 0)) > 1e-12)
        free_n = max(1, free_n)
        seed = {
            k: (0.0 if abs(float(v or 0)) < 1e-12 else round(1.0 / free_n, 4))
            for k, v in current.items()
        }
        seed = _round_norm(seed, list(current.keys()))
    elif start in ("suggested", "ols", "ols_ic") and ols_w:
        seed = ols_w

    search = coordinate_grid_search(
        stock_bars,
        seed,
        grid=grid,
        n_sweeps=n_sweeps,
        max_evals=max_evals,
        **common,
    )
    if search.get("success"):
        arms.append(
            _arm_payload(
                "coordinate_search",
                search["weights"],
                search["metrics"],
                extra={
                    "evals": search.get("evals"),
                    "best_is_return_pct": search.get("best_is_return_pct"),
                    "frozen_keys": search.get("frozen_keys"),
                    "constraint_warnings": search.get("constraint_warnings"),
                },
            )
        )
    else:
        arms.append(
            {
                "label": "coordinate_search",
                "success": False,
                "error": search.get("error"),
                "weights": None,
            }
        )

    # 按 OOS（缺则 total）粗排，仅供阅读，不表示可 promote
    def _rank_key(a: Dict[str, Any]) -> float:
        if not a.get("success"):
            return -1e18
        if a.get("oos_return_pct") is not None:
            return float(a["oos_return_pct"])
        if a.get("total_return_pct") is not None:
            return float(a["total_return_pct"])
        return -1e18

    ranked = sorted(
        [a for a in arms if a.get("success")],
        key=_rank_key,
        reverse=True,
    )
    best_label = ranked[0]["label"] if ranked else None

    return {
        "success": True,
        "task": "weight_coord_compare",
        "promote_ready": False,
        "lookback": lookback,
        "top_k": top_k_eff,
        "horizon_days": horizon_days,
        "min_score": min_score,
        "stock_count": len(stock_bars),
        "codes": list(stock_bars.keys()),
        "failures": failures[:8],
        "start_from": start,
        "arms": arms,
        "best_by_oos_label": best_label,
        "search": {
            "evals": search.get("evals"),
            "max_evals": search.get("max_evals"),
            "n_sweeps": search.get("n_sweeps"),
            "grid": search.get("grid"),
            "search_objective": search.get("search_objective"),
            "history_tail": search.get("history_tail"),
            "note": search.get("note"),
        }
        if search.get("success")
        else {"error": search.get("error")},
        "note": (
            "对照臂：global（signal_config）/ ols_ic_suggest（IC·OLS 小步）/"
            "coordinate_search（样本内坐标网格）。"
            "不写 signal_config；勿把 best_by_oos 当晋升依据。"
        ),
    }
