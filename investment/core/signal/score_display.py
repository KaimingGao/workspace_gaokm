"""展示用评分门槛：选股门槛只过滤目标簿，不隐藏分数本身。

仅收益分：用 ``scoring.min_predicted_score``。
缺省键 → 默认 +1（ŷ&lt;1% 不入簿）；显式 ``null`` → 不设下限（研究用）。
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


def json_safe(obj: Any) -> Any:
    """递归把 ±inf/NaN 换成 None，供 Starlette JSONResponse（allow_nan=False）。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def is_predicted_rank_mode(config: Optional[dict] = None) -> bool:
    return True


# 长多默认：ŷ≥+1% 才入簿/建议买入
DEFAULT_MIN_PREDICTED_SCORE = 1.0


def selection_min_score(paper: Optional[dict] = None) -> Optional[float]:
    """选股/建议买入门槛（ŷ%）。

    - 配置无 ``min_predicted_score`` 键 → ``1.0``
    - 配置显式 ``null`` → ``None``（不设下限）
    - 配置数值 → 该值
    """
    del paper
    try:
        from core.signal.config import load_signal_config

        scoring = load_signal_config().get("scoring") or {}
        if "min_predicted_score" not in scoring:
            return float(DEFAULT_MIN_PREDICTED_SCORE)
        mp = scoring.get("min_predicted_score")
        if mp is None or mp == "":
            return None
        return float(mp)
    except Exception:
        return float(DEFAULT_MIN_PREDICTED_SCORE)


def resolve_buy_floor(
    paper: Optional[dict] = None,
    *,
    explicit: Optional[float] = None,
    heuristic_default: float = 55.0,
) -> float:
    """买入/入簿分数下限。默认 +1（ŷ≥1%）；配置显式 null → -inf。"""
    del heuristic_default
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    floor = selection_min_score(paper)
    return float(floor) if floor is not None else float("-inf")


# 持仓卖出门槛：分池调仓仅当 ŷ < 该值才卖（默认 -1%）；与买入门槛形成滞回
DEFAULT_MIN_HOLD_PREDICTED_SCORE = -1.0


def resolve_hold_floor(
    paper: Optional[dict] = None,
    *,
    heuristic_default: float = 45.0,
) -> float:
    """持仓卖出门槛。默认 -1（ŷ<-1% 才卖）；显式 null → -inf（不按分卖）。"""
    del paper, heuristic_default
    try:
        from core.signal.config import load_signal_config

        scoring = load_signal_config().get("scoring") or {}
        if "min_hold_predicted_score" not in scoring:
            return float(DEFAULT_MIN_HOLD_PREDICTED_SCORE)
        mp = scoring.get("min_hold_predicted_score")
        if mp is None or mp == "":
            return float("-inf")
        return float(mp)
    except Exception:
        return float(DEFAULT_MIN_HOLD_PREDICTED_SCORE)


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
