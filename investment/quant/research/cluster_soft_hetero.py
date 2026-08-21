"""组内软异质：Δβ 大的票降权再池 OLS（不拆成单票堆）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.research.cluster_partition import beta_delta_mismatch


def soft_hetero_member_weight(
    max_abs_delta: float,
    *,
    soft_delta: float = 0.25,
    w_min: float = 0.2,
    max_rel_delta: Optional[float] = None,
    soft_rel: float = 0.40,
) -> float:
    """异质度 → (0,1] 权重。优先用相对 Δβ；否则回退绝对。"""
    if max_rel_delta is not None:
        d = max(0.0, float(max_rel_delta))
        scale = max(1e-6, float(soft_rel))
    else:
        d = max(0.0, float(max_abs_delta))
        scale = max(1e-6, float(soft_delta))
    w = 1.0 / (1.0 + (d / scale) ** 2)
    return float(max(float(w_min), min(1.0, w)))


def compute_member_soft_weights(
    *,
    member_codes: Sequence[str],
    member_raw: Dict[str, np.ndarray],
    group_raw: np.ndarray,
    active_mask: Optional[np.ndarray] = None,
    soft_delta: float = 0.25,
    soft_rel: float = 0.40,
    w_min: float = 0.2,
) -> Dict[str, Dict[str, Any]]:
    """code → {weight, max_abs_delta, max_rel_delta, ...}。"""
    out: Dict[str, Dict[str, Any]] = {}
    g = np.asarray(group_raw, dtype=float).reshape(-1)
    for code in member_codes:
        c = str(code).strip()
        raw = member_raw.get(c)
        if raw is None:
            out[c] = {
                "weight": 1.0,
                "max_abs_delta": 0.0,
                "mean_abs_delta": 0.0,
                "max_rel_delta": 0.0,
                "hetero_count": 0,
            }
            continue
        stats = beta_delta_mismatch(raw, g, active_mask=active_mask)
        w = soft_hetero_member_weight(
            float(stats.get("max_abs_delta") or 0.0),
            soft_delta=soft_delta,
            w_min=w_min,
            max_rel_delta=float(stats.get("max_rel_delta") or 0.0),
            soft_rel=soft_rel,
        )
        out[c] = {
            "weight": round(w, 4),
            "max_abs_delta": stats.get("max_abs_delta"),
            "mean_abs_delta": stats.get("mean_abs_delta"),
            "max_rel_delta": stats.get("max_rel_delta"),
            "hetero_count": stats.get("hetero_count"),
        }
    return out


def expand_member_weights_to_rows(
    row_codes: Sequence[str],
    member_weights: Dict[str, Dict[str, Any]],
    *,
    default: float = 1.0,
) -> List[float]:
    """把 per-code 权展开到逐行样本权。"""
    out: List[float] = []
    for code in row_codes:
        meta = member_weights.get(str(code).strip()) or {}
        try:
            w = float(meta.get("weight", default))
        except (TypeError, ValueError):
            w = float(default)
        if not np.isfinite(w) or w <= 0:
            w = float(default)
        out.append(float(w))
    return out


def build_pooled_rows_with_codes(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[float], List[str]]:
    """组内拼接面板，并记录每行所属 code（供软异质加权）。"""
    all_xs: List[Dict[str, Any]] = []
    all_ys: List[float] = []
    row_codes: List[str] = []
    for code in members:
        c = str(code).strip()
        panel = panel_by_code.get(c) or {}
        xs = list(panel.get("xs") or [])
        ys = list(panel.get("ys") or [])
        n = min(len(xs), len(ys))
        for i in range(n):
            try:
                yv = float(ys[i])
            except (TypeError, ValueError):
                continue
            row = xs[i]
            if not isinstance(row, dict):
                continue
            all_xs.append(row)
            all_ys.append(yv)
            row_codes.append(c)
    return all_xs, all_ys, row_codes
