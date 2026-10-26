"""策略调仓现金底仓：保留净值比例供正T低吸。"""

from __future__ import annotations

from typing import Any, Optional

# 默认保留总净值 20% 现金，供正T第一腿低吸
DEFAULT_MIN_CASH_PCT = 0.20


def resolve_min_cash_pct(rules: Optional[dict] = None) -> float:
    """调仓买腿须保留的现金占净值比例。

    规则键 ``min_cash_pct``（优先）或兼容 ``t0_cash_reserve_pct``；
    缺省 ``0.20``；``0`` 关闭底仓。夹到 ``[0, 0.95]``。
    """
    raw: Any = None
    if isinstance(rules, dict):
        raw = rules.get("min_cash_pct")
        if raw is None or raw == "":
            raw = rules.get("t0_cash_reserve_pct")
    if raw is None or raw == "":
        return DEFAULT_MIN_CASH_PCT
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_CASH_PCT
    if pct < 0:
        return 0.0
    if pct > 0.95:
        return 0.95
    return pct


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def cash_floor(equity: float, min_cash_pct: float) -> float:
    """不可动用现金地板（元）。净值或比例不是有限数时地板为 0。"""
    return max(0.0, _num(equity) * max(0.0, _num(min_cash_pct)))


def spendable_cash(cash: float, equity: float, min_cash_pct: float) -> float:
    """调仓买腿可用现金 = 现金 − 净值×底仓比例（不低于 0）。"""
    return max(0.0, _num(cash) - cash_floor(equity, min_cash_pct))
