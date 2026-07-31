"""相对强弱因子（P6.2）：个股 vs 基准超额收益。"""

from __future__ import annotations

from typing import List, Optional, Tuple


def excess_return_pct(
    stock_bars: List[dict],
    index_bars: List[dict],
    *,
    window_days: int = 20,
    min_compare_days: int = 5,
) -> Optional[float]:
    if not stock_bars or not index_bars:
        return None
    n = min(window_days, len(stock_bars) - 1, len(index_bars) - 1)
    if n < min_compare_days:
        return None
    s0, s1 = stock_bars[-(n + 1)]["close"], stock_bars[-1]["close"]
    i0, i1 = index_bars[-(n + 1)]["close"], index_bars[-1]["close"]
    if not s0 or not i0:
        return None
    stock_ret = (s1 / s0 - 1.0) * 100.0
    index_ret = (i1 / i0 - 1.0) * 100.0
    return round(stock_ret - index_ret, 2)


def score_from_excess(excess: Optional[float]) -> float:
    if excess is None:
        return 50.0
    if excess >= 3:
        return 85.0
    if excess >= 1:
        return 72.0
    if excess >= -1:
        return 58.0
    if excess >= -3:
        return 42.0
    return 28.0


def score_from_last_change(last_change: Optional[float]) -> float:
    if last_change is None:
        return 50.0
    if last_change >= 2:
        return 80.0
    if last_change >= 0:
        return 65.0
    if last_change >= -2:
        return 45.0
    return 30.0


def score_relative_strength(
    *,
    stock_bars: List[dict],
    index_bars: Optional[List[dict]] = None,
    last_change: Optional[float] = None,
    window_days: int = 20,
    min_compare_days: int = 5,
    fallback_to_last_change: bool = True,
) -> Tuple[float, dict]:
    excess = None
    rs_source = "last_change_fallback"
    if index_bars:
        excess = excess_return_pct(
            stock_bars,
            index_bars,
            window_days=window_days,
            min_compare_days=min_compare_days,
        )
        if excess is not None:
            rs_source = "index_excess"
            return score_from_excess(excess), {
                "excess_return_pct": excess,
                "rs_source": rs_source,
            }

    if fallback_to_last_change:
        return score_from_last_change(last_change), {
            "excess_return_pct": None,
            "rs_source": rs_source,
            "last_change": None if last_change is None else round(last_change, 2),
        }

    return 50.0, {"excess_return_pct": None, "rs_source": "none"}
