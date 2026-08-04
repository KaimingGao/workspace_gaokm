"""展示用评分门槛：选股门槛只过滤目标簿，不隐藏分数本身。

仅收益分：用 ``scoring.min_predicted_score``（可空=不设下限）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


def json_safe_number(value: Any) -> Any:
    """把 ±inf/NaN 转成 JSON 可序列化值（None）；有限数原样返回。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    return value


def is_predicted_rank_mode(config: Optional[dict] = None) -> bool:
    return True


def selection_min_score(paper: Optional[dict] = None) -> Optional[float]:
    """选股门槛；未设 min_predicted_score 时返回 None（不设下限）。"""
    del paper
    try:
        from core.signal.config import load_signal_config

        scoring = load_signal_config().get("scoring") or {}
        mp = scoring.get("min_predicted_score")
        if mp is None or mp == "":
            return None
        return float(mp)
    except Exception:
        return None


def resolve_buy_floor(
    paper: Optional[dict] = None,
    *,
    explicit: Optional[float] = None,
    heuristic_default: float = 55.0,
) -> float:
    """买入/入簿分数下限。未设 min_predicted_score → -inf（不截）。"""
    del heuristic_default
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    floor = selection_min_score(paper)
    return float(floor) if floor is not None else float("-inf")


def resolve_hold_floor(
    paper: Optional[dict] = None,
    *,
    heuristic_default: float = 45.0,
) -> float:
    """持仓保有下限。默认 -inf（只靠 TopK/簿成员卖出）。"""
    del paper, heuristic_default
    try:
        from core.signal.config import load_signal_config

        scoring = load_signal_config().get("scoring") or {}
        mp = scoring.get("min_hold_predicted_score")
        if mp is not None and mp != "":
            return float(mp)
    except Exception:
        pass
    return float("-inf")


def score_gates_use_heuristic_bands() -> bool:
    """0–100 加减仓分档已退役。"""
    return False


def annotate_score_gate(
    score: Any,
    *,
    paper: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """返回 min_score / below_min_score，供观察表与持仓表展示。"""
    if min_score is not None:
        floor: Optional[float] = json_safe_number(float(min_score))
    else:
        floor = selection_min_score(paper)
    sc = None
    try:
        if score is not None and score != "":
            sc = float(score)
    except (TypeError, ValueError):
        sc = None
    return {
        "min_score": floor,
        "below_min_score": bool(
            floor is not None and sc is not None and sc < floor
        ),
    }
