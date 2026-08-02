"""按单票 OLS β 相似度聚类，使同组可共用建模、异组各用各的（研究用，不写盘）。

目的：OLS 表现相似的股票进同一组 → 组内池 OLS + 共用小步权；不同组独立建模。
默认宇宙=观察池。流程：逐票 OLS → β 缩尾+z-score → average·目标 k（≈√N，3～8）
→ 多票组池 OLS → 小步建议权。异质用探针核对（自动路径不再拆成单票堆）。

``beta_scale``：
- ``feature_zscore``（默认）：列缩尾 + 因子维 z-score
- ``l2``：行 L2 归一只比方向
- ``none``：原始 β（易被极端票主导）
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.research.factor_ols import (
    clamp_ridge_lambda,
    collect_subscore_forward_panel,
    fit_factor_ols_from_panel,
)


def clamp_n_clusters(value: Any, default: int = 3) -> int:
    try:
        k = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(2, min(k, 12))


def clamp_watching_limit(value: Any, default: int = 8) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(3, min(n, 20))


def merge_cluster_universe(
    watchlist: Sequence[Any],
    holdings: Sequence[Any],
    *,
    watching_limit: int = 8,
    universe_mode: str = "watching",
) -> Dict[str, Any]:
    """构建聚类宇宙。

    默认 ``universe_mode=watching``：**全部**观察池（不截前 N）。
    ``holdings``：仅纸面持仓（旧默认）。
    ``union``：观察池前 N ∪ 全部纸面持仓（``watching_limit`` 仅约束观察侧）。
    """
    limit = clamp_watching_limit(watching_limit, 8)
    mode = str(universe_mode or "watching").strip().lower()
    if mode in ("paper", "holding", "holdings_only"):
        mode = "holdings"
    if mode in ("watch", "watchlist", "watching_only"):
        mode = "watching"

    holdings_codes: List[str] = []
    seen_h: set = set()
    for h in holdings or []:
        if isinstance(h, dict):
            c = str(h.get("stock_code") or "").strip()
        else:
            c = str(h or "").strip()
        if not c or c in seen_h:
            continue
        seen_h.add(c)
        holdings_codes.append(c)

    # 去重保序
    watch_all: List[str] = []
    seen_w: set = set()
    for c in watchlist or []:
        code = str(c).strip()
        if not code or code in seen_w:
            continue
        seen_w.add(code)
        watch_all.append(code)

    if mode == "watching":
        watching_codes = list(watch_all)
        code_roles = {
            c: {
                "from_watching": True,
                "from_holdings": c in seen_h,
                "holdings_added": False,
            }
            for c in watching_codes
        }
        return {
            "codes": list(watching_codes),
            "watching_codes": list(watching_codes),
            "holdings_codes": list(holdings_codes),
            "holdings_added": [],
            # 全量模式：limit 记实际只数，便于 UI/报告展示
            "watching_limit": len(watching_codes),
            "universe_count": len(watching_codes),
            "universe_mode": "watching",
            "code_roles": code_roles,
        }

    watching_codes = watch_all[:limit]

    if mode == "holdings":
        code_roles = {
            c: {
                "from_watching": False,
                "from_holdings": True,
                "holdings_added": True,
            }
            for c in holdings_codes
        }
        return {
            "codes": list(holdings_codes),
            "watching_codes": [],
            "holdings_codes": list(holdings_codes),
            "holdings_added": list(holdings_codes),
            "watching_limit": limit,
            "universe_count": len(holdings_codes),
            "universe_mode": "holdings",
            "code_roles": code_roles,
        }

    # union
    watch_set = set(watching_codes)
    holdings_added = [c for c in holdings_codes if c not in watch_set]
    codes: List[str] = []
    seen: set = set()
    code_roles: Dict[str, Dict[str, bool]] = {}
    for c in watching_codes + holdings_codes:
        if c not in code_roles:
            code_roles[c] = {
                "from_watching": False,
                "from_holdings": False,
                "holdings_added": False,
            }
        if c in watch_set:
            code_roles[c]["from_watching"] = True
        if c in seen_h:
            code_roles[c]["from_holdings"] = True
        if c not in watch_set and c in seen_h:
            code_roles[c]["holdings_added"] = True
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)
    return {
        "codes": codes,
        "watching_codes": watching_codes,
        "holdings_codes": holdings_codes,
        "holdings_added": holdings_added,
        "watching_limit": limit,
        "universe_count": len(codes),
        "universe_mode": "union",
        "code_roles": code_roles,
    }


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
    max_hetero_factors: int = 2,
) -> Dict[str, Any]:
    """在组活跃因子上比较单票β vs 组池β（与探针 Δβ/异质同口径）。"""
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
            "l2": 0.0,
            "hetero_count": 0,
            "reason": None,
        }
    deltas = np.abs(m - g)
    max_d = float(np.max(deltas))
    mean_d = float(np.mean(deltas))
    l2 = float(np.linalg.norm(m - g))
    hetero_n = int(np.sum(deltas >= float(hetero_abs)))
    reason = None
    if max_d >= float(max_abs_delta):
        reason = "max_abs_delta"
    elif hetero_n >= int(max_hetero_factors):
        reason = "hetero_factor_count"
    return {
        "reject": reason is not None,
        "max_abs_delta": round(max_d, 4),
        "mean_abs_delta": round(mean_d, 4),
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
    max_hetero_factors: int = 2,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """按组活跃因子上的 |Δβ| 踢出：与探针「异质」对齐，避免 002594∈G1 仍巨差。"""
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
                max_hetero_factors=max_hetero_factors,
            )
            if not stats["reject"]:
                continue
            ejects.append(
                {
                    "index": int(i),
                    "from_cluster": int(cid),
                    "distance": stats["max_abs_delta"],
                    "threshold": float(max_abs_delta),
                    "mean_abs_delta": stats["mean_abs_delta"],
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
    """组内直径 > τ 时反复踢出离中心最远的点，直到直径 ≤ τ 或只剩单票。"""
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
            center = x[np.asarray(idx)].mean(axis=0)
            far_i = max(idx, key=lambda i: float(np.linalg.norm(x[i] - center)))
            d = float(np.linalg.norm(x[far_i] - center))
            ejects.append(
                {
                    "index": int(far_i),
                    "from_cluster": int(c),
                    "distance": round(d, 4),
                    "threshold": round(tau, 4),
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
        if n0 < 1 or n1 < 1:
            # 退化：按到中心距离对半劈
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
) -> Dict[str, Any]:
    """对已尺度化的 β 矩阵聚类。

    默认：average-linkage 切到目标 k（≈√n，3～8），不做严格直径拆组。
    ``n_clusters`` 显式给定时切到该 k。
    ``method=kmeans`` 仍可用。
    """
    x = np.asarray(x, dtype=float)
    n = int(x.shape[0])
    method_s = str(method or "hierarchical").strip().lower()
    if method_s in ("auto", "hierarchical", "complete"):
        method_s = "hierarchical"
    elif method_s != "kmeans":
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
    candidates: List[Dict[str, Any]] = []
    size_splits: List[Dict[str, Any]] = []
    max_size_used: Optional[int] = None
    # 自动：目标 k + 超大组二分；不 absorb_far（否则易塌成少数大团）
    loose_refine = bool(auto_k)

    if method_s == "kmeans":
        k = clamp_n_clusters(
            n_clusters if n_clusters is not None else default_n_clusters(n), 3
        )
        k = max(1, min(k, n))
        labels, _centers = kmeans_labels(x, n_clusters=k, seed=seed)
        target_k = k
    elif auto_k:
        k = default_n_clusters(n)
        k = max(1, min(k, n))
        # complete 比 average 更不易并出超级大团
        link_auto = "complete" if link == "average" else link
        labels, _centers = agglomerative_labels(x, n_clusters=k, linkage=link_auto)
        link = link_auto
        target_k = k
        max_size_used = default_max_cluster_size(n, k)
        labels, size_splits = split_oversized_clusters(
            x, labels, max_size=max_size_used, linkage=link_auto
        )
    else:
        k = clamp_n_clusters(n_clusters, 3)
        k = max(1, min(k, n))
        labels, _centers = agglomerative_labels(
            x, n_clusters=k, linkage=link
        )
        target_k = k

    refined = refine_cluster_labels(
        x,
        labels,
        # 自动路径：不并小、不踢离群（否则又单票化）；靠目标 k + 超大组二分控规模
        min_size=1 if auto_k else min_size,
        tau=tau_v,
        strict_diameter=not loose_refine,
        absorb_far=False,
    )
    labels = np.asarray(refined["labels"], dtype=int)
    # 精炼后若再胀大，再劈一次
    if auto_k and max_size_used is not None:
        labels, size_splits2 = split_oversized_clusters(
            x, labels, max_size=max_size_used, linkage=link
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
    if auto_k:
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


def _draft_weights_from_ols(
    ols_report: Dict[str, Any],
    *,
    ic_panel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组内建议权：OLS β 优先（按 |β| 放大）；无 β 时用组内 IC 补位。"""
    from core.signal.weight_suggest import suggest_weights_from_ic

    factors: List[Any] = []
    ic_mode_panel = ""
    if isinstance(ic_panel, dict):
        factors = list(ic_panel.get("factors") or ic_panel.get("rows") or [])
        ic_mode_panel = str(ic_panel.get("mode") or "")

    # 组内截面 IC 有日序列 → 要求 ICIR；单票时序回退则不要求
    require_icir = ic_mode_panel == "group_cs_ic"

    return suggest_weights_from_ic(
        {"factors": factors, "success": True},
        ols_report=ols_report,
        ic_mode="ols_cluster",
        require_icir=require_icir,
        min_samples=6,
        min_ic=0.02,
        max_delta=0.06,
        ols_delta=0.05,
        min_ols_beta=0.02,
        weak_ic_decay=0.01,
        ols_scale_by_beta=True,
        ols_scale_cap=3.0,
        prefer_ols=True,
    )


def _weight_suggest_public(draft: Dict[str, Any]) -> Dict[str, Any]:
    """一组一表 / 悬停注释所需字段（含 Δ 来源与 rationale）。"""
    if not isinstance(draft, dict):
        return {"success": False, "error": "无建议"}
    if not draft.get("success"):
        return {
            "success": False,
            "error": draft.get("error") or "建议失败",
        }
    return {
        "success": True,
        "suggested_weights": draft.get("suggested_weights"),
        "current_weights": draft.get("current_weights"),
        "deltas": draft.get("deltas") or {},
        "delta_sources": draft.get("delta_sources") or {},
        "rationale": list(draft.get("rationale") or [])[:24],
        "constraint_warnings": draft.get("constraint_warnings") or [],
        "params": draft.get("params") or {},
        "ic_mode": draft.get("ic_mode"),
        "note": draft.get("note"),
    }


def group_ts_ic_panel(
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    feature_names: Sequence[str],
) -> Dict[str, Any]:
    """单票组回退：堆叠时序 IC（无日截面序列，ICIR 恒 null）。"""
    from core.signal.factor_corr import pearson_with_reason

    rows: List[Dict[str, Any]] = []
    exclusion_reasons: Dict[str, str] = {}
    y_list = list(ys or [])
    x_list = list(xs or [])
    n_pair = min(len(x_list), len(y_list))
    for f in feature_names or []:
        name = str(f).strip()
        if not name:
            continue
        fx: List[float] = []
        fy: List[float] = []
        for i in range(n_pair):
            row = x_list[i]
            if not isinstance(row, dict):
                continue
            v = row.get(name)
            if v is None:
                continue
            try:
                fx.append(float(v))
                fy.append(float(y_list[i]))
            except (TypeError, ValueError):
                continue
        ic, reason = pearson_with_reason(fx, fy)
        if reason:
            exclusion_reasons[name] = reason
        rows.append(
            {
                "factor": name,
                "name": name,
                "ic": round(float(ic), 4) if ic is not None else None,
                "icir": None,
                "sample_count": len(fx),
                "exclusion_reason": reason,
            }
        )
    return {
        "success": True,
        "mode": "group_ts_ic",
        "rows": rows,
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "note": "单票组时序 IC 回退（非截面）；ICIR 不适用",
    }


def group_cs_ic_panel(
    stock_bars: Dict[str, List[dict]],
    feature_names: Sequence[str],
    *,
    horizon_days: int = 3,
    pit_fundamentals: bool = False,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """组内按日截面 IC → ICIR（宇宙=组员；与研究池 factor_cs_ic 同口径）。"""
    from core.backtest.factor_cs_ic import compute_factor_cross_section_ic

    names = [str(f).strip() for f in (feature_names or []) if str(f).strip()]
    bars_map = {
        str(c): list(b)
        for c, b in (stock_bars or {}).items()
        if str(c).strip() and b
    }
    n_stocks = len(bars_map)
    if n_stocks < 2:
        empty_rows = [
            {
                "factor": name,
                "name": name,
                "ic": None,
                "icir": None,
                "sample_count": 0,
                "exclusion_reason": "sparse",
            }
            for name in names
        ]
        return {
            "success": True,
            "ok": False,
            "mode": "group_cs_ic",
            "rows": empty_rows,
            "factors": empty_rows,
            "exclusion_reasons": {r["factor"]: "sparse" for r in empty_rows},
            "stock_count": n_stocks,
            "note": "组员不足 2，无法做组内截面 IC",
        }

    min_names = 2 if n_stocks == 2 else 3
    out = compute_factor_cross_section_ic(
        bars_map,
        horizon_days=horizon_days,
        min_history=12,
        max_window=30,
        min_names=min_names,
        fundamentals_by_code=fundamentals_by_code,
        pit_fundamentals=bool(pit_fundamentals),
        factor_names=names or None,
    )
    if not out.get("success"):
        empty_rows = [
            {
                "factor": name,
                "name": name,
                "ic": None,
                "icir": None,
                "sample_count": 0,
                "exclusion_reason": "other",
            }
            for name in names
        ]
        return {
            "success": True,
            "ok": False,
            "mode": "group_cs_ic",
            "rows": empty_rows,
            "factors": empty_rows,
            "exclusion_reasons": {r["factor"]: "other" for r in empty_rows},
            "stock_count": n_stocks,
            "min_names": min_names,
            "error": out.get("error"),
            "note": f"组内截面 IC 失败：{out.get('error') or 'unknown'}",
        }

    by_fac = {
        str(r.get("factor") or r.get("name") or ""): r
        for r in (out.get("factors") or [])
        if isinstance(r, dict)
    }
    rows: List[Dict[str, Any]] = []
    exclusion_reasons: Dict[str, str] = {}
    for name in names or list(by_fac.keys()):
        src = by_fac.get(name) or {}
        reason = src.get("exclusion_reason")
        if reason:
            exclusion_reasons[name] = str(reason)
        pear = src.get("pearson") if isinstance(src.get("pearson"), dict) else {}
        n_days = src.get("sample_count")
        if n_days is None:
            n_days = pear.get("day_count")
        icir = src.get("icir")
        if icir is None:
            icir = pear.get("icir")
        ic = src.get("ic")
        if ic is None:
            ic = pear.get("ic_mean")
        rows.append(
            {
                "factor": name,
                "name": name,
                "ic": ic,
                "icir": icir,
                "sample_count": int(n_days or 0),
                "exclusion_reason": reason,
                "pearson": src.get("pearson"),
                "spearman": src.get("spearman"),
            }
        )
    return {
        "success": True,
        "ok": bool(out.get("ok")),
        "mode": "group_cs_ic",
        "rows": rows,
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "horizon_days": out.get("horizon_days") or horizon_days,
        "min_names": min_names,
        "stock_count": n_stocks,
        "day_count": out.get("day_count"),
        "pit_fundamentals": bool(pit_fundamentals),
        "score_ic": out.get("score_ic"),
        "note": "组内按日截面 IC → ICIR（宇宙=组员；非全市场）",
    }


def _member_bars_and_funds(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, List[dict]], Dict[str, dict]]:
    bars_map: Dict[str, List[dict]] = {}
    funds: Dict[str, dict] = {}
    for code in members or []:
        c = str(code).strip()
        panel = panel_by_code.get(c) or {}
        bars = panel.get("bars") or []
        if not bars:
            continue
        bars_map[c] = list(bars)
        fund = panel.get("fundamentals")
        if isinstance(fund, dict) and fund:
            funds[c] = fund
    return bars_map, funds


def _cluster_factor_ic_panel(
    members: Sequence[str],
    *,
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    horizon_days: int,
    pit_fundamentals: bool,
    all_xs: Sequence[Dict[str, Any]],
    all_ys: Sequence[float],
) -> Dict[str, Any]:
    """多票组：组内日截面 IC→ICIR；单票组：时序 IC 回退。"""
    if len(members) < 2:
        return group_ts_ic_panel(all_xs, all_ys, feature_names)
    bars_map, funds = _member_bars_and_funds(members, panel_by_code)
    return group_cs_ic_panel(
        bars_map,
        feature_names,
        horizon_days=horizon_days,
        pit_fundamentals=pit_fundamentals,
        fundamentals_by_code=funds or None,
    )


def compute_factor_ols_cluster_report(
    stock_panels: List[Dict[str, Any]],
    *,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    n_clusters: Optional[int] = None,
    pit_fundamentals: bool = False,
    l2_normalize_betas: Optional[bool] = None,
    beta_scale: str = "feature_zscore",
    cluster_method: str = "hierarchical",
    cluster_linkage: str = "average",
    within_dist_quantile: float = 0.75,
) -> Dict[str, Any]:
    """逐票 OLS → β 聚类 → 组内池 OLS + 小步权。

    默认 average-linkage 切到目标 k≈√N（3～8）；显式 ``n_clusters`` 时才严踢异质。
    """
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    k_req = None if n_clusters is None else clamp_n_clusters(n_clusters, 3)
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    use_pit = bool(pit_fundamentals)
    scale_mode = resolve_beta_scale(beta_scale, l2_normalize_betas=l2_normalize_betas)
    method_s = str(cluster_method or "hierarchical").strip().lower()
    link_s = str(cluster_linkage or "average").strip().lower()
    tau_q = float(within_dist_quantile)

    per_stock: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    panel_by_code: Dict[str, Dict[str, Any]] = {}

    for item in stock_panels or []:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code:
            continue
        if not bars:
            skipped.append({"code": code, "reason": "无日线"})
            continue
        xs, ys = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            pit_fundamentals=use_pit,
        )
        if not ys:
            skipped.append({"code": code, "reason": "面板为空"})
            continue
        fit = fit_factor_ols_from_panel(
            xs,
            ys,
            horizon_days=horizon_days,
            fundamentals_used=bool(item.get("fundamentals")) or use_pit,
            pit_fundamentals=use_pit,
            mode="single",
            stock_codes=[code],
            ridge_lambda=lam,
        )
        if not fit.get("success"):
            skipped.append(
                {"code": code, "reason": str(fit.get("error") or "单票 OLS 失败")}
            )
            continue
        fit["stock_code"] = code
        per_stock.append(fit)
        panel_by_code[code] = {
            "code": code,
            "bars": bars,
            "index_bars": item.get("index_bars"),
            "fundamentals": item.get("fundamentals"),
            "xs": xs,
            "ys": ys,
        }

    if len(per_stock) < 2:
        return {
            "success": False,
            "error": "有效单票 OLS 不足 2 只，无法按 β 聚类",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": len(per_stock),
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": k_req,
        }

    feature_names = _feature_union(per_stock)
    if len(feature_names) < 2:
        return {
            "success": False,
            "error": "入模因子过少，无法按 β 向量聚类",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": len(per_stock),
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": k_req,
        }

    codes = [str(r.get("stock_code")) for r in per_stock]
    raw = _beta_matrix(per_stock, feature_names)
    x, scale_tf = fit_beta_scale_transform(raw, scale_mode)
    clustered = cluster_beta_vectors(
        x,
        method=method_s,
        n_clusters=k_req,
        seed=42,
        cluster_linkage=link_s,
        within_dist_quantile=tau_q,
    )
    labels = np.asarray(clustered["labels"], dtype=int)
    tau_used = float(clustered.get("within_dist_cap") or clustered.get("tau") or 1.0)
    pool_ejects: List[Dict[str, Any]] = []
    # 自动目标 k：不再做组池 Δβ/直径踢出（否则又拆回数十单票组）；异质看探针
    # 仅当调用方显式指定 n_clusters 时保留严踢
    run_pool_eject = k_req is not None

    if run_pool_eject:
        for _round in range(3):
            uniq = sorted(c for c in set(int(v) for v in labels) if c >= 0)
            group_raw_map: Dict[int, np.ndarray] = {}
            active_mask_map: Dict[int, np.ndarray] = {}
            group_scaled: Dict[int, np.ndarray] = {}
            for cid in uniq:
                member_idx = [i for i, lab in enumerate(labels) if int(lab) == cid]
                if len(member_idx) < 2:
                    continue
                members = [codes[i] for i in member_idx]
                all_xs: List[Dict[str, Optional[float]]] = []
                all_ys: List[float] = []
                for code in members:
                    panel = panel_by_code.get(code) or {}
                    all_xs.extend(panel.get("xs") or [])
                    all_ys.extend(panel.get("ys") or [])
                pooled = fit_factor_ols_from_panel(
                    all_xs,
                    all_ys,
                    horizon_days=horizon_days,
                    fundamentals_used=False,
                    pit_fundamentals=use_pit,
                    mode="watching_pooled",
                    stock_codes=members,
                    ridge_lambda=lam,
                )
                if not pooled.get("success"):
                    continue
                g_raw = coef_vector_from_report(pooled, feature_names)
                group_scaled[cid] = apply_beta_scale_transform(g_raw, scale_tf)
                if len(member_idx) >= 4:
                    group_raw_map[cid] = g_raw
                    active = set(str(f) for f in (pooled.get("active_features") or []))
                    if not active:
                        active = {
                            str(f)
                            for f, v in _ols_coef_dict(pooled).items()
                            if abs(float(v)) > 1e-12
                        }
                    active_mask_map[cid] = np.asarray(
                        [fn in active for fn in feature_names], dtype=bool
                    )
            if not group_scaled and not group_raw_map:
                break
            labels, ejected_delta = (
                eject_by_group_beta_delta(
                    raw,
                    labels,
                    group_raw_by_cluster=group_raw_map,
                    active_mask_by_cluster=active_mask_map,
                    hetero_abs=0.30,
                    max_abs_delta=0.55,
                    max_hetero_factors=3,
                )
                if group_raw_map
                else (labels, [])
            )
            labels, ejected_l2 = (
                eject_far_from_group_beta(
                    x, labels, group_beta_scaled_by_cluster=group_scaled, tau=tau_used
                )
                if group_scaled
                else (labels, [])
            )
            ejected = ejected_delta + ejected_l2
            if not ejected:
                break
            pool_ejects.extend(ejected)
            refined = refine_cluster_labels(
                x,
                labels,
                min_size=2,
                tau=tau_used,
                strict_diameter=True,
                absorb_far=False,
            )
            labels = np.asarray(refined["labels"], dtype=int)
            pool_ejects.extend(refined.get("ejects") or [])

    # 离群不丢弃：各自升为单票组（自动 k 路径通常无离群）
    eject_by_idx: Dict[int, Dict[str, Any]] = {}
    for e in list(clustered.get("ejects") or []) + pool_ejects:
        if "index" in e:
            eject_by_idx[int(e["index"])] = e
    labels, promoted_idx = promote_outliers_to_singleton_clusters(labels)
    labels = _relabel_non_negative(labels)
    promoted_set = set(int(i) for i in promoted_idx)
    within_stats = cluster_within_stats(x, labels)
    within_by_id = {
        int(s["cluster_id"]): s for s in within_stats if "cluster_id" in s
    }
    k = int(len(set(int(v) for v in labels if int(v) >= 0)))
    beta_outliers: List[Dict[str, Any]] = []  # 兼容字段：升为单票组后不再「未入簇」
    singleton_outlier_groups: List[Dict[str, Any]] = []
    for i in promoted_idx:
        meta = eject_by_idx.get(int(i)) or {}
        singleton_outlier_groups.append(
            {
                "code": codes[int(i)],
                "cluster_label": f"G{int(labels[int(i)]) + 1}",
                "eject_reason": meta.get("reason") or "outlier",
                "distance": meta.get("distance"),
                "threshold": meta.get("threshold"),
            }
        )

    if k < 1:
        return {
            "success": False,
            "error": "无有效分组（拟合失败或样本不足）",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": 0,
            "fitted_count": len(codes),
            "beta_outliers": beta_outliers,
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": 0,
            "cluster_method": clustered.get("method") or method_s,
            "cluster_linkage": clustered.get("cluster_linkage") or link_s,
            "note": "无法形成分组；检查观察池标的与日线。",
            "within_dist_cap": tau_used,
            "tau_quantile": clustered.get("tau_quantile") or tau_q,
        }

    clusters: List[Dict[str, Any]] = []
    for cid in range(k):
        members = [codes[i] for i, lab in enumerate(labels) if int(lab) == cid]
        member_idx = [i for i, lab in enumerate(labels) if int(lab) == cid]
        wstat = within_by_id.get(cid) or {}
        from_outlier = bool(member_idx) and all(
            int(i) in promoted_set for i in member_idx
        )
        eject_meta = (
            eject_by_idx.get(int(member_idx[0])) if from_outlier and member_idx else {}
        ) or {}
        cluster: Dict[str, Any] = {
            "cluster_id": cid,
            "label": f"G{cid + 1}",
            "members": members,
            "member_count": len(members),
            "singleton": len(members) < 2,
            "outlier_singleton": from_outlier and len(members) < 2,
            "eject_reason": eject_meta.get("reason") if from_outlier else None,
            "max_within_dist": wstat.get("max_within_dist"),
            "mean_center_dist": wstat.get("mean_center_dist"),
            "within_dist_cap": tau_used,
        }
        all_xs = []
        all_ys: List[float] = []
        for code in members:
            panel = panel_by_code.get(code) or {}
            all_xs.extend(panel.get("xs") or [])
            all_ys.extend(panel.get("ys") or [])

        if len(members) < 2:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            if member_idx:
                one = per_stock[member_idx[0]]
                cluster["ols"] = {
                    "success": True,
                    "mode": "single",
                    "coefficients": one.get("coefficients") or {},
                    "r_squared": one.get("r_squared"),
                    "sample_count": one.get("sample_count"),
                    "active_features": one.get("active_features") or [],
                    "stock_codes": members,
                }
                draft = _draft_weights_from_ols(
                    one, ic_panel=cluster.get("factor_ic_panel")
                )
                cluster["weight_suggest"] = _weight_suggest_public(draft)
            else:
                cluster["ols"] = {"success": False, "error": "空组"}
            cluster["member_beta_gaps"] = []
            clusters.append(cluster)
            continue

        pooled = fit_factor_ols_from_panel(
            all_xs,
            all_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=use_pit,
            mode="watching_pooled",
            stock_codes=members,
            ridge_lambda=lam,
        )
        pooled["mode"] = "cluster_pooled"
        cluster["ols"] = {
            "success": bool(pooled.get("success")),
            "mode": "cluster_pooled",
            "coefficients": pooled.get("coefficients") or {},
            "r_squared": pooled.get("r_squared"),
            "sample_count": pooled.get("sample_count"),
            "active_features": pooled.get("active_features") or [],
            "excluded_features": pooled.get("excluded_features") or [],
            "stock_codes": members,
            "error": pooled.get("error"),
            "solver": pooled.get("solver"),
            "ridge_lambda": pooled.get("ridge_lambda"),
        }
        gaps: List[Dict[str, Any]] = []
        if pooled.get("success"):
            g_raw = coef_vector_from_report(pooled, feature_names)
            g_scaled = apply_beta_scale_transform(g_raw, scale_tf)
            active = set(str(f) for f in (pooled.get("active_features") or []))
            if not active:
                active = {
                    str(f)
                    for f, v in _ols_coef_dict(pooled).items()
                    if abs(float(v)) > 1e-12
                }
            amask = np.asarray(
                [fn in active for fn in feature_names], dtype=bool
            )
            for i in member_idx:
                d = float(np.linalg.norm(x[i] - g_scaled))
                dd = beta_delta_mismatch(raw[i], g_raw, active_mask=amask)
                gaps.append(
                    {
                        "code": codes[i],
                        "distance_to_group_beta": round(d, 4),
                        "max_abs_delta": dd["max_abs_delta"],
                        "mean_abs_delta": dd["mean_abs_delta"],
                        "hetero_count": dd["hetero_count"],
                        "within_cap": round(tau_used, 4),
                    }
                )
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            draft = _draft_weights_from_ols(
                pooled, ic_panel=cluster.get("factor_ic_panel")
            )
            cluster["weight_suggest"] = _weight_suggest_public(draft)
        else:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            cluster["weight_suggest"] = {
                "success": False,
                "error": pooled.get("error") or "组内池 OLS 失败",
            }
        cluster["member_beta_gaps"] = gaps
        if gaps:
            cluster["max_distance_to_group_beta"] = max(
                float(g["distance_to_group_beta"]) for g in gaps
            )
            cluster["mean_distance_to_group_beta"] = round(
                float(np.mean([g["distance_to_group_beta"] for g in gaps])), 4
            )
            cluster["max_abs_delta_to_group"] = max(
                float(g.get("max_abs_delta") or 0) for g in gaps
            )
            cluster["mean_hetero_count"] = round(
                float(np.mean([g.get("hetero_count") or 0 for g in gaps])), 2
            )
        if "factor_ic_panel" not in cluster:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
        clusters.append(cluster)

    for cl in clusters:
        coefs = _ols_coef_dict(cl.get("ols") or {})
        top = sorted(coefs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        cl["top_betas"] = [
            {"factor": f, "beta": round(b, 4)} for f, b in top if math.isfinite(b)
        ]

    in_codes = [codes[i] for i in range(len(codes)) if int(labels[i]) >= 0]
    n_singleton_out = len(singleton_outlier_groups)
    n_multi = sum(1 for c in clusters if not c.get("singleton"))
    n_pool_ej = sum(
        1
        for e in pool_ejects
        if str(e.get("reason") or "").startswith(
            ("far_from_group_beta", "delta_beta_")
        )
    )
    return {
        "success": True,
        "task": "factor_ols_clusters",
        "mode": "ols_beta_clusters",
        "horizon_days": horizon_days,
        "ridge_lambda": lam,
        "n_clusters": k,
        "n_multi_member_clusters": n_multi,
        "n_clusters_requested": k_req,
        "n_clusters_auto": bool(clustered.get("auto_k")),
        "target_k": clustered.get("target_k"),
        "max_cluster_size": clustered.get("max_cluster_size"),
        "cluster_method": clustered.get("method") or method_s,
        "cluster_linkage": clustered.get("cluster_linkage") or link_s,
        "cut_by_tau": bool(clustered.get("cut_by_tau")),
        "silhouette": clustered.get("silhouette"),
        "cluster_k_candidates": clustered.get("candidates") or [],
        "cluster_merges": clustered.get("merges") or [],
        "cluster_ejects": list(clustered.get("ejects") or []) + pool_ejects,
        "min_cluster_size": clustered.get("min_cluster_size"),
        "within_dist_cap": tau_used,
        "tau": tau_used,
        "tau_quantile": clustered.get("tau_quantile") or tau_q,
        "distance_scale": clustered.get("distance_scale"),
        "feature_names": list(feature_names),
        "stock_codes": in_codes,
        "fitted_codes": codes,
        "fitted_count": len(codes),
        "stock_count": len(in_codes),
        "beta_outliers": beta_outliers,
        "outlier_count": 0,
        "singleton_outlier_groups": singleton_outlier_groups,
        "singleton_outlier_count": n_singleton_out,
        "skipped": skipped,
        "clusters": clusters,
        "pit_fundamentals": use_pit,
        "beta_scale": scale_mode,
        "l2_normalize_betas": scale_mode == "l2",
        "cluster_balance": _cluster_balance_stats(clusters),
        "note": (
            (
                f"按单票 OLS β（{scale_mode}，列缩尾）"
                + (
                    f"目标k={clustered.get('target_k')}·{clustered.get('cluster_linkage') or link_s}"
                    if clustered.get("auto_k")
                    else (
                        f"层次聚类({clustered.get('cluster_linkage') or link_s})"
                        if (clustered.get("method") or method_s) == "hierarchical"
                        else "k-means"
                    )
                )
                + f"，共 {k} 组（其中多票组 {n_multi}）；"
            )
            + (f"单票离群组 {n_singleton_out}；" if n_singleton_out else "")
            + (f"组β校验触发 {n_pool_ej}；" if n_pool_ej else "")
            + "多票组池 OLS → 小步建议权（同组同建模）。"
            + (
                " 默认关闭逐日 PIT 财务以加速；仅作分组探针。"
                if not use_pit
                else " 已开 PIT 财务，耗时更长。"
            )
            + " 研究探针，不写 signal_config。"
        ),
    }


def _cluster_balance_stats(clusters: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    sizes = [int(c.get("member_count") or 0) for c in clusters or []]
    total = sum(sizes) or 1
    max_n = max(sizes) if sizes else 0
    singletons = sum(1 for n in sizes if n <= 1)
    max_share = round(max_n / total, 4)
    return {
        "sizes": sizes,
        "max_share": max_share,
        "singleton_groups": singletons,
        "imbalanced": bool(max_share >= 0.7 or singletons >= 1),
        "hint": (
            "组规模失衡（常见于原始 β 被极端票拉开）；已默认用因子 z-score。"
            if max_share >= 0.7 or singletons >= 1
            else ""
        ),
    }
