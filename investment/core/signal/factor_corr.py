"""截面因子相关性（研究只读，V2.1 去冗）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def pearson_with_reason(
    xs: List[float], ys: List[float]
) -> Tuple[Optional[float], Optional[str]]:
    """Pearson 相关；失败时返回原因码 sparse|constant|flat。"""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None, "sparse"
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x < 1e-12:
        return None, "constant"
    if den_y < 1e-12:
        return None, "flat"
    return num / (den_x * den_y), None


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    corr, _reason = pearson_with_reason(xs, ys)
    return corr


def compute_factor_corr_matrix(
    items: List[dict],
    *,
    factor_names: Optional[List[str]] = None,
    min_samples: int = 3,
) -> Dict[str, Any]:
    """对一批 signal_item 的 sub_scores 算 pairwise Pearson。"""
    if not items:
        return {
            "success": False,
            "error": "empty",
            "factors": [],
            "matrix": {},
            "pairs": [],
        }

    names = list(factor_names or [])
    if not names:
        keys = set()
        for it in items:
            keys.update((it.get("sub_scores") or {}).keys())
        names = sorted(keys)

    columns: Dict[str, List[float]] = {f: [] for f in names}
    for it in items:
        subs = it.get("sub_scores") or {}
        row = {}
        ok = True
        for f in names:
            if f not in subs:
                ok = False
                break
            row[f] = float(subs[f])
        if not ok:
            continue
        for f in names:
            columns[f].append(row[f])

    sample_count = min((len(columns[f]) for f in names), default=0) if names else 0
    matrix: Dict[str, Dict[str, Optional[float]]] = {f: {} for f in names}
    pairs: List[Dict[str, Any]] = []

    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                matrix[a][b] = 1.0
                continue
            if sample_count < min_samples:
                matrix[a][b] = None
                continue
            corr = _pearson(columns[a], columns[b])
            matrix[a][b] = round(corr, 4) if corr is not None else None
            if i < j and corr is not None:
                pairs.append(
                    {
                        "a": a,
                        "b": b,
                        "corr": round(corr, 4),
                        "abs_corr": round(abs(corr), 4),
                    }
                )

    pairs.sort(key=lambda r: r["abs_corr"], reverse=True)
    return {
        "success": True,
        "sample_count": sample_count,
        "factors": names,
        "matrix": matrix,
        "pairs": pairs,
        "note": "截面 sub_scores 相关；|corr| 高提示冗余，不自动改权重。",
    }


def redundancy_warnings_from_corr(
    corr_report: Dict[str, Any],
    *,
    threshold: float = 0.7,
    factor_groups: Optional[Dict[str, List[str]]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """高相关对 + 可选组内权重合计提示。"""
    warnings: List[Dict[str, Any]] = []
    for row in corr_report.get("pairs") or []:
        if float(row.get("abs_corr") or 0) >= threshold:
            warnings.append(
                {
                    "type": "high_corr",
                    "a": row["a"],
                    "b": row["b"],
                    "corr": row["corr"],
                    "message": (
                        f"{row['a']} ↔ {row['b']} corr={row['corr']:+.3f} "
                        f"（≥{threshold}，建议检查是否冗余）"
                    ),
                }
            )

    wmap = weights or {}
    for group, members in (factor_groups or {}).items():
        total = sum(float(wmap.get(m, 0.0) or 0.0) for m in members)
        if total > 0.45:
            warnings.append(
                {
                    "type": "group_weight",
                    "group": group,
                    "members": list(members),
                    "weight_sum": round(total, 3),
                    "message": (
                        f"因子组 {group} 合计权重 {total:.3f} > 0.45，"
                        "技术/同族过重可能伤害截面区分度"
                    ),
                }
            )
    return warnings
