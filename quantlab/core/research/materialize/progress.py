"""物化 Job 的进度折算。

宏步骤（L1 / L2，total 等于 planned，且小于 ``STEP_SCALE``）决定 current。
族内、票级进度一律折成 ``n * STEP_SCALE``，避免和 planned（1～3）撞车，
也避免把 800 写成 Job total。坏数字只丢掉这一拍，不中断物化。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_COUNT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

# 子进度 total 至少这么大。宏步骤 planned 只有 1～3，两者不会撞。
STEP_SCALE = 1000


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_pct(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, min(100.0, number))


def _finest_count(message: str) -> Optional[Tuple[int, int]]:
    """消息里分母最大的 ``done/total``。族序号 ``3/4`` 让位给票数 ``440/800``。"""
    best: Optional[Tuple[int, int]] = None
    for match in _COUNT_RE.finditer(str(message or "")):
        tot = _as_int(match.group(2), 0)
        if tot <= 0:
            continue
        done = _as_int(match.group(1), 0)
        if best is None or tot > best[1]:
            best = (done, tot)
    return best


def _sub_frac(c_in: int, t_in: int, message: str) -> float:
    """子进度在当前宏步骤里的比例。

    total 已经是 ``n * STEP_SCALE`` 时直接用数字。total 只有几（族/钟序号）
    而消息里有更大的 ``done/total`` 时，把票数填进这一格，避免整族钉死在格尾。
    """
    base = min(1.0, max(0.0, float(max(0, c_in)) / float(t_in)))
    if t_in >= STEP_SCALE:
        return base
    counted = _finest_count(message)
    if counted is None or counted[1] <= t_in:
        return base
    inner = min(1.0, max(0.0, float(counted[0]) / float(counted[1])))
    width = 1.0 / float(t_in)
    if c_in >= 1:
        slot_start = max(0.0, base - width)
        return slot_start + inner * (base - slot_start)
    return min(1.0, inner * width)


def fold_materialize_progress(
    *,
    planned: int,
    prev_current: int,
    prev_pct: float,
    cur: int,
    tot: int,
    message: str = "",
) -> tuple:
    """返回 ``(current, total, pct)``。current 与 pct 都不回退。"""
    steps = max(1, _as_int(planned, 1))
    prev_c = max(0, min(steps, _as_int(prev_current, 0)))
    prev_p = _as_pct(prev_pct)
    t_in = _as_int(tot, 0)
    c_in = _as_int(cur, 0)
    if t_in == steps and t_in < STEP_SCALE:
        current = max(prev_c, max(0, min(steps, c_in)))
        pct = round(100.0 * current / steps, 1)
    elif t_in > 0:
        current = prev_c
        frac = _sub_frac(c_in, t_in, message)
        pct = round(100.0 * (prev_c + frac) / steps, 1)
    else:
        current = prev_c
        pct = prev_p
    pct = max(prev_p, min(100.0, _as_pct(pct)))
    return current, steps, pct


def emit_scaled(
    cb: Optional[Any],
    index: int,
    n_steps: int,
    done: Any = 0,
    total: Any = 1,
    message: str = "",
) -> None:
    """把第 ``index`` 步内部的 done/total 折成整段位置。回调失败只记日志。"""
    if cb is None:
        return
    try:
        n = max(1, _as_int(n_steps, 1))
        span = n * STEP_SCALE
        idx = max(0, min(n - 1, _as_int(index, 0)))
        tot = max(1, _as_int(total, 1))
        cur = max(0, _as_int(done, 0))
        frac = min(1.0, float(cur) / float(tot))
        if frac >= 1.0:
            frac = 0.999
        pos = min(span - 1, max(0, int((idx + frac) * STEP_SCALE)))
        cb(pos, span, str(message or ""))
    except Exception:  # noqa: BLE001 — 进度不能打断物化
        logger.debug("materialize scaled progress dropped", exc_info=True)
