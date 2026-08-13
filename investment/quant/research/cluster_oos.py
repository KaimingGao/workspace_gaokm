"""β 分组：组内 OOS 对照 + 组权 diff 导出（只读，不写盘）。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

ProgressCb = Optional[Callable[[str, int, int], None]]


def score_cluster_partition_oos(
    clusters: List[Dict[str, Any]],
    *,
    lookback: int = 80,
    horizon_days: int = 3,
    oos_tol_pp: float = 1.0,
    respect_regime: bool = True,
    bars_by_code: Optional[Dict[str, List[dict]]] = None,
    panel_by_code: Optional[Dict[str, Dict[str, Any]]] = None,
    silhouette: Optional[float] = None,
    w_r2: float = 1.0,
    w_ic: float = 1.0,
    holdout_ratio: float = 0.3,
    ridge_lambda: float = 0.0,
    select_ridge: bool = False,
    collinearity_policy: str = "drop_redundant",
    y_spec: Optional[Dict[str, Any]] = None,
    use_pit: bool = True,
    cut_date: Optional[str] = None,
) -> Dict[str, Any]:
    """组级评分，供 auto-k 邻域选优（不写 config_diff）。

    主目标（对齐产品）：组 β→ŷ **尾段有符号 IC↑ / 前段重拟合误差↓**（``partition_loss``）。
    有 ``cut_date`` 时用宇宙日历切分；否则每票比例切尾。
    尾段评估前默认在前段重拟合 β（避免全样本泄漏）。ŷ IC 用**有符号**相关。
    辅门禁：heuristic vs ŷ 的 ΔOOS 通过组数 / 均 ΔOOS；silhouette 打破平局。

    排序键（越大越好）：``(-loss, passed, mean_ΔOOS, sil)``。
    """
    from core.research.factor_ols_fit import fit_factor_ols_from_panel
    from core.signal.weight_oos_gate import evaluate_research_oos
    from core.signal.config import load_signal_config
    from quant.research.cluster_weight_display import _return_model_from_ols
    from quant.research.partition_loss import (
        DEFAULT_W_IC,
        DEFAULT_W_R2,
        compute_partition_loss,
        yhat_group_holdout_metrics,
    )

    regime_aligned = bool(respect_regime)
    lb = max(40, min(int(lookback or 80), 90))
    base_w = dict((load_signal_config() or {}).get("weights") or {})
    passed_n = 0
    failed_n = 0
    skipped_n = 0
    deltas: List[float] = []
    groups_for_loss: List[Dict[str, Any]] = []
    ics: List[float] = []
    r2s: List[float] = []
    rmses: List[float] = []
    lam = float(ridge_lambda or 0.0)

    def _refit_train(train_xs: List[Dict[str, Any]], train_ys: List[float]):
        # 与 collect_group_chrono_holdout 的 n_train 下限对齐（≥4），避免无谓「样本不足」却退回全样本
        if len(train_xs) < 4 or len(train_ys) < 4:
            return None
        pooled = fit_factor_ols_from_panel(
            train_xs,
            train_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=bool(use_pit),
            mode="watching_pooled",
            ridge_lambda=lam,
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy or "drop_redundant"),
            respect_regime=regime_aligned,
            y_spec=dict(y_spec or {}),
        )
        return _return_model_from_ols(pooled)

    for cl in clusters or []:
        members = sorted(
            str(c).strip() for c in (cl.get("members") or []) if str(c).strip()
        )
        rm = cl.get("return_model")
        n_mem = len(members)
        if cl.get("singleton") or n_mem < 2:
            skipped_n += 1
            groups_for_loss.append(
                {
                    "member_count": n_mem,
                    "pooled_r2": None,
                    "ic_mean": 0.0,
                }
            )
            continue
        if not isinstance(rm, dict) or not rm.get("coefficients"):
            skipped_n += 1
            groups_for_loss.append(
                {
                    "member_count": n_mem,
                    "pooled_r2": None,
                    "ic_mean": 0.0,
                    "has_return_model": False,
                    "fit_ok": False,
                }
            )
            continue

        if panel_by_code:
            hold = yhat_group_holdout_metrics(
                members,
                panel_by_code,
                holdout_ratio=holdout_ratio,
                cut_date=cut_date,
                return_model=rm,
                refit_fn=_refit_train,
            )
        else:
            hold = {"ok": False, "ic": None, "r2": None, "rmse": None, "reason": "no_panel"}
        # 只采 holdout 口径；失败则 R²/IC 视为缺失（loss 里 R²→0、IC→0），不混入全样本拟合 R²
        hold_ok = bool(hold.get("ok") and hold.get("refit"))
        r2 = hold.get("r2") if hold_ok else None
        ic_v = hold.get("ic") if hold_ok else None
        try:
            ic_f = float(ic_v) if ic_v is not None else 0.0
        except (TypeError, ValueError):
            ic_f = 0.0
        if hold_ok and hold.get("ic") is not None:
            ics.append(ic_f)
        if r2 is not None:
            try:
                r2s.append(float(r2))
            except (TypeError, ValueError):
                pass
        if hold_ok and hold.get("rmse") is not None:
            try:
                rmses.append(float(hold["rmse"]))
            except (TypeError, ValueError):
                pass
        groups_for_loss.append(
            {
                "member_count": n_mem,
                "pooled_r2": r2,
                "ic_mean": ic_f,
                "has_return_model": True,
            }
        )

        top_k = 1 if n_mem <= 2 else min(2, n_mem)
        models_by_code = {m: rm for m in members}
        member_bars = None
        if bars_by_code:
            member_bars = {
                m: bars_by_code[m] for m in members if m in bars_by_code
            }
        gate = evaluate_research_oos(
            codes=members,
            research_models_by_code=models_by_code,
            baseline_weights=base_w,
            watching_limit=len(members),
            min_names=2,
            lookback=lb,
            top_k=top_k,
            horizon_days=horizon_days,
            oos_tol_pp=oos_tol_pp,
            ridge_lambda=float(rm.get("ridge_lambda") or 0.0),
            respect_regime=regime_aligned,
            stock_bars=member_bars,
        )
        if gate.get("skipped"):
            skipped_n += 1
            continue
        delta = gate.get("delta_oos_pp")
        try:
            d = float(delta) if delta is not None else None
        except (TypeError, ValueError):
            d = None
        if d is not None and d == d:  # finite
            deltas.append(d)
        if gate.get("ok") and gate.get("passed"):
            passed_n += 1
        else:
            failed_n += 1

    loss_info = compute_partition_loss(
        groups=groups_for_loss,
        w_r2=float(w_r2),
        w_ic=float(w_ic),
        # ŷ→y：负相关应罚，不用 |IC|
        ic_use_abs=False,
    )
    mean_delta = round(float(sum(deltas) / len(deltas)), 4) if deltas else None
    mean_ic = round(float(sum(ics) / len(ics)), 4) if ics else None
    mean_r2 = round(float(sum(r2s) / len(r2s)), 4) if r2s else None
    mean_rmse = round(float(sum(rmses) / len(rmses)), 4) if rmses else None
    sil = None
    if silhouette is not None:
        try:
            sil = float(silhouette)
        except (TypeError, ValueError):
            sil = None
    loss = float(loss_info.get("loss") or 1e9)
    return {
        "passed": int(passed_n),
        "failed": int(failed_n),
        "skipped": int(skipped_n),
        "n_scored": int(passed_n + failed_n),
        "mean_delta_oos_pp": mean_delta,
        "mean_yhat_ic": mean_ic,
        "mean_holdout_r2": mean_r2,
        "mean_holdout_rmse": mean_rmse,
        "partition_loss": round(loss, 6),
        "loss_r2": loss_info.get("loss_r2"),
        "loss_ic": loss_info.get("loss_ic"),
        "silhouette": None if sil is None else round(sil, 4),
        "oos_tol_pp": float(oos_tol_pp),
        "w_r2": float(w_r2),
        "w_ic": float(w_ic),
        "sort_key": (
            -loss,
            int(passed_n),
            float(mean_delta) if mean_delta is not None else float("-inf"),
            float(sil) if sil is not None else float("-inf"),
        ),
    }


def pick_best_k_selection_row(
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """从 ``k_selection.candidates`` 行里挑最优（-loss → passed → ΔOOS → sil）。"""
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda r: tuple(
            (
                r.get("sort_key")
                or (
                    -(float(r.get("partition_loss") or 1e9)),
                    r.get("passed") or 0,
                    float("-inf"),
                    float("-inf"),
                )
            )
        ),
        reverse=True,
    )
    return ranked[0]


def _pick_preferred_cluster(clusters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """优选：组内 OOS 通过且 ΔOOS 最好；否则最大非单票有效建议组。"""
    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    for cl in clusters:
        gate = cl.get("oos_gate") or {}
        sug = cl.get("weight_suggest") or {}
        if not sug.get("success"):
            continue
        n = int(cl.get("member_count") or len(cl.get("members") or []))
        if gate.get("ok") and gate.get("passed") and not gate.get("skipped"):
            delta = gate.get("delta_oos_pp")
            try:
                d = float(delta) if delta is not None else 0.0
            except (TypeError, ValueError):
                d = 0.0
            scored.append((1000.0 + d, n, cl))
        elif not cl.get("singleton") and n >= 2:
            scored.append((float(n), n, cl))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def attach_cluster_export_diffs(report: Dict[str, Any]) -> Dict[str, Any]:
    """为每组挂 ``config_diff``，并选出 ``preferred_cluster`` 供人审导出。"""
    from core.signal.weight_suggest import format_weight_config_diff

    clusters: List[Dict[str, Any]] = list(report.get("clusters") or [])
    for cl in clusters:
        sug = dict(cl.get("weight_suggest") or {})
        gate = cl.get("oos_gate") or {}
        label = str(cl.get("label") or f"G{(cl.get('cluster_id') or 0) + 1}")
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        if not sug.get("success"):
            cl["config_diff"] = {
                "success": False,
                "error": sug.get("error") or "无组内建议权",
            }
            continue
        # 组权即使 OOS 通过也不自动全局 promote_ready
        sug["oos_gate"] = gate
        sug["promote_ready"] = False
        sug["ic_mode"] = sug.get("ic_mode") or "ols_cluster"
        diff = format_weight_config_diff(sug)
        if not diff.get("success"):
            cl["config_diff"] = diff
            continue
        if gate.get("ok") and gate.get("passed") and not gate.get("skipped"):
            apply_note = (
                f"组 {label} 组内 OOS 通过（ΔOOS={gate.get('delta_oos_pp')}pp）；"
                "导出供人审。若合并为全局 weights，请确认该组成员能代表研究池；"
                "系统不自动写盘。"
            )
        elif gate.get("skipped"):
            apply_note = (
                f"组 {label} 组内 OOS 已跳过（{gate.get('reason')}）；"
                "导出仅供对照，不建议直接 promote 为全局权。"
            )
        else:
            apply_note = (
                f"组 {label} 组内 OOS 未过（{gate.get('reason')}）；"
                "不建议 promote 为全局权。"
            )
        cl["config_diff"] = {
            **diff,
            "apply_note": apply_note,
            "cluster_label": label,
            "cluster_id": cl.get("cluster_id"),
            "cluster_members": members,
            "export_kind": "ols_cluster_weights",
            "promote_ready": False,
        }

    preferred = _pick_preferred_cluster(clusters)
    report["clusters"] = clusters
    if preferred is not None:
        report["preferred_cluster"] = {
            "cluster_id": preferred.get("cluster_id"),
            "label": preferred.get("label"),
            "members": list(preferred.get("members") or []),
            "member_count": preferred.get("member_count"),
            "oos_passed": bool(
                (preferred.get("oos_gate") or {}).get("passed")
                and not (preferred.get("oos_gate") or {}).get("skipped")
            ),
            "config_diff": preferred.get("config_diff"),
        }
    else:
        report["preferred_cluster"] = None
    note = str(report.get("note") or "")
    if "组权 diff" not in note:
        report["note"] = note + " 可导出优选组 / 各组权重 diff（人审，不写盘）。"
    return report


def attach_cluster_oos_gates(
    report: Dict[str, Any],
    *,
    lookback: int = 80,
    horizon_days: int = 3,
    oos_tol_pp: float = 1.0,
    run_oos_gate: bool = True,
    respect_regime: Optional[bool] = None,
    bars_by_code: Optional[Dict[str, List[dict]]] = None,
    progress_cb: ProgressCb = None,
) -> Dict[str, Any]:
    """就地为 ``report["clusters"]`` 挂 ``oos_gate``、``config_diff`` 与优选组。

    B5：默认 ``respect_regime`` 取自 report（分组拟合默认 True）。
    ``bars_by_code`` 复用分组阶段日线，避免每组再次 IO（100 票×多组会拖过前端超时）。
    """
    clusters: List[Dict[str, Any]] = list(report.get("clusters") or [])
    regime_aligned = (
        bool(respect_regime)
        if respect_regime is not None
        else bool(report.get("respect_regime", True))
    )
    if not run_oos_gate:
        for cl in clusters:
            cl["oos_gate"] = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "gate_disabled",
                "note": "未跑组内 OOS（run_oos_gate=false）。",
                "respect_regime": regime_aligned,
            }
        report["clusters"] = clusters
        report["oos_summary"] = {
            "run": False,
            "passed": 0,
            "failed": 0,
            "skipped": len(clusters),
            "respect_regime": regime_aligned,
        }
        return attach_cluster_export_diffs(report)

    from core.signal.weight_oos_gate import evaluate_research_oos
    from core.signal.config import load_signal_config

    passed_n = 0
    failed_n = 0
    skipped_n = 0
    lb = max(40, min(int(lookback or 80), 90))
    base_w = dict((load_signal_config() or {}).get("weights") or {})
    n_cl = max(1, len(clusters))

    for i, cl in enumerate(clusters):
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        label = str(cl.get("label") or f"G{i + 1}")
        if progress_cb:
            try:
                progress_cb(f"OOS {i + 1}/{n_cl} · {label}", i + 1, n_cl)
            except Exception:
                pass
        rm = cl.get("return_model")
        if cl.get("singleton") or len(members) < 2:
            cl["oos_gate"] = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "cluster_too_small",
                "note": "组成员不足 2 只，跳过组内 Top-K OOS。",
                "stock_count": len(members),
                "respect_regime": regime_aligned,
            }
            skipped_n += 1
            continue
        if not isinstance(rm, dict) or not rm.get("coefficients"):
            cl["oos_gate"] = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "no_return_model",
                "note": "无组内 return_model（ŷ），跳过 OOS。",
                "respect_regime": regime_aligned,
            }
            skipped_n += 1
            continue

        top_k = 1 if len(members) <= 2 else min(2, len(members))
        models_by_code = {m: rm for m in members}
        member_bars = None
        if bars_by_code:
            member_bars = {
                m: bars_by_code[m] for m in members if m in bars_by_code
            }
        gate = evaluate_research_oos(
            codes=members,
            research_models_by_code=models_by_code,
            baseline_weights=base_w,
            watching_limit=len(members),
            min_names=2,
            lookback=lb,
            top_k=top_k,
            horizon_days=horizon_days,
            oos_tol_pp=oos_tol_pp,
            ridge_lambda=float(rm.get("ridge_lambda") or 0.0),
            respect_regime=regime_aligned,
            stock_bars=member_bars,
        )
        gate = dict(gate)
        gate["scope"] = "cluster_members"
        gate["cluster_label"] = cl.get("label")
        gate["respect_regime"] = regime_aligned
        if gate.get("note"):
            gate["note"] = (
                str(gate["note"])
                + " 范围仅限本组股票；基线=全局 heuristic；研究臂=本组 β→ŷ；不写 signal_config。"
                + (
                    " 研究模型按 respect_regime 拟合。"
                    if regime_aligned
                    else " 研究模型为全因子拟合。"
                )
            )
        cl["oos_gate"] = gate
        if gate.get("skipped"):
            skipped_n += 1
        elif gate.get("ok") and gate.get("passed"):
            passed_n += 1
        else:
            failed_n += 1

    report["clusters"] = clusters
    report["oos_summary"] = {
        "run": True,
        "passed": passed_n,
        "failed": failed_n,
        "skipped": skipped_n,
        "oos_tol_pp": float(oos_tol_pp),
        "respect_regime": regime_aligned,
        "note": (
            "各组：heuristic 基线 vs 组 return_model ŷ · 仅组员 Top-K · 后 30% OOS；"
            "默认 respect_regime 与 live 对齐。"
        ),
    }
    note = str(report.get("note") or "")
    if "组内 OOS" not in note:
        report["note"] = note + " 已附组内 OOS/Top-K 对照（heuristic vs ŷ，只读）。"
    return attach_cluster_export_diffs(report)
