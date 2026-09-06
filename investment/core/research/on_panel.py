"""隔夜缺口面板：y_ON = open[T+1]/close[T]-1；决策在 T 开盘。

特征 = T-1 已实现路径（昨开→昨收、昨收→前收、今开/昨开）+ 今开 gap Z；
标签为 T 收盘 → T+1 开盘的真实隔夜缺口，与 ret_oc / ret_cc 正交。
不进主排序，仅作隔夜风控旁路。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.tau_panel import (
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


def _open_to_open_pct(open_t: float, open_prev: float) -> Optional[float]:
    try:
        o0 = float(open_t)
        o1 = float(open_prev)
    except (TypeError, ValueError):
        return None
    if o0 <= 0 or o1 <= 0:
        return None
    return (o0 / o1 - 1.0) * 100.0


def _overnight_gap_pct(close_t: float, open_next: float) -> Optional[float]:
    """真实隔夜缺口 = open[T+1]/close[T]-1（百分点）。"""
    try:
        c0 = float(close_t)
        o1 = float(open_next)
    except (TypeError, ValueError):
        return None
    if c0 <= 0 or o1 <= 0:
        return None
    return (o1 / c0 - 1.0) * 100.0


def intraday_on_feature_row(
    *,
    open_t: float,
    close_t: float,
    prev_close: float,
    prev_open: float,
    prev_prev_close: float = 0.0,
    gap_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    ret_open_to_tau: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """T 日开盘决策特征（预测 open[T+1]/close[T]-1 隔夜缺口）。

    所有特征仅使用 T 开盘时已知信息：
    - ret_oc  = close[T-1]/open[T-1]-1（昨日 intraday，非当日）
    - ret_cc  = close[T-1]/close[T-2]-1（昨日收→收，非当日）
    - y_on_today = open[T]/open[T-1]-1（今开/昨开）
    - gap_pct = open[T]/close[T-1]-1（今开 gap）
    """
    g = gap_pct
    if g is None and prev_close > 0 and open_t > 0:
        g = _gap_pct(prev_close, open_t)
    # 使用 T-1 已实现数据（开盘时 close[T] 不可用，避免训练-推理口径错位）
    ret_oc = _open_to_close_pct(prev_open, prev_close) if prev_open > 0 else None
    ret_cc = None
    if prev_prev_close > 0 and prev_close > 0:
        ret_cc = (float(prev_close) / float(prev_prev_close) - 1.0) * 100.0
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
    """单票隔夜缺口面板。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = T；``ys`` = open[T+1]/close[T]-1（真实隔夜缺口，百分点）。
    特征仅用 T 开盘时已知信息（T-1 已实现 + 今开 gap）。
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
        b_prev2 = bars[i - 2] if i >= 2 else {}
        try:
            o_t = float(b_t.get("open"))
            c_t = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
            o_prev = float(b_prev.get("open"))
            ppc = float(b_prev2.get("close") or 0.0)
            o_next = float(b_next.get("open"))
        except (TypeError, ValueError):
            continue
        if min(o_t, c_t, pc, o_prev, o_next) <= 0:
            continue
        # 标签 = 真实隔夜缺口 open[T+1]/close[T]-1（与 ret_oc 正交）
        y = _overnight_gap_pct(c_t, o_next)
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
            prev_prev_close=ppc,
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
                "overnight_gap": float(y),
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
    """Live / PIT：用最新 bar + quote 构造 ON 头特征。

    特征口径与训练一致：ret_oc/ret_cc 取 T-1 已实现值（开盘时 close[T] 不可用）。
    """
    b = list(bars or [])
    if len(b) < 3:
        return {}
    asof = ""
    if quote:
        asof = str(quote.get("date") or quote.get("trade_date") or "")[:10]
    hist = hist_bars_pit(b, asof_date=asof)
    cur = b[-1]
    prev = b[-2] if len(b) >= 2 else {}
    prev2 = b[-3] if len(b) >= 3 else {}
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
        ppc = float(prev2.get("close") or 0.0)
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
        prev_prev_close=ppc,
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
