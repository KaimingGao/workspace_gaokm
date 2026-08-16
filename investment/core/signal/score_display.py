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
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    # Python float / numpy floating（含 float32）；int 原样
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, int):
        return obj
    # numpy scalar（非 float 子类的 floating / integer）
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except Exception:
            return None
    return obj


def is_predicted_rank_mode(config: Optional[dict] = None) -> bool:
    """已废弃：生产恒为 predicted_score。保留以免旧 import 崩；新代码勿分支。"""
    del config
    return True


def looks_like_legacy_heuristic_score(value: Optional[float]) -> bool:
    """≥10 的「门槛」在短线 ŷ% 语境下视为遗留 0–100 分档。"""
    if value is None:
        return False
    try:
        return float(value) >= 10.0
    except (TypeError, ValueError):
        return False


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
            v = float(explicit)
        except (TypeError, ValueError):
            v = None
        else:
            if looks_like_legacy_heuristic_score(v):
                # 调用方误传 0–100：改走配置 ŷ 门槛
                pass
            else:
                return v
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


def resolve_optimize_score_floor(explicit: Optional[float] = None) -> float:
    """组合 optimize / 日更目标仓的 ŷ 下限。

    - ``None`` → ``resolve_buy_floor()``
    - 显式 ŷ%（通常 |x|<10）→ 该值
    - 显式 ≥10 → 视为遗留 0–100，改走 ``resolve_buy_floor()``
    """
    if explicit is None:
        return float(resolve_buy_floor())
    try:
        v = float(explicit)
    except (TypeError, ValueError):
        return float(resolve_buy_floor())
    if looks_like_legacy_heuristic_score(v):
        return float(resolve_buy_floor())
    return v


def annotate_score_gate(
    score: Any,
    *,
    paper: Optional[dict] = None,
    min_score: Optional[float] = None,
    item: Optional[dict] = None,
) -> Dict[str, Any]:
    """返回 min_score / below_min_score，供观察表与持仓表展示。

    有 ``item`` 时门槛对比用 ``eod_gate_score_for_item``（原始 ŷ_EOD），
    与建簿 / 预演买入门槛同源；否则回退传入的 ``score``。
    """
    if min_score is not None:
        floor: Optional[float] = json_safe_number(float(min_score))
    else:
        floor = selection_min_score(paper)
    sc = None
    if isinstance(item, dict):
        try:
            from core.signal.dual_score import (
                eod_gate_score_for_item,
                is_heuristic_score_scale,
            )

            if is_heuristic_score_scale(item):
                # heuristic 0–100 不与 ŷ% 门槛比；也不把 score 回填成 gate
                return {
                    "min_score": floor,
                    "below_min_score": False,
                    "gate_score": None,
                }
            sc = eod_gate_score_for_item(item)
        except Exception:
            sc = None
    if sc is None:
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
        "gate_score": json_safe_number(sc) if sc is not None else None,
    }
