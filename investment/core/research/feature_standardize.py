"""特征训练集全局 z-score 开关：``feature_zscore``。

对因子 X 用训练窗列向 μ/σ（总体方差）做 (x−μ)/σ；不动 label。
Ridge / Tree 共用此名；落盘只写 ``feature_zscore``。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

DEFAULT_FEATURE_ZSCORE = True


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
