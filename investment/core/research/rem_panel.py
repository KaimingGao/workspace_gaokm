"""开盘 τ 剩余收益面板：y = close[T]/open[T]-1；特征 = 开盘 Z（缺口/ATR/截面）。

无未来函数：决策在开盘，标签为开盘→收盘。日线因子不在此计算（已在 ŷ_EOD）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

GAP_ATR_WINDOW = 14
GAP_ATR_CLIP = 10.0
_SECTOR_REL_MIN_N = 3


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


def _finite_median(vals: Sequence[float]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    if not xs:
        return None
    return float(median(xs))


def hist_bars_pit(
    bars: Optional[Sequence[dict]],
    *,
    asof_date: Optional[str] = None,
) -> List[dict]:
    """去掉 asof 当日 K 线，ATR / 日线窗口不吃 T 日振幅。"""
    hist = list(bars or [])
    day = str(asof_date or "")[:10]
    if day and hist and str(hist[-1].get("date") or "")[:10] == day:
        return hist[:-1]
    return hist


def gap_atr_from_hist(
    gap_pct: Optional[float],
    hist_bars: Optional[Sequence[dict]],
    *,
    window: int = GAP_ATR_WINDOW,
    clip: float = GAP_ATR_CLIP,
) -> Optional[float]:
    """缺口 / ATR%（T−1 窗口）。量纲：跳了几个 ATR；clip 防极小波动炸值。"""
    if gap_pct is None:
        return None
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return None
    try:
        from core.t0.rules import atr_pct_from_bars

        atr = atr_pct_from_bars(list(hist_bars or []), int(window))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in rem_panel.py", exc_info=True)
        atr = None
    if atr is None or float(atr) < 1e-6:
        return None
    v = g / float(atr)
    cap = float(clip) if clip is not None else None
    if cap is not None and cap > 0:
        v = max(-cap, min(cap, v))
    return round(v, 4)


def sector_gap_reference_by_code(
    gaps_by_code: Dict[str, Optional[float]],
    *,
    sector_map: Optional[Dict[str, str]] = None,
    min_sector_n: int = _SECTOR_REL_MIN_N,
) -> Dict[str, Optional[float]]:
    """每个 code 的参照缺口：同行中位（n≥门槛）否则全截面中位。"""
    valid = {
        str(c).strip(): float(g)
        for c, g in (gaps_by_code or {}).items()
        if str(c or "").strip() and g is not None
    }
    pool_med = _finite_median(list(valid.values()))
    sm = sector_map if isinstance(sector_map, dict) else {}
    try:
        from core.portfolio_optimize import _sector_for
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in rem_panel.py", exc_info=True)
        def _sector_for(code: str, m: Optional[Dict[str, str]] = None) -> str:  # type: ignore
            return str((m or {}).get(code) or "")

    code_sec: Dict[str, str] = {}
    by_sec: Dict[str, List[float]] = {}
    for c, g in valid.items():
        sec = str(_sector_for(c, sm) or "").strip()
        if not sec:
            continue
        code_sec[c] = sec
        by_sec.setdefault(sec, []).append(g)
    need = max(2, int(min_sector_n or _SECTOR_REL_MIN_N))
    sec_med = {
        s: _finite_median(gs) for s, gs in by_sec.items() if len(gs) >= need
    }
    out: Dict[str, Optional[float]] = {}
    for c, g in (gaps_by_code or {}).items():
        key = str(c or "").strip()
        if not key or g is None:
            out[key] = None
            continue
        med = sec_med.get(code_sec.get(key, ""), None)
        if med is None:
            med = pool_med
        out[key] = round(float(med), 4) if med is not None else None
    return out


def gap_vs_sector_value(
    gap_pct: Optional[float],
    sector_median: Optional[float],
) -> Optional[float]:
    if gap_pct is None or sector_median is None:
        return None
    try:
        return round(float(gap_pct) - float(sector_median), 4)
    except (TypeError, ValueError):
        return None


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
    ``decision_dates`` = 交易日 T（开盘决策日）；ATR 窗口截止 T-1。
    只写 Z 特征（缺口 / ATR）；日线因子已在 ŷ_EOD，此处不算。
    ``meta_rows`` 含 gap_pct 等辅助字段。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    _ = (index_bars, fundamentals, respect_regime, config)
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
        # ATR 截止 T-1：窗口 bars[:i] 末根为 T-1
        window = bars[max(0, i - max_window) : i]
        if len(window) < min_history:
            continue
        row: Dict[str, Optional[float]] = {
            "gap_pct": float(gap),
            "open_gap": float(gap),
            "gap_atr": gap_atr_from_hist(gap, window),
        }
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
    sector_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """多票面板：按日计算 gap 广度，写回每行 xs 的 sector_gap_breadth（全市场代理）。

    同时写 ``gap_vs_sector`` = 个股缺口 − 同行中位（同伴不足则减全截面中位）。
    ``panels`` 元素：``{code, xs, ys, dates, metas}``。
    """
    # date -> [(code, gap), ...]  and ret_open_to_tau by date
    by_date: Dict[str, List[Tuple[str, float]]] = {}
    ret_by_date: Dict[str, List[float]] = {}
    for p in panels:
        code_p = str(p.get("code") or "").strip()
        xs_p = list(p.get("xs") or [])
        for i, m in enumerate(p.get("metas") or []):
            d = str(m.get("date") or "")[:10]
            g = m.get("gap_pct")
            c = str(m.get("stock_code") or code_p or "").strip()
            if not d or g is None:
                continue
            by_date.setdefault(d, []).append((c, float(g)))
            rot = None
            if i < len(xs_p) and xs_p[i].get("ret_open_to_tau") is not None:
                try:
                    rot = float(xs_p[i]["ret_open_to_tau"])
                except (TypeError, ValueError):
                    rot = None
            if rot is None and m.get("ret_open_to_tau") is not None:
                try:
                    rot = float(m.get("ret_open_to_tau"))
                except (TypeError, ValueError):
                    rot = None
            if rot is not None:
                ret_by_date.setdefault(d, []).append(rot)

    sm = sector_map
    if sm is None:
        try:
            from core.portfolio_optimize import load_sector_map

            sm = load_sector_map() or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in rem_panel.py", exc_info=True)
            sm = {}

    breadth_by_date: Dict[str, float] = {}
    theme_by_date: Dict[str, int] = {}
    ref_by_date: Dict[str, Dict[str, Optional[float]]] = {}
    sector_ret_by_date: Dict[str, Optional[float]] = {}
    trigger = float(gap_trigger_pct)
    for d, pairs in by_date.items():
        if not pairs:
            continue
        gaps = [g for _c, g in pairs]
        hit = sum(1 for g in gaps if g >= trigger)
        b = hit / len(gaps)
        breadth_by_date[d] = round(b, 4)
        from core.research.rem_theme import resolve_theme_day

        theme_by_date[d] = int(
            resolve_theme_day(
                sector_breadth=b,
                pool_gaps=gaps,
                gap_trigger_pct=trigger,
            )
        )
        gaps_map: Dict[str, Optional[float]] = {}
        for c, g in pairs:
            if c:
                gaps_map[c] = g
        ref_by_date[d] = sector_gap_reference_by_code(gaps_map, sector_map=sm)
        sector_ret_by_date[d] = _finite_median(ret_by_date.get(d) or [])

    out: List[Dict[str, Any]] = []
    for p in panels:
        code_p = str(p.get("code") or "").strip()
        xs = [dict(r) for r in (p.get("xs") or [])]
        metas = [dict(m) for m in (p.get("metas") or [])]
        dates = list(p.get("dates") or [])
        for i, d in enumerate(dates):
            b = breadth_by_date.get(d)
            th = theme_by_date.get(d, 0)
            code_i = code_p
            if i < len(metas):
                code_i = str(metas[i].get("stock_code") or code_p or "").strip()
            gap_i = None
            if i < len(xs) and xs[i].get("gap_pct") is not None:
                try:
                    gap_i = float(xs[i]["gap_pct"])
                except (TypeError, ValueError):
                    gap_i = None
            elif i < len(metas) and metas[i].get("gap_pct") is not None:
                try:
                    gap_i = float(metas[i]["gap_pct"])
                except (TypeError, ValueError):
                    gap_i = None
            ref = (ref_by_date.get(d) or {}).get(code_i)
            rel = gap_vs_sector_value(gap_i, ref)
            sret = sector_ret_by_date.get(d)
            if i < len(xs):
                xs[i]["sector_gap_breadth"] = b
                xs[i]["theme_day"] = float(th)
                xs[i]["gap_vs_sector"] = rel
                if sret is not None:
                    xs[i]["sector_ret_to_tau"] = round(float(sret), 6)
            if i < len(metas):
                metas[i]["sector_gap_breadth"] = b
                metas[i]["theme_day"] = th
                metas[i]["gap_vs_sector"] = rel
                metas[i]["sector_gap_median"] = ref
                if sret is not None:
                    metas[i]["sector_ret_to_tau"] = round(float(sret), 6)
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
    """R1：τ=09:45 剩余收益面板；无分钟线时跳过该日（不回退泄漏）。只写 Z，不算日线因子。"""
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    _ = (index_bars, fundamentals)
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
        row: Dict[str, Optional[float]] = {
            "gap_pct": float(gap) if gap is not None else None,
            "open_gap": float(gap) if gap is not None else None,
            "gap_atr": gap_atr_from_hist(gap, window),
            "ret_open_to_tau": ret_open_tau,
        }
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
