"""生产门禁与复权标签（纯函数）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Optional, Tuple

from core.data.policy import DEFAULT_ADJUST_POLICY

__all__ = [
    "DEFAULT_ADJUST_POLICY",
    "allows_production_score",
    "infer_adjust",
    "normalize_adjust_policy",
]


def allows_production_score(
    *,
    quality_level: Optional[str] = None,
    fallback: bool = False,
) -> Tuple[bool, str]:
    """P1：仅 good 且非 fallback 可进生产 score。"""
    if fallback:
        return False, "data_quality_gate:fallback"
    level = str(quality_level or "empty").strip().lower() or "empty"
    if level == "empty":
        return False, "data_quality_gate:empty"
    if level == "thin":
        return False, "data_quality_gate:thin"
    if level != "good":
        return False, f"data_quality_gate:{level}"
    return True, ""


def infer_adjust(data_source: str, *, policy: str = DEFAULT_ADJUST_POLICY) -> str:
    src = str(data_source or "")
    if "raw" in src or src.endswith("_none"):
        return "none"
    if src.startswith("cache:"):
        return "cached"
    if "fallback" in src or src in ("empty", "quote_fallback"):
        return "none"
    return policy or DEFAULT_ADJUST_POLICY


def normalize_adjust_policy(adjust: Optional[str] = None) -> str:
    p = str(adjust or DEFAULT_ADJUST_POLICY or "qfq").strip().lower()
    if p in ("none", "unadjusted", ""):
        return "raw"
    if p in ("qfq", "raw", "hfq"):
        return p
    return "qfq"
