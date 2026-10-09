"""特征 z-score。

``feature_zscore`` 仍表示入模前做了标准化。``zscore_scope=cross_section`` 时
按决策日截面算 μ/σ（当天各股票），不把整段训练窗的均值和标准差套到每一天。
没有日期时仍可用训练窗列向 μ/σ（旧路径）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

DEFAULT_FEATURE_ZSCORE = True
ZSCORE_SCOPE_CROSS_SECTION = "cross_section"


def resolve_feature_zscore(
    *sources: Any,
    feature_zscore: Optional[bool] = None,
    default: bool = DEFAULT_FEATURE_ZSCORE,
) -> bool:
    """是否启用特征 z-score。显式参数优先于落盘字段。"""
    if feature_zscore is not None:
        return bool(feature_zscore)
    for src in sources:
        if not isinstance(src, Mapping):
            continue
        if "feature_zscore" in src and src["feature_zscore"] is not None:
            return bool(src["feature_zscore"])
        hp = src.get("hyperparams")
        if (
            isinstance(hp, Mapping)
            and "feature_zscore" in hp
            and hp["feature_zscore"] is not None
        ):
            return bool(hp["feature_zscore"])
    return bool(default)


def annotate_feature_zscore(dest: dict, enabled: bool) -> dict:
    """在报告 / hyperparams / 模型 dict 上写入 ``feature_zscore``。"""
    dest["feature_zscore"] = bool(enabled)
    return dest


def is_cross_section_zscore(doc: Any) -> bool:
    """模型是否按当天截面标准化（推理不再套冻住的训练窗 μ/σ）。"""
    if doc is None:
        return False
    if not isinstance(doc, Mapping):
        return str(getattr(doc, "zscore_scope", "") or "") == ZSCORE_SCOPE_CROSS_SECTION
    if str(doc.get("zscore_scope") or "") == ZSCORE_SCOPE_CROSS_SECTION:
        return True
    hp = doc.get("hyperparams")
    if isinstance(hp, Mapping) and str(hp.get("zscore_scope") or "") == ZSCORE_SCOPE_CROSS_SECTION:
        return True
    inner = doc.get("return_model")
    if isinstance(inner, Mapping) and inner is not doc:
        return is_cross_section_zscore(inner)
    return False


def stamp_cross_section_zscore(dest: dict) -> dict:
    """标记截面标准化，并清掉训练窗 μ/σ，避免推理再套一层。"""
    dest["feature_zscore"] = True
    dest["zscore_scope"] = ZSCORE_SCOPE_CROSS_SECTION
    dest["zscore_means"] = {}
    dest["zscore_stds"] = {}
    return dest


def dates_for_complete_rows(
    row_dates: Optional[Sequence[Any]],
    complete_idx: Sequence[Any],
) -> Optional[List[str]]:
    """完整行在原矩阵中的下标 → 决策日。对不齐则放弃截面标准化。"""
    if not row_dates or not complete_idx:
        return None
    try:
        idxs = [int(i) for i in complete_idx]
    except (TypeError, ValueError):
        return None
    if not idxs or max(idxs) >= len(row_dates) or min(idxs) < 0:
        return None
    return [str(row_dates[i])[:10] for i in idxs]


def cross_section_zscore_matrix(
    x: Any,
    groups: Sequence[Any],
    *,
    min_names: int = 2,
    eps: float = 1e-12,
) -> Any:
    """按组（通常是交易日）对每列做总体 z-score。缺测保持 NaN。

    组内有效样本少于 ``min_names``，或标准差接近 0 时，有效值记 0（落在当天均值上）。
    """
    import numpy as np

    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 2:
        return arr
    out = np.array(arr, copy=True)
    n, p = out.shape
    if n == 0 or p == 0 or len(groups) != n:
        return out
    buckets: Dict[str, List[int]] = {}
    for i, g in enumerate(groups):
        buckets.setdefault(str(g or "")[:10], []).append(i)
    need = max(1, int(min_names))
    for idxs in buckets.values():
        ix = np.asarray(idxs, dtype=np.int64)
        for j in range(p):
            col = out[ix, j]
            finite = np.isfinite(col)
            m = int(finite.sum())
            if m < need:
                if m:
                    out[ix[finite], j] = 0.0
                continue
            vals = col[finite]
            mu = float(vals.mean())
            var = float(np.mean((vals - mu) ** 2))
            if (not math.isfinite(var)) or var <= eps:
                out[ix[finite], j] = 0.0
                continue
            sd = math.sqrt(var)
            if sd < 1e-12:
                out[ix[finite], j] = 0.0
            else:
                out[ix[finite], j] = (vals - mu) / sd
    return out


def cross_section_zscore_dicts(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    groups: Optional[Sequence[Any]] = None,
) -> List[dict]:
    """对一组特征 dict 做截面 z-score。缺测键保持缺测。``groups`` 缺省时视为同一天。"""
    import numpy as np

    keys = [str(n) for n in names]
    n = len(rows)
    if groups is None:
        groups = ["_"] * n
    mat = np.full((n, len(keys)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        src = row if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            v = src.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                mat[i, j] = fv
    z = cross_section_zscore_matrix(mat, groups)
    out: List[dict] = []
    for i, row in enumerate(rows):
        src = dict(row) if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            val = float(z[i, j])
            if math.isfinite(val):
                src[name] = val
        out.append(src)
    return out
