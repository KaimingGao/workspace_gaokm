"""Replay trading calendar from daily bars."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    """全交集日历。回测主路径用 ``_coverage_dates``；此处留给对照测试。"""
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _coverage_dates(
    stock_bars: Dict[str, List[dict]],
    *,
    min_coverage: float = 0.8,
) -> List[str]:
    """多数票有 K 的日期。全交集会被停牌/次新一张票卡死，lookback 拉长也不生效。"""
    n = max(1, len(stock_bars or {}))
    counts: Dict[str, int] = {}
    for bars in (stock_bars or {}).values():
        seen = set()
        for b in bars or []:
            if not isinstance(b, dict):
                continue
            d = str(b.get("date") or "").strip()[:10]
            if d and d not in seen:
                seen.add(d)
                counts[d] = counts.get(d, 0) + 1
    if n <= 3:
        thresh = n
    else:
        cov = min(1.0, max(0.5, float(min_coverage)))
        thresh = max(2, int(n * cov + 0.999999))
    dates = sorted(d for d, c in counts.items() if c >= thresh)
    return dates or sorted(counts)


def _replay_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    lookback: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    min_coverage: float = 0.8,
) -> Tuple[List[str], int]:
    """回测交易日：覆盖率日历的最后 lookback 根；前面垫特征窗，不占样本长度。"""
    cal = _coverage_dates(stock_bars, min_coverage=min_coverage)
    if not cal:
        return [], 0
    try:
        lb = int(lookback) if lookback is not None else 0
    except (TypeError, ValueError):
        lb = 0
    core = cal[-lb:] if lb > 0 else cal
    pad_n = max(int(min_history or 1), int(max_window or 1), 1)
    pre = [d for d in cal if d < core[0]]
    dates = pre[-pad_n:] + list(core)
    start_i = dates.index(core[0]) if core[0] in dates else 0
    if start_i < 1 and len(dates) > 1:
        start_i = 1
    return dates, start_i
