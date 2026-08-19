"""因子系数（OLS/收益分）与展示用派生权。

真源是 ``ReturnScoreModel``（intercept + β + z）；
归一化 |β| 仅供旧路径/诊断展示，不是选股权。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def coefficients_from_return_model(
    return_model: Optional[Dict[str, Any]],
) -> Dict[str, float]:
    if not isinstance(return_model, dict):
        return {}
    raw = return_model.get("coefficients") or {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        if str(k) in ("intercept", "_intercept", "const"):
            continue
        if v is None:
            continue
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def display_weights_from_coefficients(
    coefficients: Optional[Dict[str, float]],
    *,
    min_abs: float = 1e-12,
) -> Optional[Dict[str, float]]:
    """由 |β| 归一化得到展示权（和为 1）；无有效系数则 None。"""
    if not coefficients:
        return None
    abs_map: Dict[str, float] = {}
    for k, v in coefficients.items():
        try:
            a = abs(float(v))
        except (TypeError, ValueError):
            continue
        if a < min_abs:
            continue
        abs_map[str(k)] = a
    total = sum(abs_map.values())
    if total <= 0:
        return None
    return {k: round(v / total, 6) for k, v in abs_map.items()}


def display_weights_from_return_model(
    return_model: Optional[Dict[str, Any]],
) -> Optional[Dict[str, float]]:
    return display_weights_from_coefficients(
        coefficients_from_return_model(return_model)
    )


def has_factor_coefficients(return_model: Optional[Dict[str, Any]]) -> bool:
    return bool(coefficients_from_return_model(return_model))
