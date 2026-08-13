"""开盘 τ 剩余收益面板：y = close[T]/open[T]-1；特征 = T-1 日线因子 + 开盘缺口等。

无未来函数：决策在开盘，标签为开盘→收盘。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.panel import _research_sub_scores, _resolve_factor_names


def _gap_pct(prev_close: float, open_px: float) -> Optional[float]:
    if prev_close is None or open_px is None:
        return None
    try:
        pc = float(prev_close)
        o = float(open_px)
    except (TypeError, ValueError):
        return None
    if pc <= 0 or o <= 0:
        return None
    return (o / pc - 1.0) * 100.0


def _open_to_close_pct(open_px: float, close_px: float) -> Optional[float]:
    try:
        o = float(open_px)
        c = float(close_px)
    except (TypeError, ValueError):
        return None
    if o <= 0 or c <= 0:
        return None
    return (c / o - 1.0) * 100.0


def collect_rem_open_panel(
    bars: List[dict],
    *,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    respect_regime: bool = False,
    config: Optional[dict] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """单票 open→close rem 面板。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = 交易日 T（开盘决策日）；因子窗口截止 T-1。
    ``meta_rows`` 含 gap_pct / theme 辅助字段。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    factor_names = _resolve_factor_names(
        respect_regime=respect_regime,
        index_bars=index_bars,
        config=config,
    )
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []

    n = len(bars or [])
    # i = index of day T；需要 i-1 有昨收
    for i in range(min_history, n):
        b_t = bars[i]
        b_prev = bars[i - 1]
        try:
            o = float(b_t.get("open"))
            c = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0 or pc <= 0:
            continue
        y = _open_to_close_pct(o, c)
        gap = _gap_pct(pc, o)
        if y is None or gap is None:
            continue
        # 因子截止 T-1：窗口 bars[:i] 末根为 T-1
        window = bars[max(0, i - max_window) : i]
        if len(window) < min_history:
            continue
        row = _research_sub_scores(
            window,
            index_bars=index_bars,
            fundamentals=fundamentals,
            factor_names=factor_names,
        )
        row["gap_pct"] = float(gap)
        # 开盘已实现相对昨收（与 gap 同义，显式留给模型）
        row["open_gap"] = float(gap)
        date_t = str(b_t.get("date") or "")[:10]
        xs.append(row)
        ys.append(float(y))
        dates.append(date_t)
        metas.append(
            {
                "stock_code": stock_code,
                "date": date_t,
                "gap_pct": float(gap),
                "open": o,
                "close": c,
                "prev_close": pc,
                "y_rem": float(y),
            }
        )
    return xs, ys, dates, metas


def attach_cross_section_breadth(
    panels: Sequence[Dict[str, Any]],
    *,
    gap_trigger_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    """多票面板：按日计算 gap 广度，写回每行 xs 的 sector_gap_breadth（全市场代理）。

    ``panels`` 元素：``{code, xs, ys, dates, metas}``。
    """
    # date -> list of gaps
    by_date: Dict[str, List[float]] = {}
    for p in panels:
        for m in p.get("metas") or []:
            d = str(m.get("date") or "")[:10]
            g = m.get("gap_pct")
            if not d or g is None:
                continue
            by_date.setdefault(d, []).append(float(g))

    breadth_by_date: Dict[str, float] = {}
    theme_by_date: Dict[str, int] = {}
    trigger = float(gap_trigger_pct)
    for d, gaps in by_date.items():
        if not gaps:
            continue
        hit = sum(1 for g in gaps if g >= trigger)
        b = hit / len(gaps)
        breadth_by_date[d] = round(b, 4)
        # 主题日：广度≥0.5 或 |gap| 中位≥trigger
        gaps_sorted = sorted(abs(g) for g in gaps)
        med = gaps_sorted[len(gaps_sorted) // 2]
        theme_by_date[d] = 1 if (b >= 0.5 or med >= trigger) else 0

    out: List[Dict[str, Any]] = []
    for p in panels:
        xs = [dict(r) for r in (p.get("xs") or [])]
        metas = [dict(m) for m in (p.get("metas") or [])]
        dates = list(p.get("dates") or [])
        for i, d in enumerate(dates):
            b = breadth_by_date.get(d)
            th = theme_by_date.get(d, 0)
            if i < len(xs):
                xs[i]["sector_gap_breadth"] = b
                xs[i]["theme_day"] = float(th)
            if i < len(metas):
                metas[i]["sector_gap_breadth"] = b
                metas[i]["theme_day"] = th
        out.append({**p, "xs": xs, "metas": metas, "dates": dates})
    return out


def theme_sample_weights(
    metas: Sequence[dict],
    *,
    theme_boost: float = 1.5,
    normal_weight: float = 1.0,
) -> List[float]:
    """R2：主题日样本提权（开盘可得 theme_day，无泄漏）。"""
    boost = max(0.1, float(theme_boost))
    base = max(0.1, float(normal_weight))
    weights: List[float] = []
    for m in metas:
        if int(m.get("theme_day") or 0) == 1:
            weights.append(boost)
        else:
            weights.append(base)
    return weights


def price_at_tau_from_minutes(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str = "09:45",
) -> Optional[float]:
    """R1：从分钟线取 τ 时刻价（≤τ 的最后一根）。无数据返回 None。"""
    day = str(trade_date or "")[:10]
    if not day or not minute_bars:
        return None
    target = tau_hm.replace(":", "")
    best = None
    best_hm = ""
    for b in minute_bars:
        if not isinstance(b, dict):
            continue
        t = str(b.get("time") or b.get("datetime") or b.get("date") or "")
        if day not in t:
            continue
        # 抽取 HHMM
        hm = ""
        for part in t.replace("T", " ").split(" "):
            if ":" in part:
                hm = part[:5].replace(":", "")
                break
        if not hm:
            continue
        if hm <= target and hm >= best_hm:
            try:
                px = float(b.get("close") or b.get("price"))
            except (TypeError, ValueError):
                continue
            if px > 0:
                best = px
                best_hm = hm
    return best


def collect_rem_tau_panel(
    bars: List[dict],
    minute_bars: Optional[List[dict]] = None,
    *,
    tau_hm: str = "09:45",
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """R1：τ=09:45 剩余收益面板；无分钟线时跳过该日（不回退泄漏）。"""
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    factor_names = _resolve_factor_names(
        respect_regime=False, index_bars=index_bars, config=None
    )
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []
    n = len(bars or [])
    for i in range(min_history, n):
        b_t = bars[i]
        b_prev = bars[i - 1]
        date_t = str(b_t.get("date") or "")[:10]
        try:
            o = float(b_t.get("open"))
            c = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
        except (TypeError, ValueError):
            continue
        px_tau = price_at_tau_from_minutes(
            minute_bars or [], trade_date=date_t, tau_hm=tau_hm
        )
        if px_tau is None or px_tau <= 0 or c <= 0:
            continue
        y = (c / px_tau - 1.0) * 100.0
        gap = _gap_pct(pc, o)
        ret_open_tau = (px_tau / o - 1.0) * 100.0 if o > 0 else None
        window = bars[max(0, i - max_window) : i]
        if len(window) < min_history:
            continue
        row = _research_sub_scores(
            window,
            index_bars=index_bars,
            fundamentals=fundamentals,
            factor_names=factor_names,
        )
        row["gap_pct"] = float(gap) if gap is not None else None
        row["open_gap"] = row["gap_pct"]
        row["ret_open_to_tau"] = ret_open_tau
        xs.append(row)
        ys.append(float(y))
        dates.append(date_t)
        metas.append(
            {
                "stock_code": stock_code,
                "date": date_t,
                "tau": tau_hm,
                "gap_pct": gap,
                "price_tau": px_tau,
                "y_rem": float(y),
            }
        )
    return xs, ys, dates, metas
