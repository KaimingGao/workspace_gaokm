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
    """按交易日对齐后的近 N 日超额（个股收益 − 指数收益）。

    禁止「各取最后 N 根」错位：指数缓存比个股旧时会把窗口算歪。
    """
    if not stock_bars or not index_bars:
        return None

    stock_by_date = {}
    for b in stock_bars or []:
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) >= 10 and (b or {}).get("close") is not None:
            stock_by_date[d] = b
    index_by_date = {}
    for b in index_bars or []:
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) >= 10 and (b or {}).get("close") is not None:
            index_by_date[d] = b

    common = sorted(set(stock_by_date) & set(index_by_date))
    if len(common) < min_compare_days + 1:
        return None

    end = common[-1]
    # 需要 window_days 段收益 → window_days+1 个收盘点
    need = int(window_days) + 1
    if len(common) < need:
        if len(common) < min_compare_days + 1:
            return None
        start = common[0]
    else:
        start = common[-need]

    try:
        s0 = float(stock_by_date[start]["close"])
        s1 = float(stock_by_date[end]["close"])
        i0 = float(index_by_date[start]["close"])
        i1 = float(index_by_date[end]["close"])
    except (TypeError, ValueError, KeyError):
        return None
    if s0 <= 0 or i0 <= 0:
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
                "omit_sub_score": False,
            }

    if fallback_to_last_change and last_change is not None:
        return score_from_last_change(last_change), {
            "excess_return_pct": None,
            "rs_source": rs_source,
            "last_change": round(last_change, 2),
            "omit_sub_score": False,
        }

    # 无指数对齐、无涨跌可回退 → 不进 ŷ
    return 50.0, {
        "excess_return_pct": None,
        "rs_source": "none",
        "omit_sub_score": True,
    }
