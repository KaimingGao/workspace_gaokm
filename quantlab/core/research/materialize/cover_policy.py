"""物化写入 / 消费共用覆盖门槛（单一真相）。

改「怎么挂 5m / 怎么估覆盖 / 怎么判齐」时，先改本文件契约，再改调用方与测试。

覆盖契约
========
消费方           L1                              L2 钟                         日期窗
--------------  -------------------------------  ----------------------------  --------------------
做 T 回测        oo/tc/co + 决策钟 tc_HHMM         open + ≤leg1 的 suggested τ   回看 ∩ 5m 够格日
分钟 τ 物化      tc_HHMM 网格                      **必须含 open** + suggested τ  5m 够格日（近端优先）
日线物化         oo/tc/co                          open                          交易日回看（近端优先）

门槛
----
- ``LAKE_COVER_FRAC``：L1 票/日、L2 hit+built、消费 assess 同口径（默认 0.5）
- ``HORIZON_COVER_FRAC``：窗头列名覆盖；与湖日同口径。早盘缺 ``raw_ath_*`` 不得按 1.0 全拒
- 探针可外推「有分钟的票数」，**禁止**按池比例放大交易日跨度
- 写入 ``ok``：禁止 hit+built=0 仍成功；失败文案须带 hit/built/slim_reject/need
"""

from __future__ import annotations

from typing import List, Sequence

LAKE_COVER_FRAC = 0.5
HORIZON_COVER_FRAC = LAKE_COVER_FRAC


def cover_need(
    n: int,
    *,
    frac: float = LAKE_COVER_FRAC,
    minimum: int = 1,
) -> int:
    """``ceil(n * frac)``，至少 ``minimum``。"""
    try:
        total = int(n or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        f = float(frac if frac not in (None, "") else LAKE_COVER_FRAC)
    except (TypeError, ValueError):
        f = float(LAKE_COVER_FRAC)
    if not (f > 0):
        f = float(LAKE_COVER_FRAC)
    try:
        floor = int(minimum or 0)
    except (TypeError, ValueError):
        floor = 1
    floor = max(1, floor)
    return max(floor, int(max(total, 0) * f + 0.999))


def cover_reaches(
    have: int,
    universe: int,
    *,
    frac: float = LAKE_COVER_FRAC,
    minimum: int = 1,
) -> bool:
    try:
        got = int(have or 0)
    except (TypeError, ValueError):
        got = 0
    return got >= cover_need(universe, frac=frac, minimum=minimum)


def format_cube_cover_error(
    clock: str,
    *,
    hit: int = 0,
    built: int = 0,
    n_days: int,
    frac: float = LAKE_COVER_FRAC,
    slim_reject: int = 0,
    skipped: int = 0,
) -> str:
    """统一 L2 写入失败文案：``立方 09:35 覆盖不足 hit+built=0/50 need≥25 …``。"""
    try:
        h = int(hit or 0)
    except (TypeError, ValueError):
        h = 0
    try:
        b = int(built or 0)
    except (TypeError, ValueError):
        b = 0
    try:
        days = int(n_days or 0)
    except (TypeError, ValueError):
        days = 0
    need = cover_need(days, frac=frac, minimum=1)
    who = str(clock or "").strip() or "?"
    text = f"立方 {who} 覆盖不足 hit+built={h + b}/{days} need≥{need}"
    try:
        slim = int(slim_reject or 0)
    except (TypeError, ValueError):
        slim = 0
    if slim:
        text += f" slim_reject={slim}"
    try:
        skip = int(skipped or 0)
    except (TypeError, ValueError):
        skip = 0
    if skip:
        text += f" skipped={skip}"
    return text


def resolve_tau_l2_clocks(
    clocks: Sequence[str] | None,
    *,
    do_l1: bool,
    suggested: Sequence[str] | None = None,
) -> List[str]:
    """纯 τ 物化 L2 时钟：保留 open，并并入 suggested τ 网格。"""
    from core.research.materialize.catalog import normalize_clocks, suggested_tau_clocks

    clks = normalize_clocks(clocks)
    sug = [
        str(c).strip()
        for c in (suggested if suggested is not None else suggested_tau_clocks())
        if str(c or "").strip()
    ]
    if not do_l1:
        has_tau = any(
            str(c).lower() not in ("open", "09:30", "0930") for c in clks
        )
        if not has_tau:
            clks = ["open", *list(sug)]
        elif "open" not in clks and "09:30" not in clks:
            clks = ["open", *clks]
    for c in sug:
        if c not in clks:
            clks.append(c)
    return list(clks)


__all__ = [
    "LAKE_COVER_FRAC",
    "HORIZON_COVER_FRAC",
    "cover_need",
    "cover_reaches",
    "format_cube_cover_error",
    "resolve_tau_l2_clocks",
]
