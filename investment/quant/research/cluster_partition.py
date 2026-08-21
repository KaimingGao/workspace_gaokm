"""β 向量聚类 / 分区算法（从 factor_ols_clusters 拆出，纯 numpy）。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _clamp_n_clusters(value: Any, default: int = 3) -> int:
    """目标组数下限 2（与 factor_ols_clusters.clamp_n_clusters 同口径）。"""
    try:
        k = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(2, k)


def promote_outliers_to_singleton_clusters(
    labels: np.ndarray,
) -> Tuple[np.ndarray, List[int]]:
    """把 label=-1 的离群票各自升为独立单票组。"""
    labs = np.asarray(labels, dtype=int).copy()
    next_id = max((int(v) for v in labs if int(v) >= 0), default=-1) + 1
    promoted: List[int] = []
    for i in range(int(labs.shape[0])):
        if int(labs[i]) < 0:
            labs[i] = next_id
            promoted.append(i)
            next_id += 1
    return labs, promoted


def _ols_coef_dict(report: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (report.get("coefficients") or {}).items():
        if k in ("intercept", "_intercept", "const"):
            continue
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _feature_union(reports: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for rep in reports:
        for key in rep.get("active_features") or list(_ols_coef_dict(rep).keys()):
            k = str(key)
            if k and k not in seen:
                seen.add(k)
                names.append(k)
    return names


def _beta_matrix(
    reports: Sequence[Dict[str, Any]],
    feature_names: Sequence[str],
) -> np.ndarray:
    rows = []
    for rep in reports:
        coefs = _ols_coef_dict(rep)
        rows.append([float(coefs.get(f, 0.0)) for f in feature_names])
    return np.asarray(rows, dtype=float)


def _l2_normalize_rows(x: np.ndarray) -> np.ndarray:
    out = np.array(x, dtype=float, copy=True)
    for i in range(out.shape[0]):
        nrm = float(np.linalg.norm(out[i]))
        if nrm > 1e-12:
            out[i] /= nrm
        else:
            out[i] = 0.0
    return out


def _winsorize_columns(x: np.ndarray, lo: float = 0.05, hi: float = 0.95) -> np.ndarray:
    """按列缩尾，削弱极端单票对距离的杠杆。"""
    out = np.array(x, dtype=float, copy=True)
    if out.ndim != 2 or out.shape[0] < 3:
        return out
    for j in range(out.shape[1]):
        col = out[:, j]
        a = float(np.quantile(col, lo))
        b = float(np.quantile(col, hi))
        if b < a:
            a, b = b, a
        out[:, j] = np.clip(col, a, b)
    return out


def _feature_zscore_columns(x: np.ndarray) -> np.ndarray:
    """缩尾后按列 z-score，避免极端票/因子主导距离。"""
    scaled, _ = fit_beta_scale_transform(x, "feature_zscore")
    return scaled


def fit_beta_scale_transform(
    raw: np.ndarray, beta_scale: str = "feature_zscore"
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """拟合尺度变换，便于把「组池 OLS β」投到与聚类同一空间再比距离。"""
    mode = resolve_beta_scale(beta_scale)
    x = np.asarray(raw, dtype=float)
    if mode == "l2":
        return _l2_normalize_rows(x), {"mode": "l2"}
    if mode == "none":
        return np.array(x, dtype=float, copy=True), {"mode": "none"}
    out = np.array(x, dtype=float, copy=True)
    n, p = out.shape if out.ndim == 2 else (0, 0)
    lo = np.zeros(p, dtype=float)
    hi = np.zeros(p, dtype=float)
    mu = np.zeros(p, dtype=float)
    sd = np.zeros(p, dtype=float)
    if n >= 3:
        for j in range(p):
            col = out[:, j]
            a = float(np.quantile(col, 0.05))
            b = float(np.quantile(col, 0.95))
            if b < a:
                a, b = b, a
            lo[j], hi[j] = a, b
            col = np.clip(col, a, b)
            out[:, j] = col
            mu[j] = float(col.mean())
            s = float(col.std())
            sd[j] = s if s > 1e-12 else 1.0
            out[:, j] = (col - mu[j]) / sd[j]
    else:
        for j in range(p):
            col = out[:, j]
            mu[j] = float(col.mean()) if n else 0.0
            s = float(col.std()) if n else 1.0
            sd[j] = s if s > 1e-12 else 1.0
            out[:, j] = (col - mu[j]) / sd[j] if n else col
            lo[j], hi[j] = mu[j], mu[j]
    return out, {
        "mode": "feature_zscore",
        "lo": lo,
        "hi": hi,
        "mu": mu,
        "sd": sd,
    }


def apply_beta_scale_transform(
    raw_vec: np.ndarray, transform: Dict[str, Any]
) -> np.ndarray:
    """把一条原始 β 投到聚类用的尺度空间。"""
    v = np.asarray(raw_vec, dtype=float).reshape(-1)
    mode = str((transform or {}).get("mode") or "feature_zscore")
    if mode == "l2":
        nrm = float(np.linalg.norm(v))
        return v / nrm if nrm > 1e-12 else v * 0.0
    if mode == "none":
        return np.array(v, dtype=float, copy=True)
    lo = np.asarray(transform["lo"], dtype=float)
    hi = np.asarray(transform["hi"], dtype=float)
    mu = np.asarray(transform["mu"], dtype=float)
    sd = np.asarray(transform["sd"], dtype=float)
    out = np.clip(v, lo, hi)
    return (out - mu) / np.where(sd > 1e-12, sd, 1.0)


def coef_vector_from_report(
    report: Dict[str, Any], feature_names: Sequence[str]
) -> np.ndarray:
    coefs = _ols_coef_dict(report or {})
    return np.asarray(
        [float(coefs.get(f, 0.0)) for f in feature_names], dtype=float
    )


def eject_far_from_group_beta(
    x_scaled: np.ndarray,
    labels: np.ndarray,
    *,
    group_beta_scaled_by_cluster: Dict[int, np.ndarray],
    tau: float,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """单票尺度化 β 到「组池 OLS β」欧氏距离 > τ → 踢出（辅校验）。"""
    labels = np.asarray(labels, dtype=int).copy()
    tau = max(float(tau), 1e-9)
    ejects: List[Dict[str, Any]] = []
    for cid, gvec in (group_beta_scaled_by_cluster or {}).items():
        g = np.asarray(gvec, dtype=float).reshape(-1)
        idx = [i for i, lab in enumerate(labels) if int(lab) == int(cid)]
        for i in idx:
            d = float(np.linalg.norm(x_scaled[i] - g))
            if d > tau + 1e-12:
                ejects.append(
                    {
                        "index": int(i),
                        "from_cluster": int(cid),
                        "distance": round(d, 4),
                        "threshold": round(tau, 4),
                        "reason": "far_from_group_beta",
                    }
                )
                labels[i] = OUTLIER_LABEL
    return labels, ejects


def beta_delta_mismatch(
    member_raw: np.ndarray,
    group_raw: np.ndarray,
    *,
    active_mask: Optional[np.ndarray] = None,
    hetero_abs: float = 0.25,
    max_abs_delta: float = 0.40,
    hetero_rel: float = 0.40,
    max_rel_delta: float = 0.75,
    abs_floor: float = 0.01,
    max_hetero_factors: int = 2,
    use_relative: bool = True,
) -> Dict[str, Any]:
    """在组活跃因子上比较单票β vs 组池β。

    默认同时看**相对** |Δβ|/scale 与绝对 |Δβ|：
    - scale = max(|β_g|, |β_m|, abs_floor)，避免小系数因子永远踢不出去。
    - 相对或绝对任一超阈即 reject。
    """
    m = np.asarray(member_raw, dtype=float).reshape(-1)
    g = np.asarray(group_raw, dtype=float).reshape(-1)
    if m.shape != g.shape:
        n = min(m.shape[0], g.shape[0])
        m, g = m[:n], g[:n]
    if active_mask is not None:
        mask = np.asarray(active_mask, dtype=bool).reshape(-1)[: m.shape[0]]
        if int(np.sum(mask)) >= 1:
            m, g = m[mask], g[mask]
    if m.size == 0:
        return {
            "reject": False,
            "max_abs_delta": 0.0,
            "mean_abs_delta": 0.0,
            "max_rel_delta": 0.0,
            "mean_rel_delta": 0.0,
            "l2": 0.0,
            "hetero_count": 0,
            "reason": None,
        }
    deltas = np.abs(m - g)
    floor = max(1e-6, float(abs_floor))
    scale = np.maximum(np.maximum(np.abs(g), np.abs(m)), floor)
    rel = deltas / scale
    max_d = float(np.max(deltas))
    mean_d = float(np.mean(deltas))
    max_r = float(np.max(rel))
    mean_r = float(np.mean(rel))
    l2 = float(np.linalg.norm(m - g))
    if use_relative:
        hetero_n = int(np.sum(rel >= float(hetero_rel)))
    else:
        hetero_n = int(np.sum(deltas >= float(hetero_abs)))
    reason = None
    if use_relative and max_r >= float(max_rel_delta):
        reason = "max_rel_delta"
    elif max_d >= float(max_abs_delta):
        reason = "max_abs_delta"
    elif hetero_n >= int(max_hetero_factors):
        reason = "hetero_factor_count"
    return {
        "reject": reason is not None,
        "max_abs_delta": round(max_d, 4),
        "mean_abs_delta": round(mean_d, 4),
        "max_rel_delta": round(max_r, 4),
        "mean_rel_delta": round(mean_r, 4),
        "l2": round(l2, 4),
        "hetero_count": hetero_n,
        "reason": reason,
    }


def eject_by_group_beta_delta(
    raw: np.ndarray,
    labels: np.ndarray,
    *,
    group_raw_by_cluster: Dict[int, np.ndarray],
    active_mask_by_cluster: Dict[int, np.ndarray],
    hetero_abs: float = 0.25,
    max_abs_delta: float = 0.40,
    hetero_rel: float = 0.40,
    max_rel_delta: float = 0.75,
    abs_floor: float = 0.01,
    max_hetero_factors: int = 2,
    use_relative: bool = True,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """按组活跃因子相对/绝对 |Δβ| 硬踢出。

    主路径仍会在组池迭代里调用；``cluster_soft_hetero`` 是并行的软降权，
    二者互补而非互相替代。
    """
    labels = np.asarray(labels, dtype=int).copy()
    raw = np.asarray(raw, dtype=float)
    ejects: List[Dict[str, Any]] = []
    for cid, gvec in (group_raw_by_cluster or {}).items():
        mask = active_mask_by_cluster.get(int(cid))
        idx = [i for i, lab in enumerate(labels) if int(lab) == int(cid)]
        for i in idx:
            stats = beta_delta_mismatch(
                raw[i],
                gvec,
                active_mask=mask,
                hetero_abs=hetero_abs,
                max_abs_delta=max_abs_delta,
                hetero_rel=hetero_rel,
                max_rel_delta=max_rel_delta,
                abs_floor=abs_floor,
                max_hetero_factors=max_hetero_factors,
                use_relative=use_relative,
            )
            if not stats["reject"]:
                continue
            ejects.append(
                {
                    "index": int(i),
                    "from_cluster": int(cid),
                    "distance": stats.get("max_rel_delta")
                    if use_relative
                    else stats["max_abs_delta"],
                    "threshold": float(max_rel_delta)
                    if use_relative
                    else float(max_abs_delta),
                    "mean_abs_delta": stats["mean_abs_delta"],
                    "max_abs_delta": stats["max_abs_delta"],
                    "max_rel_delta": stats.get("max_rel_delta"),
                    "l2": stats["l2"],
                    "hetero_count": stats["hetero_count"],
                    "reason": f"delta_beta_{stats['reason']}",
                }
            )
            labels[i] = OUTLIER_LABEL
    return labels, ejects



OUTLIER_LABEL = -1


def _centers_from_labels(x: np.ndarray, labels: np.ndarray) -> np.ndarray:
    labs = np.asarray(labels, dtype=int)
    uniq = sorted(c for c in set(int(v) for v in labs) if c >= 0)
    if not uniq:
        return np.zeros((0, x.shape[1] if x.ndim == 2 else 0))
    centers = np.zeros((len(uniq), x.shape[1]), dtype=float)
    for i, c in enumerate(uniq):
        centers[i] = x[labs == c].mean(axis=0)
    return centers


def _relabel_non_negative(labels: np.ndarray) -> np.ndarray:
    """把 ≥0 的标签压成 0..k-1；保留 -1 离群。"""
    labs = np.asarray(labels, dtype=int).copy()
    uniq = sorted(c for c in set(int(v) for v in labs) if c >= 0)
    remap = {old: i for i, old in enumerate(uniq)}
    out = np.array(
        [remap[int(v)] if int(v) >= 0 else OUTLIER_LABEL for v in labs],
        dtype=int,
    )
    return out


def _global_distance_scale(x: np.ndarray) -> float:
    """用两两距离中位数估尺度；退化时回退到到全局中心距离。"""
    x = np.asarray(x, dtype=float)
    n = int(x.shape[0])
    if n < 2:
        return 1.0
    if n <= 40:
        dist = _pairwise_euclidean(x)
        vals = [float(dist[i, j]) for i in range(n) for j in range(i + 1, n)]
    else:
        # 大样本：到全局中心的距离
        mu = x.mean(axis=0)
        vals = [float(np.linalg.norm(x[i] - mu)) for i in range(n)]
    pos = [v for v in vals if v > 1e-12]
    if not pos:
        return 1.0
    return float(np.median(pos))


def _pairwise_upper_values(x: np.ndarray) -> List[float]:
    dist = _pairwise_euclidean(x)
    n = int(x.shape[0])
    return [float(dist[i, j]) for i in range(n) for j in range(i + 1, n)]


def within_dist_tau(x: np.ndarray, quantile: float = 0.35) -> float:
    """类内直径上限 τ = 两两距离分位数。"""
    q = float(quantile)
    q = min(0.95, max(0.05, q))
    vals = _pairwise_upper_values(x)
    if not vals:
        return 1.0
    tau = float(np.quantile(vals, q))
    if not math.isfinite(tau) or tau < 1e-9:
        pos = [v for v in vals if v > 1e-12]
        tau = float(np.median(pos)) if pos else 1e-6
    return max(tau, 1e-9)


def cluster_diameter(x: np.ndarray, indices: Sequence[int]) -> float:
    idx = [int(i) for i in indices]
    if len(idx) <= 1:
        return 0.0
    dmax = 0.0
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            d = float(np.linalg.norm(x[idx[a]] - x[idx[b]]))
            if d > dmax:
                dmax = d
    return dmax


def complete_linkage_distance(
    x: np.ndarray, a_idx: Sequence[int], b_idx: Sequence[int]
) -> float:
    """两组间 complete-linkage = 最大跨组两两距离。"""
    dmax = 0.0
    for i in a_idx:
        for j in b_idx:
            d = float(np.linalg.norm(x[int(i)] - x[int(j)]))
            if d > dmax:
                dmax = d
    return dmax


def cluster_within_stats(x: np.ndarray, labels: np.ndarray) -> List[Dict[str, Any]]:
    """每组直径 / 到中心平均距（供花名册展示紧度）。"""
    x = np.asarray(x, dtype=float)
    labs = np.asarray(labels, dtype=int)
    out: List[Dict[str, Any]] = []
    for c in sorted(v for v in set(int(v) for v in labs) if v >= 0):
        idx = [i for i, lab in enumerate(labs) if int(lab) == c]
        if not idx:
            continue
        center = x[np.asarray(idx)].mean(axis=0)
        center_dists = [float(np.linalg.norm(x[i] - center)) for i in idx]
        out.append(
            {
                "cluster_id": int(c),
                "member_count": len(idx),
                "max_within_dist": round(cluster_diameter(x, idx), 4),
                "mean_center_dist": round(float(np.mean(center_dists)), 4),
            }
        )
    return out


def enforce_diameter_cap(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    tau: float,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """组内直径 > τ 时反复踢点，直到直径 ≤ τ 或只剩单票。

    优先踢**构成当前直径的端点**里离中心更远的那个（而非「离中心最远但可能不在直径上」的点）。
    """
    x = np.asarray(x, dtype=float)
    labels = np.asarray(labels, dtype=int).copy()
    tau = max(float(tau), 1e-9)
    ejects: List[Dict[str, Any]] = []
    changed = True
    while changed:
        changed = False
        uniq = sorted(c for c in set(int(v) for v in labels) if c >= 0)
        for c in uniq:
            idx = [i for i, lab in enumerate(labels) if int(lab) == c]
            if len(idx) < 2:
                continue
            diam = cluster_diameter(x, idx)
            if diam <= tau + 1e-12:
                continue
            # 找直径端点对
            best_pair = (idx[0], idx[1])
            best_d = -1.0
            for a in range(len(idx)):
                for b in range(a + 1, len(idx)):
                    d_ab = float(np.linalg.norm(x[idx[a]] - x[idx[b]]))
                    if d_ab > best_d:
                        best_d = d_ab
                        best_pair = (idx[a], idx[b])
            center = x[np.asarray(idx)].mean(axis=0)
            i0, i1 = best_pair
            d0 = float(np.linalg.norm(x[i0] - center))
            d1 = float(np.linalg.norm(x[i1] - center))
            far_i = i0 if d0 >= d1 else i1
            d = float(np.linalg.norm(x[far_i] - center))
            ejects.append(
                {
                    "index": int(far_i),
                    "from_cluster": int(c),
                    "distance": round(d, 4),
                    "threshold": round(tau, 4),
                    "diameter": round(float(best_d), 4),
                    "reason": "diameter_over_tau",
                }
            )
            labels[far_i] = OUTLIER_LABEL
            changed = True
            break
    return labels, ejects


def soft_merge_or_eject_small(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    min_size: int = 2,
    tau: float,
    absorb_far: bool = False,
) -> Tuple[np.ndarray, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """小组：优先并入合并后直径 ≤ τ 的组；否则踢离群。

    ``absorb_far=True``：无法在 τ 内合并时并入最近组（不产离群单票）。
    """
    x = np.asarray(x, dtype=float)
    labels = np.asarray(labels, dtype=int).copy()
    min_size = max(1, int(min_size))
    tau = max(float(tau), 1e-9)
    merges: List[Dict[str, Any]] = []
    ejects: List[Dict[str, Any]] = []

    if min_size <= 1:
        return labels, merges, ejects

    while True:
        uniq = sorted(c for c in set(int(v) for v in labels) if c >= 0)
        sizes = {c: int(np.sum(labels == c)) for c in uniq}
        small = [c for c, s in sizes.items() if s < min_size]
        others = [c for c in uniq if c not in small]
        if not small:
            break
        src = min(small, key=lambda c: (sizes[c], c))
        src_idx = [i for i, lab in enumerate(labels) if int(lab) == src]
        # 优先并入「合并后直径最小且 ≤ τ」的组（含其它小组，允许两单票结对）
        candidates = [c for c in uniq if c != src]
        best: Optional[Tuple[float, int]] = None
        for t in candidates:
            t_idx = [i for i, lab in enumerate(labels) if int(lab) == t]
            diam = complete_linkage_distance(x, src_idx, t_idx)
            # 合并后直径 = max(各自直径, 跨组 complete)
            diam = max(
                diam,
                cluster_diameter(x, src_idx),
                cluster_diameter(x, t_idx),
            )
            if diam <= tau + 1e-12 and (best is None or diam < best[0]):
                best = (diam, t)
        if best is not None:
            _, best_t = best
            merges.append(
                {
                    "from": int(src),
                    "to": int(best_t),
                    "moved": int(len(src_idx)),
                    "distance": round(best[0], 4),
                    "threshold": round(tau, 4),
                    "reason": "within_tau",
                }
            )
            labels[np.asarray(src_idx, dtype=int)] = best_t
            continue
        # 并不进去：踢成离群；absorb_far 则硬并最近组
        nearest_d = None
        nearest_c = None
        for t in others or candidates:
            t_idx = [i for i, lab in enumerate(labels) if int(lab) == t]
            d = complete_linkage_distance(x, src_idx, t_idx)
            if nearest_d is None or d < nearest_d:
                nearest_d, nearest_c = d, t
        if absorb_far and nearest_c is not None:
            merges.append(
                {
                    "from": int(src),
                    "to": int(nearest_c),
                    "moved": int(len(src_idx)),
                    "distance": None if nearest_d is None else round(float(nearest_d), 4),
                    "threshold": round(tau, 4),
                    "reason": "absorb_far",
                }
            )
            labels[np.asarray(src_idx, dtype=int)] = int(nearest_c)
            continue
        for i in src_idx:
            ejects.append(
                {
                    "index": int(i),
                    "from_cluster": int(src),
                    "nearest_cluster": nearest_c,
                    "distance": None if nearest_d is None else round(float(nearest_d), 4),
                    "threshold": round(tau, 4),
                    "reason": "too_far_to_merge",
                }
            )
            labels[i] = OUTLIER_LABEL
    return labels, merges, ejects


def refine_cluster_labels(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    min_size: int = 2,
    tau: float,
    strict_diameter: bool = True,
    absorb_far: bool = False,
) -> Dict[str, Any]:
    """默认：直径压到 ≤ τ → 小组近并/远踢 → 再压直径 → 重编号。

    ``strict_diameter=False``：不拆直径（配合目标 k，避免组数炸成单票堆）。
    """
    x = np.asarray(x, dtype=float)
    labels = np.asarray(labels, dtype=int).copy()
    tau = max(float(tau), 1e-9)
    eject_diam: List[Dict[str, Any]] = []
    eject_diam2: List[Dict[str, Any]] = []
    if strict_diameter:
        labels, eject_diam = enforce_diameter_cap(x, labels, tau=tau)
    labels, merges, eject_small = soft_merge_or_eject_small(
        x, labels, min_size=min_size, tau=tau, absorb_far=absorb_far
    )
    if strict_diameter:
        labels, eject_diam2 = enforce_diameter_cap(x, labels, tau=tau)
    labels, merges2, eject_small2 = soft_merge_or_eject_small(
        x, labels, min_size=min_size, tau=tau, absorb_far=absorb_far
    )
    labels = _relabel_non_negative(labels)
    centers = _centers_from_labels(x, labels)
    ejects = eject_diam + eject_small + eject_diam2 + eject_small2
    by_idx: Dict[int, Dict[str, Any]] = {}
    for e in ejects:
        by_idx[int(e["index"])] = e
    final_ejects = [
        by_idx[i]
        for i in sorted(by_idx)
        if int(labels[i]) == OUTLIER_LABEL and i in by_idx
    ]
    for i in range(int(labels.shape[0])):
        if int(labels[i]) == OUTLIER_LABEL and i not in {
            int(e["index"]) for e in final_ejects
        }:
            final_ejects.append(
                {
                    "index": int(i),
                    "from_cluster": None,
                    "distance": None,
                    "threshold": round(tau, 4),
                    "reason": "outlier",
                }
            )
    in_cluster = int(np.sum(labels >= 0))
    k = int(len(set(int(v) for v in labels if int(v) >= 0)))
    return {
        "labels": labels,
        "centers": centers,
        "merges": merges + merges2,
        "ejects": final_ejects,
        "n_clusters": k,
        "n_in_cluster": in_cluster,
        "n_outliers": int(labels.shape[0]) - in_cluster,
        "tau": round(tau, 4),
        "min_cluster_size": int(min_size),
        "distance_scale": round(_global_distance_scale(x), 4),
        "within_stats": cluster_within_stats(x, labels),
    }


def enforce_min_cluster_size(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    min_size: int = 2,
    tau: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """兼容旧接口：走 refine（直径 τ + 近并远踢）。"""
    if tau is None:
        tau = within_dist_tau(x, 0.5)
    refined = refine_cluster_labels(x, labels, min_size=min_size, tau=float(tau))
    return refined["labels"], refined["centers"], refined["merges"]


def resolve_beta_scale(
    beta_scale: Any = None,
    *,
    l2_normalize_betas: Any = None,
) -> str:
    """归一化策略：feature_zscore | l2 | none。"""
    if l2_normalize_betas is True:
        return "l2"
    s = str(beta_scale or "feature_zscore").strip().lower()
    if s in ("none", "raw", "off", "false", "0"):
        return "none"
    if s in ("l2", "row_l2", "row"):
        return "l2"
    return "feature_zscore"


def scale_beta_matrix(raw: np.ndarray, beta_scale: str) -> np.ndarray:
    scaled, _ = fit_beta_scale_transform(raw, beta_scale)
    return scaled


def kmeans_labels(
    x: np.ndarray,
    *,
    n_clusters: int,
    max_iter: int = 40,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """轻量 k-means（numpy only）；返回 labels 与 centers。"""
    n = int(x.shape[0])
    k = max(1, min(int(n_clusters), n))
    if n == 0:
        return np.zeros(0, dtype=int), np.zeros((0, x.shape[1] if x.ndim == 2 else 0))
    if k == 1:
        return np.zeros(n, dtype=int), np.asarray([x.mean(axis=0)])

    rng = np.random.default_rng(seed)
    # k-means++ 风格初始化
    centers = np.empty((k, x.shape[1]), dtype=float)
    first = int(rng.integers(0, n))
    centers[0] = x[first]
    for j in range(1, k):
        d2 = np.min(
            np.sum((x[:, None, :] - centers[None, :j, :]) ** 2, axis=2),
            axis=1,
        )
        d2 = np.maximum(d2, 0.0)
        total = float(d2.sum())
        if total <= 1e-18:
            centers[j] = x[int(rng.integers(0, n))]
        else:
            probs = d2 / total
            centers[j] = x[int(rng.choice(n, p=probs))]

    labels = np.zeros(n, dtype=int)
    for _ in range(max(1, int(max_iter))):
        dist = np.sum((x[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(dist, axis=1).astype(int)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            mask = labels == j
            if not np.any(mask):
                centers[j] = x[int(rng.integers(0, n))]
            else:
                centers[j] = x[mask].mean(axis=0)
    return labels, centers


def _pairwise_euclidean(x: np.ndarray) -> np.ndarray:
    """(n,n) 欧氏距离矩阵。"""
    n = int(x.shape[0])
    d = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            dist = float(np.linalg.norm(x[i] - x[j]))
            d[i, j] = dist
            d[j, i] = dist
    return d


def _linkage_cluster_dist(
    dist: np.ndarray,
    a: Sequence[int],
    b: Sequence[int],
    linkage: str,
) -> float:
    if linkage == "complete":
        dmax = 0.0
        for i in a:
            for j in b:
                d = float(dist[int(i), int(j)])
                if d > dmax:
                    dmax = d
        return dmax
    # average
    s = 0.0
    c = 0
    for i in a:
        for j in b:
            s += float(dist[int(i), int(j)])
            c += 1
    return s / c if c else 1e18


def _labels_centers_from_groups(
    x: np.ndarray, clusters: List[List[int]]
) -> Tuple[np.ndarray, np.ndarray]:
    n = int(x.shape[0])
    labels = np.zeros(n, dtype=int)
    centers = np.zeros((len(clusters), x.shape[1]), dtype=float)
    for cid, members in enumerate(clusters):
        for idx in members:
            labels[idx] = cid
        centers[cid] = x[np.asarray(members, dtype=int)].mean(axis=0)
    return labels, centers


def agglomerative_labels(
    x: np.ndarray,
    *,
    n_clusters: int,
    linkage: str = "complete",
) -> Tuple[np.ndarray, np.ndarray]:
    """层次聚类切到固定 k；默认 complete-linkage（控直径）。"""
    n = int(x.shape[0])
    k = max(1, min(int(n_clusters), n))
    link = str(linkage or "complete").strip().lower()
    if link not in ("complete", "average"):
        link = "complete"
    if n == 0:
        return np.zeros(0, dtype=int), np.zeros((0, x.shape[1] if x.ndim == 2 else 0))
    if k == 1:
        return np.zeros(n, dtype=int), np.asarray([x.mean(axis=0)])

    clusters: List[List[int]] = [[i] for i in range(n)]
    dist = _pairwise_euclidean(x)

    while len(clusters) > k:
        best = (1e18, -1, -1)
        m = len(clusters)
        for i in range(m):
            for j in range(i + 1, m):
                d = _linkage_cluster_dist(dist, clusters[i], clusters[j], link)
                if d < best[0]:
                    best = (d, i, j)
        _, i, j = best
        if i < 0 or j < 0:
            break
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]

    return _labels_centers_from_groups(x, clusters)


def agglomerative_cut_by_tau(
    x: np.ndarray,
    *,
    tau: float,
    linkage: str = "complete",
) -> Tuple[np.ndarray, np.ndarray]:
    """自底向上合并，仅当 linkage 距离 ≤ τ 才合并；自然多分紧组。"""
    n = int(x.shape[0])
    link = str(linkage or "complete").strip().lower()
    if link not in ("complete", "average"):
        link = "complete"
    tau = max(float(tau), 1e-9)
    if n == 0:
        return np.zeros(0, dtype=int), np.zeros((0, x.shape[1] if x.ndim == 2 else 0))
    if n == 1:
        return np.zeros(1, dtype=int), np.asarray([x.mean(axis=0)])

    clusters: List[List[int]] = [[i] for i in range(n)]
    dist = _pairwise_euclidean(x)

    while len(clusters) > 1:
        best = (1e18, -1, -1)
        m = len(clusters)
        for i in range(m):
            for j in range(i + 1, m):
                d = _linkage_cluster_dist(dist, clusters[i], clusters[j], link)
                if d < best[0]:
                    best = (d, i, j)
        d_best, i, j = best
        if i < 0 or j < 0 or d_best > tau + 1e-12:
            break
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]

    return _labels_centers_from_groups(x, clusters)


def silhouette_score(x: np.ndarray, labels: np.ndarray) -> Optional[float]:
    """平均轮廓系数；无法计算时返回 None。"""
    n = int(x.shape[0])
    labs = np.asarray(labels, dtype=int)
    uniq = sorted(set(int(v) for v in labs))
    if n < 3 or len(uniq) < 2 or len(uniq) >= n:
        return None
    dist = _pairwise_euclidean(x)
    scores: List[float] = []
    for i in range(n):
        own = int(labs[i])
        same = [j for j in range(n) if j != i and int(labs[j]) == own]
        if not same:
            continue
        a = float(np.mean([dist[i, j] for j in same]))
        b = None
        for other in uniq:
            if other == own:
                continue
            other_idx = [j for j in range(n) if int(labs[j]) == other]
            if not other_idx:
                continue
            mean_d = float(np.mean([dist[i, j] for j in other_idx]))
            if b is None or mean_d < b:
                b = mean_d
        if b is None:
            continue
        denom = max(a, b)
        if denom <= 1e-18:
            scores.append(0.0)
        else:
            scores.append((b - a) / denom)
    if not scores:
        return None
    return float(np.mean(scores))


def auto_cluster_range(n: int, *, k_min: int = 2, k_max: int = 6) -> Tuple[int, int]:
    """自动选 k 的搜索上下界。"""
    n = int(n)
    if n <= 2:
        return 1, 1
    lo = max(2, int(k_min))
    # 上限：不超过 6，且留出至少每组大致可有 2 只的空间（n 小时放宽）
    hi_cap = min(int(k_max), n - 1)
    if n >= 6:
        hi_cap = min(hi_cap, max(2, n // 2))
    hi = max(lo, hi_cap)
    return lo, hi


def default_n_clusters(n: int) -> int:
    """观察池默认目标组数：约每组 5 只（n/5），夹在 4～10。

    比 √n 略多，避免「4 组里一团过大」。
    """
    n = int(n)
    if n <= 1:
        return max(1, n)
    if n <= 4:
        return max(1, min(n - 1, 2)) if n >= 2 else 1
    k = int(round(n / 5.0))
    return max(4, min(10, k, n - 1))


def auto_k_candidates(n: int) -> List[int]:
    """自动 k 邻域：``default_n_clusters(n) ± 1``，夹在与默认相同的 4～10 / ``n-1``。

    小宇宙（n≤4）只返回中心 k，避免无意义扩搜。
    """
    n = int(n)
    k0 = default_n_clusters(n)
    if n <= 4:
        return [int(k0)]
    lo = 4
    hi = min(10, max(2, n - 1))
    if hi < lo:
        return [max(1, min(k0, n))]
    out: List[int] = []
    for k in (k0 - 1, k0, k0 + 1):
        kk = int(k)
        if lo <= kk <= hi and kk not in out:
            out.append(kk)
    if not out:
        out.append(max(lo, min(hi, int(k0))))
    return out


def default_max_cluster_size(n: int, k: int) -> int:
    """单组上限：约 1.5× 理想组均规模，至少 5。"""
    n = max(1, int(n))
    k = max(1, int(k))
    ideal = max(1, int(math.ceil(n / float(k))))
    return max(5, ideal + 2, int(math.ceil(1.5 * ideal)))


def split_oversized_clusters(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    max_size: int,
    linkage: str = "average",
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """组员过多时对超大组做二分，直到各组 ≤ max_size。"""
    x = np.asarray(x, dtype=float)
    labels = np.asarray(labels, dtype=int).copy()
    max_size = max(2, int(max_size))
    link = str(linkage or "average").strip().lower()
    if link not in ("complete", "average"):
        link = "average"
    splits: List[Dict[str, Any]] = []
    next_id = max((int(v) for v in labels if int(v) >= 0), default=-1) + 1
    guard = 0
    while guard < 64:
        guard += 1
        oversized = None
        for c in sorted(set(int(v) for v in labels if int(v) >= 0)):
            idx = [i for i, lab in enumerate(labels) if int(lab) == c]
            if len(idx) > max_size:
                oversized = (c, idx)
                break
        if oversized is None:
            break
        cid, idx = oversized
        if len(idx) < 2:
            break
        sub = x[np.asarray(idx, dtype=int)]
        sub_lab, _ = agglomerative_labels(sub, n_clusters=2, linkage=link)
        n0 = int(np.sum(sub_lab == 0))
        n1 = int(np.sum(sub_lab == 1))
        smaller = min(n0, n1)
        min_side = max(2, len(idx) // 5)
        # complete 对一团相似点常剥出 1 只 → 单票堆；改对半劈
        if n0 < 1 or n1 < 1 or smaller < min_side:
            # 退化 / 失衡：按到中心距离对半劈
            center = sub.mean(axis=0)
            order = sorted(
                range(len(idx)),
                key=lambda j: float(np.linalg.norm(sub[j] - center)),
                reverse=True,
            )
            mid = max(1, len(order) // 2)
            for j in order[:mid]:
                labels[int(idx[j])] = next_id
            splits.append(
                {
                    "from": int(cid),
                    "to": int(next_id),
                    "moved": int(mid),
                    "reason": "oversized_half",
                    "max_size": max_size,
                }
            )
        else:
            moved = 0
            for j, gi in enumerate(idx):
                if int(sub_lab[j]) == 1:
                    labels[int(gi)] = next_id
                    moved += 1
            splits.append(
                {
                    "from": int(cid),
                    "to": int(next_id),
                    "moved": int(moved),
                    "reason": "oversized_bisect",
                    "max_size": max_size,
                }
            )
        next_id += 1
    return labels, splits


def _partition_score(
    x: np.ndarray,
    labels: np.ndarray,
    *,
    min_size: int = 2,
) -> Tuple[float, Optional[float], List[int]]:
    """选 k 用的综合分：轮廓 − 单票/失衡惩罚（轮廓单独爱「大团+离群」）。"""
    labs = np.asarray(labels, dtype=int)
    k = int(len(set(int(v) for v in labs)))
    sizes = [int(np.sum(labs == c)) for c in range(k)]
    # 若标签不连续，按实际组统计
    if sum(sizes) != int(labs.shape[0]):
        uniq = sorted(set(int(v) for v in labs))
        sizes = [int(np.sum(labs == c)) for c in uniq]
    sil = silhouette_score(x, labs)
    base = float(sil) if sil is not None else -1.0
    n = int(labs.shape[0]) or 1
    singletons = sum(1 for s in sizes if s < max(1, min_size))
    max_share = max(sizes) / n if sizes else 1.0
    # 单票组几乎废掉组权，重罚；极度偏斜再罚
    score = base - 1.0 * singletons - (0.4 if max_share >= 0.85 else 0.0)
    return score, None if sil is None else float(sil), sizes


def cluster_beta_vectors(
    x: np.ndarray,
    *,
    method: str = "hierarchical",
    n_clusters: Optional[int] = None,
    seed: int = 42,
    min_cluster_size: int = 2,
    cluster_linkage: str = "average",
    within_dist_quantile: float = 0.75,
    tau: Optional[float] = None,
    auto_postprocess: Optional[bool] = None,
) -> Dict[str, Any]:
    """对已尺度化的 β 矩阵聚类。

    默认：目标 k 层次聚类（中心 ≈ n/5，夹 4～10），不做严格直径拆组。
    ``n_clusters`` 显式给定时切到该 k。
    ``auto_postprocess``：None 时仅在 ``n_clusters is None`` 为 True；
    True 时对显式 k 也走超大组二分（供 auto-k 邻域搜）。层次若传入 average
    会改 complete；**kmeans 同样劈超大组**（此前漏了，会留下 30+ 只的团）。
    """
    x = np.asarray(x, dtype=float)
    n = int(x.shape[0])
    method_s = str(method or "hierarchical").strip().lower()
    if method_s in ("auto", "hierarchical", "complete") or method_s != "kmeans":
        method_s = "hierarchical"
    link = str(cluster_linkage or "average").strip().lower()
    if link not in ("complete", "average"):
        link = "average"
    min_size = max(1, int(min_cluster_size))
    q = float(within_dist_quantile)
    tau_v = float(tau) if tau is not None else within_dist_tau(x, q)

    empty = {
        "labels": np.zeros(n, dtype=int),
        "centers": np.asarray([x.mean(axis=0)]) if n == 1 else np.zeros((0, x.shape[1])),
        "n_clusters": n,
        "method": method_s,
        "cluster_linkage": link,
        "auto_k": False,
        "cut_by_tau": False,
        "silhouette": None,
        "candidates": [],
        "merges": [],
        "ejects": [],
        "outlier_indices": [],
        "min_cluster_size": min_size,
        "within_dist_cap": round(tau_v, 4),
        "tau": round(tau_v, 4),
        "tau_quantile": q,
        "n_outliers": 0,
        "distance_scale": None,
        "within_stats": [],
        "target_k": None,
    }
    if n <= 1:
        return empty

    auto_k = n_clusters is None
    do_auto_pp = bool(auto_postprocess) if auto_postprocess is not None else auto_k
    candidates: List[Dict[str, Any]] = []
    size_splits: List[Dict[str, Any]] = []
    max_size_used: Optional[int] = None
    # 目标 k（自动或手动）都不严踢直径：否则离群升单票组会把 k=2 炸成十几组
    loose_refine = True
    split_link = link

    if method_s == "kmeans":
        k = _clamp_n_clusters(
            n_clusters if n_clusters is not None else default_n_clusters(n), 3
        )
        k = max(1, min(k, n))
        labels, _centers = kmeans_labels(x, n_clusters=k, seed=seed)
        target_k = k
        # 二分超大团用 complete，避免 kmeans 大团再被 average 粘回去
        split_link = "complete"
    elif auto_k or do_auto_pp:
        k = (
            default_n_clusters(n)
            if auto_k
            else _clamp_n_clusters(n_clusters, 3)
        )
        k = max(1, min(k, n))
        # complete 比 average 更不易并出超级大团
        link_auto = "complete" if link == "average" else link
        labels, _centers = agglomerative_labels(x, n_clusters=k, linkage=link_auto)
        link = link_auto
        split_link = link_auto
        target_k = k
    else:
        k = _clamp_n_clusters(n_clusters, 3)
        k = max(1, min(k, n))
        labels, _centers = agglomerative_labels(
            x, n_clusters=k, linkage=link
        )
        target_k = k

    if auto_k or do_auto_pp:
        max_size_used = default_max_cluster_size(n, k)
        labels, size_splits = split_oversized_clusters(
            x, labels, max_size=max_size_used, linkage=split_link
        )

    refined = refine_cluster_labels(
        x,
        labels,
        # 不并小、不踢离群；靠目标 k（自动路径另加超大组二分）控规模
        min_size=1,
        tau=tau_v,
        strict_diameter=not loose_refine,
        absorb_far=False,
    )
    labels = np.asarray(refined["labels"], dtype=int)
    # 精炼后若再胀大，再劈一次
    if do_auto_pp and max_size_used is not None:
        labels, size_splits2 = split_oversized_clusters(
            x, labels, max_size=max_size_used, linkage=split_link
        )
        size_splits = list(size_splits) + list(size_splits2)
        labels = _relabel_non_negative(labels)
    in_mask = labels >= 0
    sil_best = (
        silhouette_score(x[in_mask], labels[in_mask])
        if int(np.sum(in_mask)) >= 3
        and int(len(set(int(v) for v in labels if int(v) >= 0))) >= 2
        else None
    )
    outlier_indices = [int(i) for i in range(n) if int(labels[i]) < 0]
    centers = _centers_from_labels(x, labels)
    if auto_k or do_auto_pp:
        sizes = [
            int(np.sum(labels == c))
            for c in sorted(set(int(v) for v in labels if int(v) >= 0))
        ]
        candidates.append(
            {
                "mode": "target_k",
                "target_k": int(target_k),
                "max_cluster_size": max_size_used,
                "size_splits": len(size_splits),
                "tau": round(tau_v, 4),
                "quantile": q,
                "sizes": sizes,
                "n_outliers": len(outlier_indices),
            }
        )
    return {
        "labels": labels,
        "centers": centers,
        "n_clusters": int(len(set(int(v) for v in labels if int(v) >= 0))),
        "method": method_s,
        "cluster_linkage": link,
        "auto_k": auto_k,
        "auto_postprocess": bool(do_auto_pp),
        "cut_by_tau": False,
        "target_k": int(target_k),
        "max_cluster_size": max_size_used,
        "size_splits": size_splits,
        "silhouette": None if sil_best is None else round(float(sil_best), 4),
        "candidates": candidates,
        "merges": refined.get("merges") or [],
        "ejects": refined.get("ejects") or [],
        "outlier_indices": outlier_indices,
        "min_cluster_size": min_size,
        "within_dist_cap": round(tau_v, 4),
        "tau": round(tau_v, 4),
        "tau_quantile": q,
        "n_outliers": int(np.sum(labels < 0)),
        "distance_scale": refined.get("distance_scale"),
        "within_stats": cluster_within_stats(x, labels),
    }
