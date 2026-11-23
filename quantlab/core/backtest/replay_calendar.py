"""Replay trading calendar from daily bars.

收益窗只有一处：覆盖率日，丢掉完整日线终点之后的日期，再取末尾 N 根。
历史回测在这窗前面再垫特征根；回测中心只取起止。
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

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


def tail_trading_days(dates: Sequence[str], n: int) -> List[str]:
    """已有交易日里取末尾 ``n`` 根，含终点。``n`` 无效或日历更短则原样返回。"""
    days = sorted({str(raw)[:10] for raw in dates if len(str(raw)[:10]) >= 10})
    try:
        k = int(n)
    except (TypeError, ValueError):
        k = 0
    if k > 0 and len(days) > k:
        days = days[-k:]
    return days


def _clip_as_of(dates: Sequence[str], as_of: str) -> List[str]:
    end = str(as_of or "")[:10]
    days = [str(raw)[:10] for raw in dates if len(str(raw)[:10]) >= 10]
    if len(end) < 10:
        return days
    return [day for day in days if day <= end]


def _clipped_coverage(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    as_of: str = "",
    min_coverage: float = 0.8,
) -> List[str]:
    """覆盖率日，再丢掉 ``as_of`` 之后的日期。"""
    bars = {
        str(code): [row for row in (rows or []) if isinstance(row, dict)]
        for code, rows in (stock_bars or {}).items()
        if str(code).strip()
    }
    return _clip_as_of(_coverage_dates(bars, min_coverage=min_coverage), as_of)


def return_window(
    stock_bars: Mapping[str, Sequence[dict]],
    n: int,
    *,
    as_of: str = "",
    min_coverage: float = 0.8,
) -> Tuple[str, str]:
    """收益窗起止：覆盖率日，截到 ``as_of``，再取末尾 ``n`` 根。

    ``as_of`` 为空则不截。日线短于 ``n`` 时用现有的全部。没有交易日时返回空串。
    """
    days = tail_trading_days(
        _clipped_coverage(stock_bars, as_of=as_of, min_coverage=min_coverage),
        n,
    )
    if not days:
        return "", ""
    return days[0], days[-1]


def _replay_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    lookback: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    min_coverage: float = 0.8,
    as_of: str = "",
) -> Tuple[List[str], int]:
    """回测交易日：收益窗是末尾 lookback 根；前面垫特征窗，不占样本长度。

    ``as_of`` 给定时先丢掉这一天之后的日期，再取末尾，避免未入库的会话日占掉一根。
    """
    cal = _clipped_coverage(stock_bars, as_of=as_of, min_coverage=min_coverage)
    if not cal:
        return [], 0
    try:
        lb = int(lookback) if lookback is not None else 0
    except (TypeError, ValueError):
        lb = 0
    core = tail_trading_days(cal, lb)
    if not core:
        return [], 0
    pad_n = max(int(min_history or 1), int(max_window or 1), 1)
    pre = [d for d in cal if d < core[0]]
    dates = pre[-pad_n:] + list(core)
    start_i = dates.index(core[0]) if core[0] in dates else 0
    if start_i < 1 and len(dates) > 1:
        start_i = 1
    return dates, start_i
