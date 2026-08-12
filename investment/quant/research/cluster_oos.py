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
    silhouette: Optional[float] = None,
) -> Dict[str, Any]:
    """轻量组级 ΔOOS 汇总，供 auto-k 邻域选优（不写 config_diff）。

    排序键：通过组数 ↓ → 平均 ΔOOS ↓ → silhouette ↓。
    """
    from core.signal.weight_oos_gate import evaluate_research_oos
    from core.signal.config import load_signal_config

    regime_aligned = bool(respect_regime)
    lb = max(40, min(int(lookback or 80), 90))
    base_w = dict((load_signal_config() or {}).get("weights") or {})
    passed_n = 0
    failed_n = 0
    skipped_n = 0
    deltas: List[float] = []

    for cl in clusters or []:
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        rm = cl.get("return_model")
        if cl.get("singleton") or len(members) < 2:
            skipped_n += 1
            continue
        if not isinstance(rm, dict) or not rm.get("coefficients"):
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

    mean_delta = round(float(sum(deltas) / len(deltas)), 4) if deltas else None
    sil = None
    if silhouette is not None:
        try:
            sil = float(silhouette)
        except (TypeError, ValueError):
            sil = None
    return {
        "passed": int(passed_n),
        "failed": int(failed_n),
        "skipped": int(skipped_n),
        "n_scored": int(passed_n + failed_n),
        "mean_delta_oos_pp": mean_delta,
        "silhouette": None if sil is None else round(sil, 4),
        "oos_tol_pp": float(oos_tol_pp),
        "sort_key": (
            int(passed_n),
            float(mean_delta) if mean_delta is not None else float("-inf"),
            float(sil) if sil is not None else float("-inf"),
        ),
    }


def pick_best_k_selection_row(
    rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """从 ``k_selection.candidates`` 行里挑最优（passed → mean ΔOOS → sil）。"""
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda r: tuple(
            (r.get("sort_key") or (r.get("passed") or 0, float("-inf"), float("-inf")))
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
