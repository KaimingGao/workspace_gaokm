"""OOS 配对：非有限预测/标签不进 IC、方向命中与残差。

尾部未实现的 y=NaN、脏标签或 Inf 不得污染汇总指标。
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple


def as_float(value: Any) -> float:
    """转 float。缺失、非数字、Inf 一律记 NaN。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(out):
        return float("nan")
    return out


def finite_xy(pred: Optional[float], y: Any) -> bool:
    """预测与标签都有限才进入 OOS。"""
    if pred is None:
        return False
    try:
        pf = float(pred)
        yf = float(y)
    except (TypeError, ValueError):
        return False
    return math.isfinite(pf) and math.isfinite(yf)


def n_label_nan(ys: Sequence[Any]) -> int:
    return sum(1 for y in ys if not math.isfinite(as_float(y)))


def finite_pairs(
    preds: Sequence[Optional[float]], ys: Sequence[Any]
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p, y in zip(preds, ys):
        if not finite_xy(p, y):
            continue
        out.append((float(p), float(y)))
    return out


__all__ = [
    "as_float",
    "finite_pairs",
    "finite_xy",
    "n_label_nan",
]
