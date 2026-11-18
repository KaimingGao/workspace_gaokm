"""模型中心 Arm·R：四头岭回归。

回归头直接拟合收益。排序头的训练标签是日内稠密名次（收益越高名次越大），
打分仍是连续值，评测对照的是实现收益。截距不惩罚。不写研究套 / 执行套。
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np


def average_ranks(values: np.ndarray) -> np.ndarray:
    """升序并列取平均名次，名次从 1 起。数值越大名次越大。"""
    v = np.asarray(values, dtype=np.float64)
    n = int(v.shape[0])
    ranks = np.empty(n, dtype=np.float64)
    if n == 0:
        return ranks
    order = np.argsort(v, kind="mergesort")
    sorted_v = v[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_v[j] == sorted_v[i]:
            j += 1
        ranks[order[i:j]] = 0.5 * ((i + 1) + j)
        i = j
    return ranks


def dense_rank_targets(
    y: np.ndarray,
    dates: Sequence[str],
    train_idx: Sequence[int],
    *,
    min_names: int = 8,
) -> Tuple[np.ndarray, List[int]]:
    """训练行按日换成稠密名次。不足 ``min_names`` 的交易日不进拟合。"""
    out = np.asarray(y, dtype=np.float64).copy()
    groups: dict = {}
    n = int(out.shape[0])
    for raw in train_idx:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i < 0 or i >= n or not np.isfinite(out[i]):
            continue
        groups.setdefault(str(dates[i])[:10], []).append(i)
    keep: List[int] = []
    need = max(2, int(min_names))
    for idxs in groups.values():
        if len(idxs) < need:
            continue
        pos = np.asarray(idxs, dtype=np.int64)
        out[pos] = average_ranks(out[pos])
        keep.extend(idxs)
    return out, keep


def fit_ridge(
    X: np.ndarray,
    y: np.ndarray,
    alpha: float,
) -> Optional[Tuple[float, np.ndarray]]:
    """带截距的岭回归。样本不足或求解失败返回 None。"""
    from core.research.panel_matrix import _ridge_solve_np

    mat = np.asarray(X, dtype=np.float64)
    target = np.asarray(y, dtype=np.float64).reshape(-1)
    n = int(target.shape[0])
    if n < 24 or mat.ndim != 2 or int(mat.shape[0]) != n or int(mat.shape[1]) == 0:
        return None
    if not np.isfinite(mat).all() or not np.isfinite(target).all():
        return None
    design = np.column_stack([np.ones(n, dtype=np.float64), mat])
    beta = _ridge_solve_np(design, target, float(alpha))
    if beta is None or int(np.asarray(beta).shape[0]) != int(design.shape[1]):
        return None
    coef = np.asarray(beta[1:], dtype=np.float64).reshape(-1)
    return float(beta[0]), coef


def predict_ridge(X: np.ndarray, intercept: float, coef: Sequence[float]) -> np.ndarray:
    mat = np.asarray(X, dtype=np.float64)
    weights = np.asarray(coef, dtype=np.float64).reshape(-1)
    if mat.ndim != 2 or int(mat.shape[1]) != int(weights.shape[0]):
        raise ValueError("因子列数与权重不一致")
    if int(mat.shape[0]) == 0:
        return np.zeros((0,), dtype=np.float64)
    return np.asarray(float(intercept) + mat @ weights, dtype=np.float64).reshape(-1)
