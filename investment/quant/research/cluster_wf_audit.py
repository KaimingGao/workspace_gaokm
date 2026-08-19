"""扩展窗多折聚类审计（只读诊断，不改交付标签）。

在若干宇宙日历切点：前段估 β → 固定 k/配方重聚类 → 尾段 holdout 评分；
相邻折标签对齐后记稳定性。生产 return_model 仍走主路径全样本组池。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def resolve_expanding_cut_dates(
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    codes: Optional[Sequence[str]] = None,
    train_fractions: Sequence[float] = (0.55, 0.7),
) -> List[Dict[str, Any]]:
    """train_fractions → [{train_fraction, holdout_ratio, cut_date}, ...]。缺日期则空。"""
    from quant.research.partition_loss import resolve_calendar_cut_date

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for tf in train_fractions:
        try:
            frac = float(tf)
        except (TypeError, ValueError):
            continue
        frac = max(0.45, min(0.85, frac))
        holdout_ratio = max(0.15, min(0.5, 1.0 - frac))
        cut = resolve_calendar_cut_date(
            panel_by_code, holdout_ratio=holdout_ratio, codes=codes
        )
        if not cut or cut in seen:
            continue
        seen.add(cut)
        out.append(
            {
                "train_fraction": round(frac, 4),
                "holdout_ratio": round(float(holdout_ratio), 4),
                "cut_date": cut,
            }
        )
    return out


def _partition_kind_to_spec(kind: str, fallback_method: str, fallback_link: str) -> Dict[str, Any]:
    k = str(kind or "").strip().lower()
    if k.startswith("kmeans"):
        seed = 42
        if "_" in k:
            try:
                seed = int(k.split("_", 1)[1])
            except (TypeError, ValueError):
                seed = 42
        return {
            "method": "kmeans",
            "linkage": fallback_link or "average",
            "seed": seed,
            "kind": kind or "kmeans_42",
        }
    if "average" in k:
        return {
            "method": "hierarchical",
            "linkage": "average",
            "seed": 42,
            "kind": kind or "hierarchical_average",
        }
    if "complete" in k or k.startswith("hierarchical"):
        return {
            "method": "hierarchical",
            "linkage": "complete",
            "seed": 42,
            "kind": kind or "hierarchical_complete",
        }
    return {
        "method": str(fallback_method or "hierarchical"),
        "linkage": str(fallback_link or "average"),
        "seed": 42,
        "kind": kind or f"{fallback_method}_{fallback_link}",
    }


def expanding_cluster_wf_audit(
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    n_clusters: int,
    partition_kind: str,
    method: str = "hierarchical",
    linkage: str = "average",
    scale_mode: str = "feature_zscore",
    tau_q: float = 0.85,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    collinearity_policy: str = "drop_redundant",
    respect_regime: bool = True,
    y_spec: Optional[Dict[str, Any]] = None,
    use_pit: bool = True,
    select_ridge: bool = False,
    train_fractions: Sequence[float] = (0.55, 0.7),
    per_stock: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """多折扩展窗：每折独立前段 β 定组 + 尾段评分；不写 live。"""
    from quant.research.cluster_label_align import pair_label_stability
    from quant.research.cluster_oos import score_cluster_partition_oos
    from quant.research.cluster_partition import (
        cluster_beta_vectors,
        fit_beta_scale_transform,
        promote_outliers_to_singleton_clusters,
        _relabel_non_negative,
    )
    from quant.research.factor_ols_clusters import (
        _slim_pool_clusters_for_oos,
        build_train_window_beta_matrix,
    )

    empty: Dict[str, Any] = {
        "ok": False,
        "n_folds": 0,
        "folds": [],
        "mean_partition_loss": None,
        "mean_yhat_ic": None,
        "mean_stability": None,
        "reason": None,
    }
    code_list = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if len(code_list) < 4:
        return {**empty, "reason": "too_few_codes"}
    cuts = resolve_expanding_cut_dates(
        panel_by_code, codes=code_list, train_fractions=train_fractions
    )
    if len(cuts) < 2:
        return {**empty, "reason": "need_calendar_cuts"}

    spec = _partition_kind_to_spec(partition_kind, method, linkage)
    k_req = max(2, int(n_clusters or 2))
    stock_rows = list(per_stock or [])
    bars_by_code = {
        str(c): list((panel_by_code.get(str(c)) or {}).get("bars") or [])
        for c in code_list
    }

    folds: List[Dict[str, Any]] = []
    prev_labels: Optional[np.ndarray] = None
    stabilities: List[float] = []
    losses: List[float] = []
    ics: List[float] = []

    for fold_i, cut_meta in enumerate(cuts):
        cut = str(cut_meta["cut_date"])
        holdout_ratio = float(cut_meta["holdout_ratio"])
        raw, beta_meta = build_train_window_beta_matrix(
            code_list,
            panel_by_code,
            feature_names,
            horizon_days=horizon_days,
            ridge_lambda=float(ridge_lambda),
            collinearity_policy=str(collinearity_policy),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
            use_pit=bool(use_pit),
            holdout_ratio=holdout_ratio,
            cut_date=cut,
        )
        if int(beta_meta.get("n_fit_ok") or 0) < 4:
            folds.append(
                {
                    "fold": fold_i + 1,
                    **cut_meta,
                    "ok": False,
                    "reason": "train_beta_sparse",
                    "cluster_beta": beta_meta,
                }
            )
            continue
        x, _tf = fit_beta_scale_transform(raw, scale_mode)
        clustered = cluster_beta_vectors(
            x,
            method=str(spec["method"]),
            n_clusters=k_req,
            seed=int(spec.get("seed") or 42),
            cluster_linkage=str(spec.get("linkage") or "average"),
            within_dist_quantile=float(tau_q),
            auto_postprocess=True,
        )
        labels = np.asarray(clustered.get("labels"), dtype=int)
        labels, _ = promote_outliers_to_singleton_clusters(labels)
        labels = _relabel_non_negative(labels)
        stability = None
        if prev_labels is not None and prev_labels.shape == labels.shape:
            stab = pair_label_stability(code_list, prev_labels, labels)
            stability = stab.get("stability")
            if stability is not None:
                stabilities.append(float(stability))
        clusters = _slim_pool_clusters_for_oos(
            codes=code_list,
            labels=labels,
            panel_by_code=panel_by_code,
            per_stock=stock_rows,
            horizon_days=horizon_days,
            ridge_lambda=float(ridge_lambda),
            use_pit=bool(use_pit),
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
            train_window_only=True,
            holdout_ratio=holdout_ratio,
            cut_date=cut,
        )
        score = score_cluster_partition_oos(
            clusters,
            horizon_days=horizon_days,
            respect_regime=bool(respect_regime),
            bars_by_code=bars_by_code,
            panel_by_code=panel_by_code,
            silhouette=clustered.get("silhouette"),
            ridge_lambda=float(ridge_lambda),
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy),
            y_spec=dict(y_spec or {}),
            use_pit=bool(use_pit),
            holdout_ratio=holdout_ratio,
            cut_date=cut,
        )
        loss_v = score.get("partition_loss")
        try:
            if loss_v is not None:
                losses.append(float(loss_v))
        except (TypeError, ValueError):
            pass
        ic_v = score.get("mean_yhat_ic")
        try:
            if ic_v is not None:
                ics.append(float(ic_v))
        except (TypeError, ValueError):
            pass
        folds.append(
            {
                "fold": fold_i + 1,
                **cut_meta,
                "ok": True,
                "n_clusters": int(clustered.get("n_clusters") or len(set(int(v) for v in labels if int(v) >= 0))),
                "partition_kind": spec.get("kind"),
                "partition_loss": score.get("partition_loss"),
                "mean_yhat_ic": score.get("mean_yhat_ic"),
                "mean_holdout_r2": score.get("mean_holdout_r2"),
                "passed": score.get("passed"),
                "failed": score.get("failed"),
                "stability_vs_prev": None if stability is None else round(float(stability), 4),
                "cluster_beta": {
                    "n_fit_ok": beta_meta.get("n_fit_ok"),
                    "n_full_sample_fallback": beta_meta.get("n_full_sample_fallback"),
                    "cut_date": cut,
                },
            }
        )
        prev_labels = labels

    ok_folds = [f for f in folds if f.get("ok")]
    if len(ok_folds) < 1:
        return {**empty, "folds": folds, "reason": "all_folds_failed"}
    return {
        "ok": True,
        "n_folds": len(ok_folds),
        "folds": folds,
        "mean_partition_loss": (
            round(float(sum(losses) / len(losses)), 6) if losses else None
        ),
        "mean_yhat_ic": (
            round(float(sum(ics) / len(ics)), 4) if ics else None
        ),
        "mean_stability": (
            round(float(sum(stabilities) / len(stabilities)), 4)
            if stabilities
            else None
        ),
        "partition_kind": spec.get("kind"),
        "n_clusters_target": k_req,
        "note": (
            "扩展窗审计：各折独立前段 β 重聚类 + 尾段 holdout；"
            "不改交付标签 / 全样本 return_model。"
        ),
    }
