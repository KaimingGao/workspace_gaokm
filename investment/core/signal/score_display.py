"""展示用评分门槛：选股 min_score 只过滤目标簿，不隐藏分数本身。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def selection_min_score(paper: Optional[dict] = None) -> float:
    """账户策略门槛优先，否则全局 rank 默认。"""
    rules = (paper or {}).get("rules") if isinstance(paper, dict) else None
    if isinstance(rules, dict) and rules.get("min_score") is not None:
        try:
            return float(rules.get("min_score"))
        except (TypeError, ValueError):
            pass
    try:
        from core.signal.config import get_rank_defaults

        return float(get_rank_defaults().get("min_score") or 55.0)
    except Exception:
        return 55.0


def annotate_score_gate(
    score: Any,
    *,
    paper: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """返回 min_score / below_min_score，供观察表与持仓表展示。"""
    floor = float(min_score) if min_score is not None else selection_min_score(paper)
    sc = None
    try:
        if score is not None and score != "":
            sc = float(score)
    except (TypeError, ValueError):
        sc = None
    return {
        "min_score": floor,
        "below_min_score": bool(sc is not None and sc < floor),
    }
