"""ŷ_τ 训练面板：y = close[T]/open[T]-1；特征 = 开盘 Z（缺口/ATR/截面）。

无未来函数：决策在开盘，标签为开盘→收盘。日线因子不在此计算（已在 ŷ_EOD）。
另加 PIT 历史真实 open→close（tau_lag1 / tau_ma5，不含当日）。
"""


import logging

logger = logging.getLogger(__name__)
from statistics import median, stdev
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

GAP_ATR_WINDOW = 14
GAP_ATR_CLIP = 10.0
_SECTOR_REL_MIN_N = 3
LABEL_LAG_WINDOW = 5
# tau_std5 / yest_gap 曾入模，做 T 回测变差后撤回
TAU_LAG_FEATURES = ("tau_lag1", "tau_ma5")
TAU_LAG_FEAT_LABELS = {
    "tau_lag1": "昨真实开→收 %",
    "tau_ma5": "近5日真实开→收均 %",
}

from core.signal.minute_tau_grid import (
    DEFAULT_MINUTE_TAU_GRID,
    DEFAULT_T0_TRAIN_TAU_GRID_5M,
    minute_tau_grid_5m_range,
)


def normalize_minute_tau_grid(
    tau_hm: Optional[str] = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> List[str]:
    """解析训练用 τ 网格；空则回退单点 ``tau_hm`` / 默认网格。"""
    if tau_grid is not None:
        out: List[str] = []
        for t in tau_grid:
            s = str(t or "").strip()
            if not s or s.lower() == "open":
                continue
            if ":" not in s and len(s) == 4 and s.isdigit():
                s = f"{s[:2]}:{s[2:]}"
            if s not in out:
                out.append(s)
        if out:
            return out
    hm = str(tau_hm or "").strip()
    if hm and hm.lower() not in ("", "open"):
        if ":" not in hm and len(hm) == 4 and hm.isdigit():
            hm = f"{hm[:2]}:{hm[2:]}"
        return [hm]
    return list(DEFAULT_MINUTE_TAU_GRID)


def tau_elapsed_min_from_open(tau_hm: str) -> Optional[float]:
    """相对 09:30 的分钟数（5m 前缀长度代理）。"""
    s = str(tau_hm or "").strip().replace(":", "")
    if len(s) != 4 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:])
    return float(h * 60 + m - (9 * 60 + 30))


def is_open_minute_clock(tau_hm: str) -> bool:
    """09:30 / 已开盘 0 分钟：无 5m 前缀，训练行只保留开盘 Z。"""
    elapsed = tau_elapsed_min_from_open(tau_hm)
    return elapsed is not None and elapsed <= 0.0


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


def _series_std(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    if len(vals) < 2:
        return 0.0
    try:
        return round(float(stdev(vals)), 6)
    except Exception:  # noqa: BLE001
        return None


def _series_sign_streak(vals: Sequence[float]) -> Optional[float]:
    """最近有效值起、同号连续天数（带符号）；0 断开；无样本则空。"""
    if not vals:
        return None

    def _sign(x: float) -> int:
        if x > 0:
            return 1
        if x < 0:
            return -1
        return 0

    s0 = _sign(float(vals[0]))
    if s0 == 0:
        return 0.0
    n = 0
    for v in vals:
        if _sign(float(v)) != s0:
            break
        n += 1
    return float(s0 * n)


def label_lag_features(
    *,
    hist_bars: Sequence[dict],
    by_date: Mapping[str, float],
    asof_date: str,
    window: int = LABEL_LAG_WINDOW,
    lag1_key: str,
    ma_key: Optional[str] = None,
    std_key: Optional[str] = None,
    streak_key: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """PIT：只用 **严格早于** asof 且 ``by_date`` 有值的交易日；最多 ``window`` 天。

    asof 缺失则全部留空（宁缺，避免 hist 含 T 日 K 时把当日 label 当因子）。
    ``by_date`` 即使含 T/未来日也不读。
    """
    asof = str(asof_date or "")[:10]

    def _empty() -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {lag1_key: None}
        if ma_key:
            out[ma_key] = None
        if std_key:
            out[std_key] = None
        if streak_key:
            out[streak_key] = None
        return out

    if len(asof) < 10:
        return _empty()
    past = {
        str(k)[:10]: v
        for k, v in (by_date or {}).items()
        if len(str(k)[:10]) >= 10 and str(k)[:10] < asof
    }
    win = max(1, int(window or LABEL_LAG_WINDOW))
    vals: List[float] = []
    seen: set = set()
    for bar in reversed(list(hist_bars or [])):
        if not isinstance(bar, dict):
            continue
        d = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if len(d) < 10 or d in seen:
            continue
        if d >= asof:
            continue
        seen.add(d)
        y = past.get(d)
        if y is None:
            continue
        try:
            vals.append(float(y))
        except (TypeError, ValueError):
            continue
        if len(vals) >= win:
            break
    lag1 = round(vals[0], 6) if vals else None
    out: Dict[str, Optional[float]] = {lag1_key: lag1}
    if ma_key:
        out[ma_key] = round(sum(vals) / float(len(vals)), 6) if vals else None
    if std_key:
        out[std_key] = _series_std(vals)
    if streak_key:
        out[streak_key] = _series_sign_streak(vals)
    return out


def yest_gap_from_hist(
    hist_bars: Optional[Sequence[dict]],
    asof_date: str,
) -> Optional[float]:
    """昨隔夜缺口 (O_{T-1}/C_{T-2}-1)×100；缺两根则空。不是当日 gap_pct。"""
    asof = str(asof_date or "")[:10]
    if len(asof) < 10:
        return None
    hist = hist_bars_pit(hist_bars, asof_date=asof)
    if len(hist) < 2:
        return None
    b_yest = hist[-1]
    b_prev = hist[-2]
    g = _gap_pct(b_prev.get("close"), b_yest.get("open"))
    if g is None:
        return None
    return round(float(g), 6)


def realized_tau_by_date(bars: Optional[Sequence[dict]]) -> Dict[str, float]:
    """各日真实 y_τ = open→close %；算不出的日不进表。"""
    out: Dict[str, float] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        d = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if len(d) < 10:
            continue
        y = _open_to_close_pct(bar.get("open"), bar.get("close"))
        if y is None:
            continue
        out[d] = round(float(y), 6)
    return out


def tau_lag_features(
    *,
    hist_bars: Sequence[dict],
    tau_by_date: Mapping[str, float],
    asof_date: str,
    window: int = LABEL_LAG_WINDOW,
) -> Dict[str, Optional[float]]:
    return label_lag_features(
        hist_bars=hist_bars,
        by_date=tau_by_date,
        asof_date=asof_date,
        window=window,
        lag1_key="tau_lag1",
        ma_key="tau_ma5",
    )


def attach_tau_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Sequence[dict],
    asof_date: str,
) -> Dict[str, Any]:
    """把 tau_lag1 / tau_ma5 写入特征行；缺历史则留空，不挡打分。"""
    out: Dict[str, Any] = dict(feats or {})
    hist = hist_bars_pit(hist_bars, asof_date=asof_date)
    lags = tau_lag_features(
        hist_bars=hist,
        tau_by_date=realized_tau_by_date(hist),
        asof_date=asof_date,
    )
    for k in TAU_LAG_FEATURES:
        out[k] = lags.get(k)
    return out


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
        logger.debug("catch except Exception: in tau_panel.py", exc_info=True)
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
        logger.debug("catch except Exception: in tau_panel.py", exc_info=True)
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
        med = sec_med.get(code_sec.get(key, ""))
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


def yclose_loc_from_prev(prev_bar: Optional[dict], open_px: float) -> Optional[float]:
    """昨收相对昨高低的位置 [0,1]；开盘可得，无泄漏。"""
    if not isinstance(prev_bar, dict):
        return None
    try:
        hi = float(prev_bar.get("high"))
        lo = float(prev_bar.get("low"))
        o = float(open_px)
    except (TypeError, ValueError):
        return None
    if hi <= lo or o <= 0:
        return None
    return round(max(0.0, min(1.0, (o - lo) / (hi - lo))), 4)


def mom3_pct_from_hist(hist: Sequence[dict]) -> Optional[float]:
    """近 3 日收盘动量 %（窗口末根为 T−1）。"""
    bars = [b for b in (hist or []) if isinstance(b, dict)]
    if len(bars) < 4:
        return None
    try:
        c0 = float(bars[-4].get("close"))
        c1 = float(bars[-1].get("close"))
    except (TypeError, ValueError):
        return None
    if c0 <= 0 or c1 <= 0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 4)


def collect_tau_open_panel(
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
    """单票 open→close 的 y_τ 面板（τ=open）。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = 交易日 T（开盘决策日）；ATR 窗口截止 T-1。
    只写 Z 特征（缺口 / ATR）；日线因子已在 ŷ_EOD，此处不算。
    ``meta_rows`` 含 ``y_tau``（= open→close %）等辅助字段。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    _ = (index_bars, fundamentals, respect_regime, config)
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []

    n = len(bars or [])
    tau_map = realized_tau_by_date(bars)
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
        date_t = str(b_t.get("date") or "")[:10]
        lags = tau_lag_features(
            hist_bars=window,
            tau_by_date=tau_map,
            asof_date=date_t,
        )
        row: Dict[str, Optional[float]] = {
            "gap_pct": float(gap),
            "open_gap": float(gap),
            "gap_atr": gap_atr_from_hist(gap, window),
            "yclose_loc": yclose_loc_from_prev(b_prev, o),
            "mom3_pct": mom3_pct_from_hist(window),
        }
        for k in TAU_LAG_FEATURES:
            row[k] = lags.get(k)
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
                "y_tau": float(y),
                "yclose_loc": row.get("yclose_loc"),
                "mom3_pct": row.get("mom3_pct"),
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
    ``sector_ret_to_tau`` 按 **(date, τ)** 聚合，避免变长前缀把不同时钟的开→τ 混中位。
    ``panels`` 元素：``{code, xs, ys, dates, metas}``。
    """
    # date -> [(code, gap), ...]  unique per (date, code)
    by_date: Dict[str, List[Tuple[str, float]]] = {}
    seen_gap: set = set()
    # (date, tau) -> ret_open_to_tau list
    ret_by_date_tau: Dict[Tuple[str, str], List[float]] = {}
    for p in panels:
        code_p = str(p.get("code") or "").strip()
        xs_p = list(p.get("xs") or [])
        for i, m in enumerate(p.get("metas") or []):
            d = str(m.get("date") or "")[:10]
            g = m.get("gap_pct")
            c = str(m.get("stock_code") or code_p or "").strip()
            tau_k = str(m.get("tau") or "open").strip() or "open"
            if not d or g is None:
                continue
            gap_key = (d, c)
            if gap_key not in seen_gap:
                seen_gap.add(gap_key)
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
                ret_by_date_tau.setdefault((d, tau_k), []).append(rot)

    sm = sector_map
    if sm is None:
        try:
            from core.portfolio_optimize import load_sector_map

            sm = load_sector_map() or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in tau_panel.py", exc_info=True)
            sm = {}

    breadth_by_date: Dict[str, float] = {}
    theme_by_date: Dict[str, int] = {}
    gaps_by_date: Dict[str, List[float]] = {}
    ref_by_date: Dict[str, Dict[str, Optional[float]]] = {}
    sector_ret_by_date_tau: Dict[Tuple[str, str], Optional[float]] = {}
    trigger = float(gap_trigger_pct)
    from core.research.tau_theme import resolve_theme_day

    for d, pairs in by_date.items():
        if not pairs:
            continue
        gaps = [g for _c, g in pairs]
        gaps_by_date[d] = gaps
        # 大缺口广度：|gap|≥trigger（旧实现只计正缺口 → theme 几乎恒 0）
        hit = sum(1 for g in gaps if abs(float(g)) >= trigger)
        b = hit / len(gaps)
        breadth_by_date[d] = round(b, 4)
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

    for key, vals in ret_by_date_tau.items():
        sector_ret_by_date_tau[key] = _finite_median(vals)

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
            tau_k = "open"
            if i < len(metas):
                code_i = str(metas[i].get("stock_code") or code_p or "").strip()
                tau_k = str(metas[i].get("tau") or "open").strip() or "open"
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
            sret = sector_ret_by_date_tau.get((d, tau_k))
            # 日级主题 OR 本票大缺口（与 resolve_theme_day 对齐）
            th_row = int(
                resolve_theme_day(
                    gap_pct=gap_i,
                    sector_breadth=b,
                    pool_gaps=gaps_by_date.get(d),
                    gap_trigger_pct=trigger,
                )
            )
            if th_row:
                th = 1
            if i < len(xs):
                xs[i]["sector_gap_breadth"] = b
                xs[i]["theme_day"] = float(th)
                xs[i]["gap_vs_sector"] = rel
                if sret is not None:
                    xs[i]["sector_ret_to_tau"] = round(float(sret), 6)
                    rot = xs[i].get("ret_open_to_tau")
                    if rot is not None:
                        try:
                            xs[i]["ret_vs_sector"] = round(
                                float(rot) - float(sret), 6
                            )
                        except (TypeError, ValueError):
                            pass
            if i < len(metas):
                metas[i]["sector_gap_breadth"] = b
                metas[i]["theme_day"] = th
                metas[i]["gap_vs_sector"] = rel
                metas[i]["sector_gap_median"] = ref
                if sret is not None:
                    metas[i]["sector_ret_to_tau"] = round(float(sret), 6)
                    if i < len(xs) and xs[i].get("ret_vs_sector") is not None:
                        metas[i]["ret_vs_sector"] = xs[i]["ret_vs_sector"]
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
    tau_hm: str = "10:30",
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


def _minute_bar_day(b: dict) -> str:
    if not isinstance(b, dict):
        return ""
    d = str(b.get("date") or "")[:10]
    if len(d) == 10 and d[4] == "-":
        return d
    t = str(b.get("datetime") or b.get("time") or "")
    return t[:10] if len(t) >= 10 else ""


def _minute_bar_hm(b: dict) -> str:
    t = str(b.get("datetime") or b.get("time") or b.get("date") or "")
    for part in t.replace("T", " ").split(" "):
        if ":" in part:
            return part[:5].replace(":", "")
    return ""


def minute_session_open_close(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
) -> Tuple[Optional[float], Optional[float]]:
    """同日分钟序列的开盘价（首根 open）与收盘价（末根 close）。"""
    day = str(trade_date or "")[:10]
    if not day or not minute_bars:
        return None, None
    first_hm = ""
    last_hm = ""
    open_px: Optional[float] = None
    close_px: Optional[float] = None
    for b in minute_bars:
        if not isinstance(b, dict):
            continue
        if _minute_bar_day(b) != day and day not in str(b.get("datetime") or ""):
            continue
        hm = _minute_bar_hm(b)
        if not hm:
            continue
        try:
            o = float(b.get("open") or 0.0) or None
            c = float(b.get("close") or b.get("price") or 0.0) or None
        except (TypeError, ValueError):
            continue
        if open_px is None or hm < first_hm:
            if o is not None and o > 0:
                open_px = o
                first_hm = hm
            elif c is not None and c > 0:
                open_px = c
                first_hm = hm
        if close_px is None or hm >= last_hm:
            if c is not None and c > 0:
                close_px = c
                last_hm = hm
    return open_px, close_px


def collect_tau_intraday_panel(
    bars: List[dict],
    minute_bars: Optional[List[dict]] = None,
    *,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """分钟 τ 面板（变长前缀 / 少数时钟）：特征 ≤τ，标签 y_τ = 日线 close/open−1。

    与 τ=open 头同一标签口径（日线 open→close）；仅信息集多了前缀分钟路径。
    ``tau_grid`` 非空时：同一交易日在多个 τ 各采一行（共享 β，**同日 y 相同**）；
    默认网格 ``09:30|09:35|…|11:00`` 每 5m（09:30 无分钟前缀只留开盘 Z；与做 T v6 扫描至 11:00 对齐；不含 13:00 / 14:00）。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    _ = (index_bars, fundamentals)
    clocks = normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []
    n = len(bars or [])
    tau_map = realized_tau_by_date(bars)
    for i in range(min_history, n):
        b_t = bars[i]
        b_prev = bars[i - 1]
        date_t = str(b_t.get("date") or "")[:10]
        date_prev = str(b_prev.get("date") or "")[:10]
        try:
            o = float(b_t.get("open"))
            c = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0 or pc <= 0:
            continue
        y_oc = _open_to_close_pct(o, c)
        if y_oc is None:
            continue
        o_min, c_min = minute_session_open_close(
            minute_bars or [], trade_date=date_t
        )
        gap = _gap_pct(pc, o)
        window = bars[max(0, i - max_window) : i]
        if len(window) < min_history:
            continue
        from core.signal.minute_tau_feats import extract_minute_tau_pack

        _, c_prev_min = minute_session_open_close(
            minute_bars or [], trade_date=date_prev
        )
        lags = tau_lag_features(
            hist_bars=window,
            tau_by_date=tau_map,
            asof_date=date_t,
        )
        for clock in clocks:
            open_clock = is_open_minute_clock(clock)
            px_tau = price_at_tau_from_minutes(
                minute_bars or [], trade_date=date_t, tau_hm=clock
            )
            if (px_tau is None or px_tau <= 0) and open_clock:
                # 当日须有分钟会话（5m 通常从 09:35 起）；无缓存日不混进 09:30
                if not (o_min and o_min > 0):
                    continue
                px_tau = o_min
            if px_tau is None or px_tau <= 0:
                continue
            pack = extract_minute_tau_pack(
                minute_bars or [],
                trade_date=date_t,
                tau_hm=clock,
                open_px=o_min if o_min and o_min > 0 else o,
                prev_close=c_prev_min if c_prev_min and c_prev_min > 0 else None,
            )
            if len(pack) < 2:
                if not open_clock:
                    continue
                pack = {}
            row: Dict[str, Optional[float]] = {
                "gap_pct": float(gap) if gap is not None else None,
                "open_gap": float(gap) if gap is not None else None,
                "gap_atr": gap_atr_from_hist(gap, window),
            }
            for k in TAU_LAG_FEATURES:
                row[k] = lags.get(k)
            row.update(pack)
            if "ret_open_to_tau" not in row:
                open_for_ret = float(o_min) if o_min and o_min > 0 else float(o)
                if open_for_ret > 0:
                    row["ret_open_to_tau"] = (px_tau / open_for_ret - 1.0) * 100.0
            elapsed = tau_elapsed_min_from_open(clock)
            if elapsed is not None:
                row["tau_elapsed_min"] = elapsed
            xs.append(row)
            ys.append(float(y_oc))
            dates.append(date_t)
            metas.append(
                {
                    "stock_code": stock_code,
                    "date": date_t,
                    "tau": clock,
                    "gap_pct": gap,
                    "price_tau": px_tau,
                    "open": o,
                    "close": c,
                    "open_minute": o_min,
                    "close_minute": c_min,
                    "y_tau": float(y_oc),
                    "ret_open_to_tau": row.get("ret_open_to_tau"),
                    "tau_elapsed_min": elapsed,
                }
            )
    return xs, ys, dates, metas

# --- Backward-compatible aliases (deprecated) ---
collect_rem_open_panel = collect_tau_open_panel
collect_rem_tau_panel = collect_tau_intraday_panel

