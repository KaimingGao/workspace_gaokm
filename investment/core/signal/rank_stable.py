"""稳定排序键：主分相同按代码升序，避免回测/纸面因遍历序选不同票。"""

from __future__ import annotations

from typing import Any, Optional, Tuple


def stable_rank_key(
    score: Any,
    code: Any = "",
    *,
    reverse_score: bool = True,
) -> Tuple[float, str]:
    """返回可直接用于 ``sorted(..., key=...)`` 的元组。

    ``reverse_score=True`` 时对分数取负，配合 ``sorted`` 升序得到「高分在前」。
    """
    try:
        sc = float(score) if score is not None else float("-inf")
    except (TypeError, ValueError):
        sc = float("-inf")
    c = str(code or "").strip()
    if reverse_score:
        return (-sc, c)
    return (sc, c)


def stable_asc_score_key(score: Any, code: Any = "") -> Tuple[float, str]:
    """低分在前（减仓/卸仓用）。"""
    return stable_rank_key(score, code, reverse_score=False)
