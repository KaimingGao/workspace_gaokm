"""训练标签全局去均值：拟合用 y−μ，截距/预测加回 μ。

与特征 ``feature_zscore`` 独立。默认关（原始 label）；开启后落盘
``y_demeaned`` / ``y_label_mean``，回测读模型截距即可（μ 已并入 intercept）。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

DEFAULT_LABEL_DEMEAN = False


def resolve_label_demean(
    *sources: Any,
    label_demean: Optional[bool] = None,
    default: bool = DEFAULT_LABEL_DEMEAN,
) -> bool:
    if label_demean is not None:
        return bool(label_demean)
    for src in sources:
        if not isinstance(src, Mapping):
            continue
        if "label_demean" in src and src["label_demean"] is not None:
            return bool(src["label_demean"])
        if "y_demeaned" in src and src["y_demeaned"] is not None:
            return bool(src["y_demeaned"])
    return bool(default)


def demean_labels(
    y: Union[Sequence[float], np.ndarray],
) -> Tuple[np.ndarray, float]:
    """返回 ``(y - mean, mean)``；空样本 mean=0。"""
    arr = np.asarray(list(y), dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return arr, 0.0
    mu = float(arr.mean())
    if not np.isfinite(mu):
        mu = 0.0
    return arr - mu, mu


def annotate_label_demean(
    dest: dict,
    *,
    enabled: bool,
    y_mean: float = 0.0,
) -> dict:
    on = bool(enabled)
    dest["label_demean"] = on
    dest["y_demeaned"] = on
    dest["y_label_mean"] = round(float(y_mean or 0.0), 6) if on else 0.0
    return dest


def restore_intercept_after_demean(
    fit: Mapping[str, Any],
    y_mean: float,
) -> Dict[str, Any]:
    """去均值空间拟合后：截距加回 μ，并标注。"""
    out = dict(fit or {})
    try:
        base = float(out.get("intercept") or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    mu = float(y_mean or 0.0)
    out["intercept_demeaned"] = round(base, 6)
    out["intercept"] = round(base + mu, 6)
    annotate_label_demean(out, enabled=True, y_mean=mu)
    return out
