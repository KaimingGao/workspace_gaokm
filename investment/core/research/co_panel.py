"""隔夜缺口面板：y_co = open[T+1]/close[T]-1；决策在 T 开盘。

特征 = T-1 已实现路径 + 今开 gap Z + 昨 K 位置/振幅/量能 + 距涨停 + 隔夜滞后。
标签为 T 收盘 → T+1 开盘的真实隔夜缺口，与 ret_oc / ret_cc 正交。
不进主排序，仅作隔夜风控旁路。全部特征只用 T 开盘已知信息（不用 close[T]）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.tau_panel import (
    LABEL_LAG_WINDOW,
    _gap_pct,
    _open_to_close_pct,
    attach_cross_section_breadth,
    gap_atr_from_hist,
    gap_vs_sector_value,
    hist_bars_pit,
    label_lag_features,
    mom3_pct_from_hist,
    theme_sample_weights,
    yclose_loc_from_prev,
    yest_gap_from_hist,
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

VOL_RATIO_WINDOW = 20
VOL_RATIO_MIN = 5

# 隔夜滞后：asof 用 T-1，避开今日 gap_pct（= T-1 日隔夜 = open[T]/close[T-1]-1）
CO_LAG_FEATURES = ("yest_gap", "on_ma5")
CO_LAG_FEAT_LABELS = {
    "yest_gap": "昨隔夜缺口 %",
    "on_ma5": "近5日隔夜缺口均 %",
}

# 手造 Z；raw_alpha158_* 为动态附加列（见 inject_alpha158）
CO_Z_FEATURES = (
    "ret_oc",
    "gap_pct",
    "ret_cc",
    "y_on_today",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "ret_open_to_tau",
    "yclose_loc",
    "mom3_pct",
    "yest_close_loc",
    "yest_range_pct",
    "yest_vol_ratio",
    "dist_to_up_limit",
) + CO_LAG_FEATURES


def inject_alpha158_into_row(
    row: Dict[str, Optional[float]],
    hist_bars: Optional[Sequence[dict]],
    *,
    include_alpha158: bool = True,
) -> Dict[str, Optional[float]]:
    """把 ≤T−1 日 K 上的 ``raw_alpha158_*`` 并入特征行（默认开）。"""
    if not include_alpha158 or not isinstance(row, dict):
        return row
    try:
        from core.signal.factors.alpha158 import raw_alpha158_from_bars

        extras = raw_alpha158_from_bars(hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("co alpha158 inject failed", exc_info=True)
        return row
    if extras:
        row.update(extras)
    return row


def yest_close_loc_from_prev(prev_bar: Optional[dict]) -> Optional[float]:
    """昨收在昨高低中的位置 [0,1]；近高收盘常伴隔夜惯性。"""
    if not isinstance(prev_bar, dict):
        return None
    try:
        hi = float(prev_bar.get("high"))
        lo = float(prev_bar.get("low"))
        c = float(prev_bar.get("close"))
    except (TypeError, ValueError):
        return None
    if hi <= lo or c <= 0:
        return None
    return round(max(0.0, min(1.0, (c - lo) / (hi - lo))), 4)


def yest_range_pct_from_prev(prev_bar: Optional[dict]) -> Optional[float]:
    """昨振幅 (H−L)/C ×100。隔夜波动代理，非当日。"""
    if not isinstance(prev_bar, dict):
        return None
    try:
        hi = float(prev_bar.get("high"))
        lo = float(prev_bar.get("low"))
        c = float(prev_bar.get("close"))
    except (TypeError, ValueError):
        return None
    if c <= 0 or hi < lo:
        return None
    return round((hi - lo) / c * 100.0, 4)


def yest_vol_ratio_from_hist(
    hist_bars: Optional[Sequence[dict]],
    prev_bar: Optional[dict],
    *,
    window: int = VOL_RATIO_WINDOW,
) -> Optional[float]:
    """昨量 / 此前均量。MA 不含 T-1 自身。"""
    if not isinstance(prev_bar, dict):
        return None
    try:
        last = float(prev_bar.get("volume") or 0.0)
    except (TypeError, ValueError):
        return None
    if last <= 0:
        return None
    prev_date = str(prev_bar.get("date") or prev_bar.get("trade_date") or "")[:10]
    vols: List[float] = []
    for b in hist_bars or []:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "")[:10]
        if prev_date and d and d >= prev_date:
            continue
        try:
            v = float(b.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            vols.append(v)
    if len(vols) < VOL_RATIO_MIN:
        return None
    win = max(VOL_RATIO_MIN, int(window or VOL_RATIO_WINDOW))
    tail = vols[-win:]
    ma = sum(tail) / float(len(tail))
    if ma < 1e-9:
        return None
    return round(last / ma, 4)


def dist_to_up_limit_pct(
    ret_cc: Optional[float],
    stock_code: Optional[str] = None,
) -> Optional[float]:
    """昨收到涨停剩余空间（百分点）。主板≈10、创业/科创≈20；缺码则空。"""
    if ret_cc is None or not str(stock_code or "").strip():
        return None
    try:
        r = float(ret_cc)
    except (TypeError, ValueError):
        return None
    try:
        from core.backtest.matching import limit_up_threshold_for_code

        thr = float(limit_up_threshold_for_code(stock_code)) + 0.5
    except Exception:  # noqa: BLE001
        logger.debug("limit_up_threshold_for_code failed", exc_info=True)
        c = str(stock_code or "").strip()
        thr = 20.0 if c.startswith(("300", "301", "688", "689")) else 10.0
    return round(float(thr) - r, 4)


def realized_co_by_date(bars: Optional[Sequence[dict]]) -> Dict[str, float]:
    """各日真实隔夜缺口 open[D+1]/close[D]−1（%）；算不出的日不进表。"""
    out: Dict[str, float] = {}
    seq = list(bars or [])
    for i in range(len(seq) - 1):
        b = seq[i]
        nxt = seq[i + 1]
        if not isinstance(b, dict) or not isinstance(nxt, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "")[:10]
        if len(d) < 10:
            continue
        try:
            y = _overnight_gap_pct(float(b.get("close")), float(nxt.get("open")))
        except (TypeError, ValueError):
            continue
        if y is None:
            continue
        out[d] = round(float(y), 6)
    return out


def co_lag_features(
    *,
    hist_bars: Sequence[dict],
    co_by_date: Dict[str, float],
    asof_prev_date: str,
    window: int = LABEL_LAG_WINDOW,
) -> Dict[str, Optional[float]]:
    """PIT 隔夜滞后：asof=T-1，lag1=昨隔夜（≠今日 gap_pct），ma5 不含今日缺口。"""
    return label_lag_features(
        hist_bars=hist_bars,
        by_date=co_by_date,
        asof_date=asof_prev_date,
        window=window,
        lag1_key="yest_gap",
        ma_key="on_ma5",
    )


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


def intraday_co_feature_row(
    *,
    open_t: float,
    close_t: float,
    prev_close: float,
    prev_open: float,
    prev_prev_close: float = 0.0,
    gap_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    ret_open_to_tau: Optional[float] = None,
    prev_bar: Optional[dict] = None,
    stock_code: Optional[str] = None,
    asof_date: Optional[str] = None,
    co_by_date: Optional[Dict[str, float]] = None,
    include_alpha158: bool = True,
) -> Dict[str, Optional[float]]:
    """T 日开盘决策特征（预测 open[T+1]/close[T]-1 隔夜缺口）。

    所有特征仅使用 T 开盘时已知信息（不用 close[T]）：
    - ret_oc  = close[T-1]/open[T-1]-1（昨日 intraday）
    - ret_cc  = close[T-1]/close[T-2]-1（昨日收→收）
    - y_on_today = open[T]/open[T-1]-1（今开/昨开）
    - gap_pct = open[T]/close[T-1]-1（今开 gap）
    - yclose_loc / mom3_pct：与 τ 头同源开盘 Z
    - yest_close_loc / yest_range_pct / yest_vol_ratio：昨 K 微观结构
    - dist_to_up_limit：昨收到涨停剩余空间
    - yest_gap / on_ma5：隔夜滞后（不含今日 gap_pct）
    - raw_alpha158_*：≤T−1 日 K 量价衍生（默认开）
    """
    _ = close_t
    g = gap_pct
    if g is None and prev_close > 0 and open_t > 0:
        g = _gap_pct(prev_close, open_t)
    hist = list(hist_bars or [])
    ret_oc = _open_to_close_pct(prev_open, prev_close) if prev_open > 0 else None
    ret_cc = None
    if prev_prev_close > 0 and prev_close > 0:
        ret_cc = (float(prev_close) / float(prev_prev_close) - 1.0) * 100.0
    y_on_today = _open_to_open_pct(open_t, prev_open)
    prev_date = ""
    if isinstance(prev_bar, dict):
        prev_date = str(prev_bar.get("date") or prev_bar.get("trade_date") or "")[:10]
    lags: Dict[str, Optional[float]] = {}
    if prev_date:
        lags = co_lag_features(
            hist_bars=hist,
            co_by_date=co_by_date or {},
            asof_prev_date=prev_date,
        )
    if lags.get("yest_gap") is None and asof_date:
        lags["yest_gap"] = yest_gap_from_hist(hist, str(asof_date)[:10])
    row: Dict[str, Optional[float]] = {
        "ret_oc": float(ret_oc) if ret_oc is not None else None,
        "gap_pct": float(g) if g is not None else None,
        "ret_cc": float(ret_cc) if ret_cc is not None else None,
        "y_on_today": float(y_on_today) if y_on_today is not None else None,
        "gap_atr": gap_atr_from_hist(g, hist),
        "ret_open_to_tau": (
            float(ret_open_to_tau) if ret_open_to_tau is not None else None
        ),
        "yclose_loc": yclose_loc_from_prev(prev_bar, open_t),
        "mom3_pct": mom3_pct_from_hist(hist),
        "yest_close_loc": yest_close_loc_from_prev(prev_bar),
        "yest_range_pct": yest_range_pct_from_prev(prev_bar),
        "yest_vol_ratio": yest_vol_ratio_from_hist(hist, prev_bar),
        "dist_to_up_limit": dist_to_up_limit_pct(ret_cc, stock_code),
        "yest_gap": lags.get("yest_gap"),
        "on_ma5": lags.get("on_ma5"),
    }
    return inject_alpha158_into_row(
        row, hist, include_alpha158=include_alpha158
    )


def collect_co_panel(
    bars: List[dict],
    *,
    min_history: int = 12,
    max_window: int = 30,
    stock_code: Optional[str] = None,
    include_alpha158: bool = True,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """单票隔夜缺口面板。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = T；``ys`` = open[T+1]/close[T]-1（真实隔夜缺口，百分点）。
    特征仅用 T 开盘时已知信息（T-1 已实现 + 今开 gap）。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    if include_alpha158:
        try:
            from core.signal.factors.alpha158 import bump_window_for_alpha158

            min_history, max_window = bump_window_for_alpha158(
                ("alpha158",),
                min_history=min_history,
                max_window=max_window,
            )
        except Exception:  # noqa: BLE001
            logger.debug("co panel alpha158 window bump failed", exc_info=True)
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []

    n = len(bars or [])
    co_map = realized_co_by_date(bars)
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
        date_t = str(b_t.get("date") or "")[:10]
        row = intraday_co_feature_row(
            open_t=o_t,
            close_t=c_t,
            prev_close=pc,
            prev_open=o_prev,
            prev_prev_close=ppc,
            gap_pct=gap,
            hist_bars=window,
            prev_bar=b_prev,
            stock_code=stock_code,
            asof_date=date_t,
            co_by_date=co_map,
            include_alpha158=include_alpha158,
        )
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


def build_co_features_from_quote_bars(
    quote: Optional[dict],
    bars: Optional[Sequence[dict]],
    *,
    gap_pct: Optional[float] = None,
    ret_open_to_tau: Optional[float] = None,
    stock_code: Optional[str] = None,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    trade_date: Optional[str] = None,
    include_alpha158: bool = True,
) -> Dict[str, Optional[float]]:
    """Live / PIT：用最新 bar + quote 构造 ŷ_co 特征。

    特征口径与训练一致：ret_oc/ret_cc 取 T-1 已实现值（开盘时 close[T] 不可用）。
    ``open_t`` / ``prev_close`` / ``trade_date``：与 ``resolve_open_t`` 同源；
    昨 K 合成 quote.open 不得当 open[T]。
    """
    b = list(bars or [])
    if len(b) < 3:
        return {}
    asof = str(trade_date or "")[:10]
    code = str(stock_code or "").strip()
    if quote:
        if len(asof) < 10:
            asof = str(quote.get("date") or quote.get("trade_date") or "")[:10]
        if not code:
            code = str(quote.get("stock_code") or quote.get("code") or "").strip()
    last = b[-1] if isinstance(b[-1], dict) else {}
    last_d = str(last.get("date") or last.get("trade_date") or "")[:10]
    # 仓停在 T−1：最后一根就是昨 K，不要把 T−2 当成昨
    cache_behind = bool(asof and last_d and last_d < asof)
    if cache_behind:
        hist = list(b)
        prev = last
        prev2 = b[-2] if len(b) >= 2 and isinstance(b[-2], dict) else {}
        cur = last
    else:
        hist = hist_bars_pit(b, asof_date=asof)
        cur = last
        prev = b[-2] if len(b) >= 2 else {}
        prev2 = b[-3] if len(b) >= 3 else {}
    o_t = None
    if open_t is not None:
        try:
            o_t = float(open_t)
            if o_t <= 0:
                o_t = None
        except (TypeError, ValueError):
            o_t = None
    # 昨 K 合成 quote（date≠T）不能当今开；回测 mock quote.date=T 仍可用。
    q_day = ""
    if isinstance(quote, dict):
        q_day = str(quote.get("date") or quote.get("trade_date") or "")[:10]
    quote_is_trade_day = bool(asof and q_day == asof)
    if o_t is None and (not cache_behind or quote_is_trade_day):
        o_t = _parse_open_price(quote)
        if o_t is None or o_t <= 0:
            if not cache_behind:
                try:
                    o_t = float(cur.get("open") or 0.0)
                except (TypeError, ValueError):
                    o_t = 0.0
    if o_t is None:
        o_t = 0.0
    c_t = _parse_close_price(quote, cur)
    if c_t is None:
        c_t = 0.0
    try:
        pc = float(prev_close) if prev_close is not None else float(prev.get("close") or 0.0)
        o_prev = float(prev.get("open") or 0.0)
        ppc = float(prev2.get("close") or 0.0)
    except (TypeError, ValueError):
        return {}
    if o_t is None or o_t <= 0 or pc <= 0 or o_prev <= 0:
        return {}
    g = gap_pct
    if g is None:
        g = _gap_pct(pc, o_t)
    return intraday_co_feature_row(
        open_t=o_t,
        close_t=c_t if c_t and c_t > 0 else o_t,
        prev_close=pc,
        prev_open=o_prev,
        prev_prev_close=ppc,
        gap_pct=g,
        hist_bars=hist,
        ret_open_to_tau=ret_open_to_tau,
        prev_bar=prev if isinstance(prev, dict) else None,
        stock_code=code or None,
        asof_date=asof,
        co_by_date=realized_co_by_date(b),
        include_alpha158=include_alpha158,
    )


def enrich_co_panel_breadth(
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
    "CO_LAG_FEATURES",
    "CO_LAG_FEAT_LABELS",
    "CO_Z_FEATURES",
    "build_co_features_from_quote_bars",
    "collect_co_panel",
    "dist_to_up_limit_pct",
    "enrich_co_panel_breadth",
    "inject_alpha158_into_row",
    "intraday_co_feature_row",
    "co_lag_features",
    "realized_co_by_date",
    "theme_sample_weights",
    "yest_close_loc_from_prev",
    "yest_range_pct_from_prev",
    "yest_vol_ratio_from_hist",
]
