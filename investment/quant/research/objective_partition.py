"""[研究试点] 目标驱动的分区优化器（组池 R² 高 + 组内 IC 高）。

生产路径已接入：auto-k 多折 holdout 选区，以及定组后
``cluster_greedy_refine.light_greedy_swap_refine``（有界 holdout 贪心换组）。
本模块的全量候选（含 IC 特征拼接）与无界贪心仍可选调用，默认不进 live。

用法（研究试点）::

    from quant.research.objective_partition import (
        objective_search_partition,
        labels_to_group_indices,
    )

    best_labels, candidates, diagnostics = objective_search_partition(
        codes=codes,
        beta_scaled=x,            # 建议用前段窗口尺度化 β
        panel_by_code=panel_by_code,
        feature_names=feature_names,
        horizon_days=horizon_days,
        ridge_lambda=lam,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
    )
"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.research.cluster_partition import (
    agglomerative_labels,
    auto_k_candidates,
    default_n_clusters,
    fit_beta_scale_transform,
    kmeans_labels,
    refine_cluster_labels,
    split_oversized_clusters,
    within_dist_tau,
)
from quant.research.partition_loss import (
    compute_partition_loss,
    extract_group_ic_from_panel,
    extract_group_r2_from_pooled,
)


def labels_to_group_indices(labels: Sequence[int]) -> Dict[int, List[int]]:
    """labels 数组 → {cluster_id: [idx_in_codes, ...]}。"""
    groups: Dict[int, List[int]] = {}
    for i, lab in enumerate(labels):
        if int(lab) < 0:
            continue
        groups.setdefault(int(lab), []).append(int(i))
    return groups


def _build_ic_matrix(
    panel_by_code: Dict[str, Dict[str, Any]],
    codes: Sequence[str],
    feature_names: Sequence[str],
    *,
    method: str = "spearman",
) -> np.ndarray:
    """n×p 时序 IC 矩阵（每个单票各自的 xs vs ys IC）。

    用 scipy spearman；没装则退回 pearson。结果仅用作候选特征，不参与最终打分。
    """
    from core.signal.factor_corr import pearson_with_reason

    rows: List[np.ndarray] = []
    try:
        from scipy.stats import spearmanr  # type: ignore
        have_spearman = True
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
        have_spearman = False
    use_spearman = have_spearman and str(method).lower() == "spearman"

    for code in codes:
        panel = panel_by_code.get(str(code)) or {}
        xs = list(panel.get("xs") or [])
        ys_raw = list(panel.get("ys") or [])
        n = min(len(xs), len(ys_raw))
        y_list: List[float] = []
        for v in ys_raw[:n]:
            try:
                y_list.append(float(v))
            except (TypeError, ValueError):
                y_list.append(float("nan"))
        if n < 5:
            rows.append(np.zeros(len(feature_names), dtype=float))
            continue
        vec = np.zeros(len(feature_names), dtype=float)
        for j, f in enumerate(feature_names):
            fx: List[float] = []
            fy: List[float] = []
            for i in range(n):
                row = xs[i]
                if not isinstance(row, dict):
                    continue
                v = row.get(str(f))
                if v is None:
                    continue
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    continue
                yi = y_list[i]
                if not math.isfinite(yi) or not math.isfinite(val):
                    continue
                fx.append(val)
                fy.append(yi)
            if len(fx) < 20:
                continue
            if use_spearman:
                try:
                    icv, _ = spearmanr(fx, fy)
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
                    icv, _ = pearson_with_reason(fx, fy)
            else:
                icv, _ = pearson_with_reason(fx, fy)
            if icv is not None and math.isfinite(float(icv)):
                vec[j] = float(icv)
        rows.append(vec)
    return np.asarray(rows, dtype=float)


def _candidate_hstack_beta_ic(
    beta_scaled: np.ndarray,
    panel_by_code: Dict[str, Dict[str, Any]],
    codes: Sequence[str],
    feature_names: Sequence[str],
    *,
    alpha: float = 0.7,
    seed: int = 42,
    k_target: int,
    linkage: str = "complete",
) -> Dict[str, Any]:
    """候选：β 拼 IC → agglomerative。"""
    ic_raw = _build_ic_matrix(panel_by_code, codes, feature_names)
    ic_scaled, _ = fit_beta_scale_transform(ic_raw, "feature_zscore")
    a = float(alpha)
    feat = np.hstack([a * beta_scaled, (1.0 - a) * ic_scaled])
    labels, centers = agglomerative_labels(feat, n_clusters=k_target, linkage=linkage)
    tau = within_dist_tau(feat, 0.55)
    refined = refine_cluster_labels(
        feat, labels, min_size=2, tau=tau, strict_diameter=True, absorb_far=False
    )
    labels = np.asarray(refined["labels"], dtype=int)
    max_size = max(5, int(math.ceil(1.5 * len(codes) / max(1, k_target))))
    labels, _splits = split_oversized_clusters(feat, labels, max_size=max_size, linkage=linkage)
    # 重新编号
    uniq = sorted(set(int(v) for v in labels if int(v) >= 0))
    remap = {old: i for i, old in enumerate(uniq)}
    labels = np.array(
        [remap[int(v)] if int(v) >= 0 else -1 for v in labels],
        dtype=int,
    )
    return {"labels": labels, "kind": "hstack_beta_ic", "alpha": a}


def _candidate_ic_weighted(
    beta_scaled: np.ndarray,
    panel_by_code: Dict[str, Dict[str, Any]],
    codes: Sequence[str],
    feature_names: Sequence[str],
    *,
    shrinkage: float = 0.4,
    seed: int = 42,
    k_target: int,
) -> Dict[str, Any]:
    """候选：IC 加权 β 距离 → kmeans 嵌入近似 agglomerative。"""
    ic_raw = _build_ic_matrix(panel_by_code, codes, feature_names)
    ic_mean = ic_raw.mean(axis=0, keepdims=True)
    ic_shrunk = shrinkage * ic_raw + (1.0 - shrinkage) * ic_mean
    w = np.abs(ic_shrunk).mean(axis=0)
    w_sum = float(w.sum())
    if w_sum < 1e-9:
        w = np.ones(beta_scaled.shape[1], dtype=float) / beta_scaled.shape[1]
    else:
        w = w / w_sum
    weighted = beta_scaled * np.sqrt(w)  # 距离平方 = (\u03b2_i-\u03b2_j)^T W (\u03b2_i-\u03b2_j)
    labels, centers = agglomerative_labels(weighted, n_clusters=k_target, linkage="complete")
    tau = within_dist_tau(weighted, 0.55)
    refined = refine_cluster_labels(
        weighted, labels, min_size=2, tau=tau, strict_diameter=True, absorb_far=False
    )
    labels = np.asarray(refined["labels"], dtype=int)
    max_size = max(5, int(math.ceil(1.5 * len(codes) / max(1, k_target))))
    labels, _splits = split_oversized_clusters(weighted, labels, max_size=max_size, linkage="complete")
    uniq = sorted(set(int(v) for v in labels if int(v) >= 0))
    remap = {old: i for i, old in enumerate(uniq)}
    labels = np.array(
        [remap[int(v)] if int(v) >= 0 else -1 for v in labels],
        dtype=int,
    )
    return {"labels": labels, "kind": "ic_weighted_beta", "shrinkage": shrinkage}


def _candidate_agglomerative_linkage(
    beta_scaled: np.ndarray,
    *,
    k_target: int,
    linkage: str,
) -> Dict[str, Any]:
    """候选：β + 指定 linkage + 标准后处理。"""
    labels, centers = agglomerative_labels(beta_scaled, n_clusters=k_target, linkage=linkage)
    tau = within_dist_tau(beta_scaled, 0.55)
    refined = refine_cluster_labels(
        beta_scaled, labels, min_size=2, tau=tau, strict_diameter=True, absorb_far=False
    )
    labels = np.asarray(refined["labels"], dtype=int)
    max_size = max(5, int(math.ceil(1.5 * len(labels) / max(1, k_target))))
    labels, _splits = split_oversized_clusters(
        beta_scaled, labels, max_size=max_size, linkage=linkage
    )
    uniq = sorted(set(int(v) for v in labels if int(v) >= 0))
    remap = {old: i for i, old in enumerate(uniq)}
    labels = np.array(
        [remap[int(v)] if int(v) >= 0 else -1 for v in labels],
        dtype=int,
    )
    return {"labels": labels, "kind": f"agglomerative_{linkage}"}


def _candidate_kmeans_seeds(
    beta_scaled: np.ndarray, *, k_target: int, seed: int
) -> Dict[str, Any]:
    """候选：kmeans 单独跑一种 seed。"""
    labels, centers = kmeans_labels(beta_scaled, n_clusters=k_target, seed=seed)
    tau = within_dist_tau(beta_scaled, 0.55)
    refined = refine_cluster_labels(
        beta_scaled, labels, min_size=2, tau=tau, strict_diameter=True, absorb_far=False
    )
    labels = np.asarray(refined["labels"], dtype=int)
    uniq = sorted(set(int(v) for v in labels if int(v) >= 0))
    remap = {old: i for i, old in enumerate(uniq)}
    labels = np.array(
        [remap[int(v)] if int(v) >= 0 else -1 for v in labels],
        dtype=int,
    )
    return {"labels": labels, "kind": f"kmeans_seed_{seed}"}


def generate_candidate_partitions(
    codes: Sequence[str],
    beta_scaled: np.ndarray,
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    k_target: Optional[int] = None,
    k_list: Optional[Sequence[int]] = None,
    enable_ic_features: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """生成多个候选分区，每个 dict 含 labels (n,) + kind。

    ``k_list`` 优先；否则单点 ``k_target`` 或 ``default_n_clusters(n)``。
    生产 auto-k 邻域请传 ``auto_k_candidates(n)``。
    """
    n = len(codes)
    if k_list:
        ks = sorted({max(2, min(int(k), n - 1)) for k in k_list if int(k) >= 2})
    else:
        if k_target is None:
            k_target = default_n_clusters(n)
        ks = [max(2, min(int(k_target), n - 1))]
    candi: List[Dict[str, Any]] = []

    for k_target in ks:
        # 1) agglomerative complete / average
        c1 = _candidate_agglomerative_linkage(
            beta_scaled, k_target=k_target, linkage="complete"
        )
        c1["kind"] = f"{c1.get('kind', 'agg_complete')}_k{k_target}"
        candi.append(c1)
        c2 = _candidate_agglomerative_linkage(
            beta_scaled, k_target=k_target, linkage="average"
        )
        c2["kind"] = f"{c2.get('kind', 'agg_average')}_k{k_target}"
        candi.append(c2)
        # 2) kmeans 多种 seed
        for s in (42, 7, 2024):
            ck = _candidate_kmeans_seeds(beta_scaled, k_target=k_target, seed=s)
            ck["kind"] = f"{ck.get('kind', f'kmeans_{s}')}_k{k_target}"
            candi.append(ck)
        # 3) IC 相关增强（需要 panel_by_code 非空）
        if enable_ic_features and panel_by_code:
            try:
                ch = _candidate_hstack_beta_ic(
                    beta_scaled, panel_by_code, codes, feature_names,
                    alpha=0.7, seed=42, k_target=k_target, linkage="complete",
                )
                ch["kind"] = f"{ch.get('kind', 'hstack')}_k{k_target}"
                candi.append(ch)
            except Exception as exc:
                if progress_cb:
                    try:
                        progress_cb(f"候选 hstack 跳过: {exc}", 0, 1)
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
                        pass
            try:
                cw = _candidate_ic_weighted(
                    beta_scaled, panel_by_code, codes, feature_names,
                    shrinkage=0.4, seed=42, k_target=k_target,
                )
                cw["kind"] = f"{cw.get('kind', 'ic_weighted')}_k{k_target}"
                candi.append(cw)
            except Exception as exc:
                if progress_cb:
                    try:
                        progress_cb(f"候选 ic_weighted 跳过: {exc}", 0, 1)
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
                        pass
    # 去重（labels 完全相同则只留 1）
    seen = set()
    dedup: List[Dict[str, Any]] = []
    for c in candi:
        lab = tuple(int(v) for v in np.asarray(c["labels"], dtype=int).tolist())
        if lab in seen:
            continue
        seen.add(lab)
        dedup.append(c)
    return dedup


def _fit_group_pooled(
    member_codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
) -> Tuple[Optional[float], Dict[str, Any]]:
    """返回组池 R²，以及完整 pooled 结果。"""
    from core.research.factor_ols_fit import fit_factor_ols_from_panel

    all_xs: List[Dict[str, Any]] = []
    all_ys: List[float] = []
    for code in member_codes:
        panel = panel_by_code.get(str(code)) or {}
        all_xs.extend(panel.get("xs") or [])
        all_ys.extend(panel.get("ys") or [])
    if len(all_xs) < 10 or len(all_ys) < 10:
        return None, {"success": False, "error": "组内样本不足"}
    try:
        pooled = fit_factor_ols_from_panel(
            all_xs,
            all_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=bool(use_pit),
            mode="watching_pooled",
            stock_codes=list(member_codes),
            ridge_lambda=float(ridge_lambda),
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
        )
    except Exception as exc:
        logger.exception('unexpected error in _fit_group_pooled')
        return None, {"success": False, "error": str(exc)}
    r2 = extract_group_r2_from_pooled(pooled)
    return r2, pooled


def _fit_group_ic(
    member_codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    pit_fundamentals: bool,
) -> float:
    """组内 IC 均值：多票组用截面 IC，单票组回退时序 IC。"""
    from quant.research.factor_ols_clusters import (
        _member_bars_and_funds,
        group_cs_ic_panel,
        group_ts_ic_panel,
    )

    if len(member_codes) < 2:
        xs, ys = [], []
        if member_codes:
            p = panel_by_code.get(str(member_codes[0])) or {}
            xs = list(p.get("xs") or [])
            ys = list(p.get("ys") or [])
        result = group_ts_ic_panel(xs, ys, feature_names)
        return extract_group_ic_from_panel(result, use_abs=False)
    bars_map, funds = _member_bars_and_funds(member_codes, panel_by_code)
    if len(bars_map) < 2:
        # 回退 TS
        xs, ys = [], []
        if member_codes:
            p = panel_by_code.get(str(member_codes[0])) or {}
            xs = list(p.get("xs") or [])
            ys = list(p.get("ys") or [])
        result = group_ts_ic_panel(xs, ys, feature_names)
        return extract_group_ic_from_panel(result, use_abs=False)
    result = group_cs_ic_panel(
        bars_map,
        feature_names,
        horizon_days=horizon_days,
        pit_fundamentals=bool(pit_fundamentals),
        fundamentals_by_code=funds or None,
    )
    return extract_group_ic_from_panel(result, use_abs=False)


def evaluate_partition(
    labels: Sequence[int],
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    w_r2: float = 1.0,
    w_ic: float = 1.0,
    lambda_imbalance: float = 0.3,
    lambda_singleton: float = 0.75,
    use_holdout: bool = True,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
) -> Dict[str, Any]:
    """对一组 labels 做组指标 → 统一 loss。

    默认 ``use_holdout=True``：与 live 一致，用尾段 ŷ holdout（前段重拟合）的
    R²/IC；``False`` 时回退全样本池 OLS + 因子 IC（仅诊断）。
    """
    if use_holdout:
        from quant.research.cluster_greedy_refine import evaluate_labels_holdout_loss

        out = evaluate_labels_holdout_loss(
            labels,
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
        )
        # 覆盖权重（holdout 评估器内部用默认常量；此处允许研究覆盖）
        loss_info = compute_partition_loss(
            groups=out.get("groups") or [],
            w_r2=float(w_r2),
            w_ic=float(w_ic),
            lambda_imbalance=float(lambda_imbalance),
            lambda_singleton=float(lambda_singleton),
            ic_use_abs=False,
        )
        return {
            "groups": out.get("groups") or [],
            "cache": out.get("cache"),
            **loss_info,
            "eval_mode": "holdout_yhat",
        }

    groups_idx = labels_to_group_indices(labels)
    groups_metrics: List[Dict[str, Any]] = []
    for cid, idxs in groups_idx.items():
        members = [codes[i] for i in idxs]
        r2, pooled = _fit_group_pooled(
            members,
            panel_by_code,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            use_pit=use_pit,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
        )
        has_rm = False
        if isinstance(pooled, dict):
            coefs = pooled.get("coefficients")
            if isinstance(coefs, dict) and coefs or pooled.get("success") and r2 is not None:
                has_rm = True
        try:
            ic_m = _fit_group_ic(
                members,
                panel_by_code,
                feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=bool(use_pit),
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
            ic_m = 0.0
        groups_metrics.append(
            {
                "cluster_id": int(cid),
                "member_count": int(len(members)),
                "members": members,
                "pooled_r2": None if r2 is None else float(r2),
                "ic_mean": float(ic_m),
                "has_return_model": bool(has_rm),
                "fit_ok": bool(has_rm),
                "_pooled": pooled,
            }
        )
    loss_info = compute_partition_loss(
        groups=groups_metrics,
        w_r2=float(w_r2),
        w_ic=float(w_ic),
        lambda_imbalance=float(lambda_imbalance),
        lambda_singleton=float(lambda_singleton),
        ic_use_abs=False,
    )
    return {"groups": groups_metrics, **loss_info, "eval_mode": "in_sample"}


def greedy_swap_optimize(
    labels_init: Sequence[int],
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    w_r2: float = 1.0,
    w_ic: float = 1.0,
    max_rounds: int = 6,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[np.ndarray, Dict[str, Any], Dict[str, Any]]:
    """贪心：逐票尝试换到其它组，若 loss 下降则永久接受。多轮直到无改进。

    返回 (best_labels, final_eval, swap_log)。
    """
    best = np.asarray(labels_init, dtype=int).copy()
    best_eval = evaluate_partition(
        best, codes, panel_by_code, feature_names,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        w_r2=w_r2, w_ic=w_ic,
    )
    best_loss = float(best_eval["loss"])
    n = int(best.shape[0])
    swaps: List[Dict[str, Any]] = []

    for rnd in range(max_rounds):
        moved_any = False
        for i in range(n):
            own = int(best[i])
            if own < 0:
                continue
            uniq = sorted(set(int(v) for v in best if int(v) >= 0))
            if len(uniq) < 2:
                break
            for other in uniq:
                if other == own:
                    continue
                trial = best.copy()
                trial[i] = other
                ev = evaluate_partition(
                    trial, codes, panel_by_code, feature_names,
                    horizon_days=horizon_days,
                    ridge_lambda=ridge_lambda,
                    use_pit=use_pit,
                    select_ridge=select_ridge,
                    collinearity_policy=collinearity_policy,
                    respect_regime=respect_regime,
                    y_spec=y_spec,
                    w_r2=w_r2, w_ic=w_ic,
                )
                if float(ev["loss"]) + 1e-6 < best_loss:
                    loss_before = best_loss
                    best = trial
                    best_eval = ev
                    best_loss = float(ev["loss"])
                    swaps.append(
                        {
                            "round": int(rnd + 1),
                            "index": int(i),
                            "code": str(codes[i]),
                            "from": int(own),
                            "to": int(other),
                            "loss_before": round(loss_before, 6),
                            "loss_after": round(float(ev["loss"]), 6),
                            "delta": round(float(ev["loss"]) - loss_before, 6),
                        }
                    )
                    moved_any = True
                    own = int(other)
                    break
        if not moved_any:
            break

    uniq = sorted(set(int(v) for v in best if int(v) >= 0))
    remap = {old: i for i, old in enumerate(uniq)}
    best = np.array(
        [remap[int(v)] if int(v) >= 0 else -1 for v in best],
        dtype=int,
    )
    # 组 id 变了但 groups 里没变，需要重跑一次评估
    final_eval = evaluate_partition(
        best, codes, panel_by_code, feature_names,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        w_r2=w_r2, w_ic=w_ic,
    )
    swap_log = {"n_swaps": len(swaps), "swaps": swaps}
    return best, final_eval, swap_log


def objective_search_partition(
    codes: Sequence[str],
    beta_scaled: np.ndarray,
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    k_target: Optional[int] = None,
    enable_ic_features: bool = True,
    w_r2: float = 1.0,
    w_ic: float = 1.0,
    run_greedy_swap: bool = True,
    search_k_neighborhood: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]], Dict[str, Any]]:
    """对外主入口：候选搜索 + 打分 + 可选贪心精修。

    返回 (best_labels, candidates_scores, diagnostics)。
    默认 ``search_k_neighborhood=True``：对 ``auto_k_candidates(n)`` 各跑一套候选。
    """
    n = len(codes)
    if n <= 2:
        # 样本太少，退化成一组
        return (
            np.zeros(n, dtype=int),
            [],
            {"degraded": True, "reason": "n<=2 退化分组"},
        )

    if k_target is not None:
        k_list = [int(k_target)]
    elif search_k_neighborhood:
        k_list = list(auto_k_candidates(n))
    else:
        k_list = [default_n_clusters(n)]

    candidates = generate_candidate_partitions(
        codes,
        beta_scaled,
        panel_by_code,
        feature_names,
        k_list=k_list,
        enable_ic_features=enable_ic_features,
        progress_cb=progress_cb,
    )
    n_cand = len(candidates)
    if progress_cb:
        try:
            progress_cb(f"评估候选分区（共 {n_cand} 个）", 0, n_cand)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
            pass

    scored: List[Dict[str, Any]] = []
    for i, cand in enumerate(candidates):
        labels = np.asarray(cand["labels"], dtype=int)
        ev = evaluate_partition(
            labels, codes, panel_by_code, feature_names,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            use_pit=use_pit,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
            w_r2=w_r2, w_ic=w_ic,
        )
        scored.append(
            {
                "kind": cand.get("kind", "unknown"),
                "labels": labels,
                "loss": float(ev["loss"]),
                "loss_r2": float(ev["loss_r2"]),
                "loss_ic": float(ev["loss_ic"]),
                "penalty_imbalance": float(ev["penalty_imbalance"]),
                "penalty_singleton": float(ev["penalty_singleton"]),
                "group_count": int(ev["group_count"]),
                "singleton_count": int(ev["singleton_count"]),
                "max_share": float(ev["max_share"]),
                "details": ev["details"],
                "groups": ev["groups"],
            }
        )
        if progress_cb:
            try:
                progress_cb(
                    f"候选 {i+1}/{n_cand}: {cand.get('kind')} loss={float(ev['loss']):.4f}",
                    i + 1, n_cand,
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
                pass

    scored.sort(key=lambda r: float(r["loss"]))
    best_cand = scored[0]
    best_labels = np.asarray(best_cand["labels"], dtype=int)

    diagnostics = {
        "candidate_count": int(n_cand),
        "best_kind": best_cand.get("kind"),
        "best_loss": float(best_cand["loss"]),
        "best_loss_r2": float(best_cand["loss_r2"]),
        "best_loss_ic": float(best_cand["loss_ic"]),
        "best_group_count": int(best_cand["group_count"]),
        "best_singleton_count": int(best_cand["singleton_count"]),
        "candidates_summary": [
            {k: v for k, v in s.items() if k not in ("labels", "details", "groups")}
            for s in scored
        ],
    }

    if run_greedy_swap and n >= 4 and int(best_cand["group_count"]) >= 2:
        if progress_cb:
            try:
                progress_cb("贪心交换精修…", 0, 1)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in objective_partition.py", exc_info=True)
                pass
        new_labels, final_eval, swap_log = greedy_swap_optimize(
            best_labels,
            codes,
            panel_by_code,
            feature_names,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            use_pit=use_pit,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
            w_r2=w_r2, w_ic=w_ic,
            progress_cb=progress_cb,
        )
        diagnostics["greedy"] = {
            "before_loss": float(best_cand["loss"]),
            "after_loss": float(final_eval["loss"]),
            "delta": round(float(final_eval["loss"]) - float(best_cand["loss"]), 6),
            "swap_log": swap_log,
            "final_groups": final_eval.get("groups"),
        }
        # 如果贪心后确实更好，才采纳；否则保留原来
        if float(final_eval["loss"]) + 1e-6 < float(best_cand["loss"]):
            best_labels = new_labels
            diagnostics["greedy"]["adopted"] = True
        else:
            diagnostics["greedy"]["adopted"] = False

    return best_labels, scored, diagnostics
