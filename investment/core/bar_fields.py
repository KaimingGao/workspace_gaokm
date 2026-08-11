"""日线字段辅助：成交额与成交量分离（模拟验证诚实度）。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def bar_amount(bar: Optional[dict]) -> float:
    """优先用独立 ``amount``；缺失时用 volume×close 近似。"""
    if not isinstance(bar, dict):
        return 0.0
    amt = bar.get("amount")
    try:
        if amt is not None:
            return max(0.0, float(amt))
    except (TypeError, ValueError):
        pass
    try:
        vol = float(bar.get("volume") or bar.get("vol") or 0.0)
        close = float(bar.get("close") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, vol * close)


def bar_amount_raw(bar: Optional[dict]) -> Optional[float]:
    """仅返回显式成交额；无则 None（不近似）。"""
    if not isinstance(bar, dict):
        return None
    amt = bar.get("amount")
    try:
        if amt is None:
            return None
        return max(0.0, float(amt))
    except (TypeError, ValueError):
        return None


def extract_amount_from_row(row: Dict[str, Any]) -> Optional[float]:
    """从原始行情行解析成交额（不读成交量）。"""
    from core.numbers import to_float

    if not isinstance(row, dict):
        return None
    return to_float(
        row.get("amount")
        or row.get("成交额")
        or row.get("turnover")
        or row.get("成交金额")
    )


def extract_volume_from_row(row: Dict[str, Any]) -> Optional[float]:
    """从原始行情行解析成交量（不读成交额）。"""
    from core.numbers import to_float

    if not isinstance(row, dict):
        return None
    return to_float(
        row.get("volume") or row.get("成交量") or row.get("vol") or row.get("手数")
    )
