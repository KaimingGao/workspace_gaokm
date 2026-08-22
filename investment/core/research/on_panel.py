"""隔夜 open 链面板：y_ON(T+1) = open[T+1]/open[T]-1；决策在 T 收盘前 / EOD。

特征 = T 日已实现路径（开→收、收→收、今开/昨开）+ 开盘 Z；标签为下一交易日 open/open。
与 gap_pct（收→开）和 ŷ_τ（开→收）正交；不进主排序。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.rem_panel import (
    _gap_pct,
    _open_to_close_pct,
    attach_cross_section_breadth,
    gap_atr_from_hist,
    gap_vs_sector_value,
    hist_bars_pit,
    theme_sample_weights,
)

try:
    from core.event_prior import _parse_open_price
except Exception:  # noqa: BLE001

    def _parse_open_price(quote):  # type: ignore[misc]
        return None


def _parse_close_price(quote: Optional[dict], cur: dict) -> Optional[float]:
    if isinstance(quote, dict):
        for key in ("price_raw", "close", "price"):
            v = quote.get(key)
            if v is None:
                continue
            try:
                if isinstance(v, (int, float)):
                    px = float(v)
                    return px if px > 0 else None
                s = str(v).replace("元", "").replace(",", "").strip()
                px = float(s)
                return px if px > 0 else None
            except (TypeError, ValueError):
                continue
    try:
        px = float(cur.get("close") or 0.0)
        return px if px > 0 else None
    except (TypeError, ValueError):
        return None

logger = logging.getLogger(__name__)

_ON_PCT_KEYS = ("ret_oc", "ret_cc", "y_on_today", "gap_pct")


def _open_to_open_fwd_pct(open_t: float, open_next: float) -> Optional[float]:
    try:
        o0 = float(open_t)
        o1 = float(open_next)
    except (TypeError, ValueError):
        return None
    if o0 <= 0 or o1 <= 0:
        return None
    return (o1 / o0 - 1.0) * 100.0


def _open_to_open_pct(open_t: float, open_prev: float) -> Optional[float]:
    try:
        o0 = float(open_t)
        o1 = float(open_prev)
    except (TypeError, ValueError):
        return None
    if o0 <= 0 or o1 <= 0:
        return None
    return (o0 / o1 - 1.0) * 100.0


def intraday_on_feature_row(
    *,
    open_t: float,
    close_t: float,
    prev_close: float,
    prev_open: float,
    gap_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    ret_open_to_tau: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """T 日决策特征（预测 open[T+1]/open[T]-1）。"""
    g = gap_pct
    if g is None and prev_close > 0 and open_t > 0:
        g = _gap_pct(prev_close, open_t)
    ret_oc = _open_to_close_pct(open_t, close_t)
    ret_cc = None
    if prev_close > 0 and close_t > 0:
        ret_cc = (float(close_t) / float(prev_close) - 1.0) * 100.0
    y_on_today = _open_to_open_pct(open_t, prev_open)
    row: Dict[str, Optional[float]] = {
        "ret_oc": float(ret_oc) if ret_oc is not None else None,
        "gap_pct": float(g) if g is not None else None,
        "ret_cc": float(ret_cc) if ret_cc is not None else None,
        "y_on_today": float(y_on_today) if y_on_today is not None else None,
        "gap_atr": gap_atr_from_hist(g, hist_bars),
        "ret_open_to_tau": (
            float(ret_open_to_tau) if ret_open_to_tau is not None else None
        ),
    }
    return row


def collect_on_panel(
    bars: List[dict],
    *,
    min_history: int = 12,
    max_window: int = 30,
    stock_code: Optional[str] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """单票 open→next-open 面板。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = T；``ys`` = open[T+1]/open[T]-1（百分点）。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []

    n = len(bars or [])
    for i in range(min_history, n - 1):
        b_t = bars[i]
        b_next = bars[i + 1]
        b_prev = bars[i - 1]
        try:
            o_t = float(b_t.get("open"))
            c_t = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
            o_prev = float(b_prev.get("open"))
            o_next = float(b_next.get("open"))
        except (TypeError, ValueError):
            continue
        if min(o_t, c_t, pc, o_prev, o_next) <= 0:
            continue
        y = _open_to_open_fwd_pct(o_t, o_next)
        if y is None:
            continue
        gap = _gap_pct(pc, o_t)
        window = bars[max(0, i - max_window) : i]
        if len(window) < min_history:
            continue
        row = intraday_on_feature_row(
            open_t=o_t,
            close_t=c_t,
            prev_close=pc,
            prev_open=o_prev,
            gap_pct=gap,
            hist_bars=window,
        )
        date_t = str(b_t.get("date") or "")[:10]
        xs.append(row)
        ys.append(float(y))
        dates.append(date_t)
        metas.append(
            {
                "stock_code": stock_code,
                "date": date_t,
                "label_date": str(b_next.get("date") or "")[:10],
                "gap_pct": float(gap) if gap is not None else None,
                "open": o_t,
                "close": c_t,
                "open_next": o_next,
                "prev_open": o_prev,
                "prev_close": pc,
                "y_on_fwd": float(y),
            }
        )
    return xs, ys, dates, metas


def build_on_features_from_quote_bars(
    quote: Optional[dict],
    bars: Optional[Sequence[dict]],
    *,
    gap_pct: Optional[float] = None,
    ret_open_to_tau: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """Live / PIT：用最新 bar + quote 构造 ON 头特征。"""
    b = list(bars or [])
    if len(b) < 2:
        return {}
    asof = ""
    if quote:
        asof = str(quote.get("date") or quote.get("trade_date") or "")[:10]
    hist = hist_bars_pit(b, asof_date=asof)
    cur = b[-1]
    prev = b[-2] if len(b) >= 2 else {}
    o_t = _parse_open_price(quote)
    if o_t is None or o_t <= 0:
        try:
            o_t = float(cur.get("open") or 0.0)
        except (TypeError, ValueError):
            o_t = 0.0
    c_t = _parse_close_price(quote, cur)
    if c_t is None:
        c_t = 0.0
    try:
        pc = float(prev.get("close") or 0.0)
        o_prev = float(prev.get("open") or 0.0)
    except (TypeError, ValueError):
        return {}
    if min(o_t, c_t, pc, o_prev) <= 0:
        return {}
    g = gap_pct
    if g is None:
        g = _gap_pct(pc, o_t)
    return intraday_on_feature_row(
        open_t=o_t,
        close_t=c_t,
        prev_close=pc,
        prev_open=o_prev,
        gap_pct=g,
        hist_bars=hist,
        ret_open_to_tau=ret_open_to_tau,
    )


def enrich_on_panel_breadth(
    panels: Sequence[Dict[str, Any]],
    *,
    gap_trigger_pct: float = 2.0,
    sector_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """复用 rem 截面广度写 sector_gap_breadth / theme_day / gap_vs_sector。"""
    return attach_cross_section_breadth(
        panels, gap_trigger_pct=gap_trigger_pct, sector_map=sector_map
    )


__all__ = [
    "build_on_features_from_quote_bars",
    "collect_on_panel",
    "enrich_on_panel_breadth",
    "intraday_on_feature_row",
    "theme_sample_weights",
]
