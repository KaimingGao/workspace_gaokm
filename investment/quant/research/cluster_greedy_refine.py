"""轻量因果贪心换组：定组后按 holdout partition_loss 试换票。

与 ``objective_partition.greedy_swap_optimize`` 不同：
- 用尾段 ŷ holdout（前段重拟合），不用全样本 |IC|
- 轮次 / 评估次数有硬上限；大宇宙跳过
- 只在已有组之间换，不新建组

交付标签可被接受的 swap 改写；组池 return_model 仍由后续全样本路径重估。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.research.objective_partition import labels_to_group_indices
from quant.research.partition_loss import compute_partition_loss


def _refit_fn_factory(
    *,
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
):
    from core.research.factor_ols_fit import fit_factor_ols_from_panel
    from quant.research.cluster_weight_display import _return_model_from_ols

    def _refit(train_xs: List[Dict[str, Any]], train_ys: List[float]):
        if len(train_xs) < 4 or len(train_ys) < 4:
            return None
        pooled = fit_factor_ols_from_panel(
            train_xs,
            train_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=bool(use_pit),
            mode="watching_pooled",
            ridge_lambda=float(ridge_lambda),
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy or "drop_redundant"),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
        )
        return _return_model_from_ols(pooled)

    return _refit


def _group_holdout_metrics(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float,
    cut_date: Optional[str],
    refit_fn,
) -> Dict[str, Any]:
    from quant.research.partition_loss import yhat_group_holdout_metrics

    n_mem = len([m for m in members if str(m).strip()])
    if n_mem < 2:
        return {
            "member_count": n_mem,
            "pooled_r2": None,
            "ic_mean": 0.0,
        }
    hold = yhat_group_holdout_metrics(
        members,
        panel_by_code,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
        refit_fn=refit_fn,
    )
    hold_ok = bool(hold.get("ok") and hold.get("refit"))
    r2 = hold.get("r2") if hold_ok else None
    ic_v = hold.get("ic") if hold_ok else None
    try:
        ic_f = float(ic_v) if ic_v is not None else 0.0
    except (TypeError, ValueError):
        ic_f = 0.0
    return {
        "member_count": n_mem,
        "pooled_r2": None if r2 is None else float(r2),
        "ic_mean": float(ic_f),
    }


def evaluate_labels_holdout_loss(
    labels: Sequence[int],
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    use_pit: bool = True,
    select_ridge: bool = False,
    collinearity_policy: str = "drop_redundant",
    respect_regime: bool = True,
    y_spec: Optional[Dict[str, Any]] = None,
    cache: Optional[Dict[Tuple[str, ...], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """labels → 各组 holdout 指标 → partition_loss（有符号 IC）。"""
    refit_fn = _refit_fn_factory(
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=dict(y_spec or {}),
    )
    groups_idx = labels_to_group_indices(labels)
    groups_metrics: List[Dict[str, Any]] = []
    local_cache: Dict[Tuple[str, ...], Dict[str, Any]] = (
        cache if cache is not None else {}
    )
    for cid, idxs in sorted(groups_idx.items()):
        members = tuple(
            sorted(str(codes[i]).strip() for i in idxs if str(codes[i]).strip())
        )
        key = members
        if key in local_cache:
            metrics = dict(local_cache[key])
            metrics["cluster_id"] = int(cid)
        else:
            metrics = _group_holdout_metrics(
                members,
                panel_by_code,
                holdout_ratio=holdout_ratio,
                cut_date=cut_date,
                refit_fn=refit_fn,
            )
            metrics["cluster_id"] = int(cid)
            metrics["members"] = list(members)
            local_cache[key] = {
                "member_count": metrics["member_count"],
                "pooled_r2": metrics["pooled_r2"],
                "ic_mean": metrics["ic_mean"],
                "members": list(members),
            }
        groups_metrics.append(metrics)
    loss_info = compute_partition_loss(
        groups=groups_metrics,
        w_r2=1.0,
        w_ic=0.5,
        lambda_imbalance=0.3,
        lambda_singleton=1.5,
        ic_use_abs=False,
    )
    return {"groups": groups_metrics, "cache": local_cache, **loss_info}


def light_greedy_swap_refine(
    labels_init: Sequence[int],
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    use_pit: bool = True,
    select_ridge: bool = False,
    collinearity_policy: str = "drop_redundant",
    respect_regime: bool = True,
    y_spec: Optional[Dict[str, Any]] = None,
    max_rounds: int = 2,
    max_evals: int = 80,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """有限轮贪心换组。返回 (labels, diagnostics)。"""
    best = np.asarray(labels_init, dtype=int).copy()
    n = int(best.shape[0])
    empty = {
        "ok": False,
        "n_swaps": 0,
        "n_evals": 0,
        "loss_before": None,
        "loss_after": None,
        "swaps": [],
        "reason": None,
    }
    if n < 4:
        return best, {**empty, "reason": "too_few_codes"}
    uniq0 = sorted({int(v) for v in best if int(v) >= 0})
    if len(uniq0) < 2:
        return best, {**empty, "reason": "need_two_clusters"}

    cache: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    base = evaluate_labels_holdout_loss(
        best,
        codes,
        panel_by_code,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        cache=cache,
    )
    best_loss = float(base.get("loss") or 1e9)
    loss_before = best_loss
    swaps: List[Dict[str, Any]] = []
    n_evals = 0
    max_rounds_i = max(1, min(int(max_rounds or 2), 4))
    max_evals_i = max(10, min(int(max_evals or 80), 200))

    for rnd in range(max_rounds_i):
        moved = False
        # 优先试多票组内的票（换走更可能改善异质）
        order = list(range(n))
        multi = {
            int(cid)
            for cid, idxs in labels_to_group_indices(best).items()
            if len(idxs) >= 2
        }
        order.sort(
            key=lambda i: (
                0 if int(best[i]) in multi else 1,
                int(best[i]),
                i,
            )
        )
        for i in order:
            if n_evals >= max_evals_i:
                break
            own = int(best[i])
            if own < 0:
                continue
            uniq = sorted({int(v) for v in best if int(v) >= 0})
            if len(uniq) < 2:
                break
            # 不允许掏空多票组到 0（允许降到 1=单票）
            own_size = int(sum(1 for v in best if int(v) == own))
            for other in uniq:
                if other == own:
                    continue
                if n_evals >= max_evals_i:
                    break
                if own_size <= 1:
                    # 单票组整票并入它组：允许
                    pass
                trial = best.copy()
                trial[i] = int(other)
                n_evals += 1
                # 缓存按 (cid, members) —— cid 不变但 members 变，旧缓存仍可复用未动组
                ev = evaluate_labels_holdout_loss(
                    trial,
                    codes,
                    panel_by_code,
                    holdout_ratio=holdout_ratio,
                    cut_date=cut_date,
                    horizon_days=horizon_days,
                    ridge_lambda=ridge_lambda,
                    use_pit=use_pit,
                    select_ridge=select_ridge,
                    collinearity_policy=collinearity_policy,
                    respect_regime=respect_regime,
                    y_spec=y_spec,
                    cache=cache,
                )
                trial_loss = float(ev.get("loss") or 1e9)
                if trial_loss + 1e-6 < best_loss:
                    swaps.append(
                        {
                            "round": int(rnd + 1),
                            "index": int(i),
                            "code": str(codes[i]),
                            "from": int(own),
                            "to": int(other),
                            "loss_before": round(best_loss, 6),
                            "loss_after": round(trial_loss, 6),
                            "delta": round(trial_loss - best_loss, 6),
                        }
                    )
                    best = trial
                    best_loss = trial_loss
                    moved = True
                    break
            if moved:
                break
        if not moved or n_evals >= max_evals_i:
            break

    return best, {
        "ok": True,
        "n_swaps": len(swaps),
        "n_evals": int(n_evals),
        "loss_before": round(float(loss_before), 6),
        "loss_after": round(float(best_loss), 6),
        "improved": bool(best_loss + 1e-9 < loss_before),
        "swaps": swaps,
        "max_rounds": max_rounds_i,
        "max_evals": max_evals_i,
        "cut_date": str(cut_date or "").strip()[:10] or None,
        "note": (
            "holdout partition_loss 贪心换组（有符号 IC）；"
            "接受的 swap 改写交付标签，组池仍全样本重估。"
        ),
    }
