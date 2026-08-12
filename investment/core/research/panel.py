"""子因子 + 前瞻收益面板对齐（研究用，不依赖 quant）。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.signal.factor_registry import compute_factor, registered_factor_names


def _forward_return(bars: List[dict], idx: int, horizon: int) -> Optional[float]:
    if idx + horizon >= len(bars):
        return None
    entry = bars[idx].get("close")
    exit_p = bars[idx + horizon].get("close")
    if not entry:
        return None
    return (exit_p / entry - 1.0) * 100.0


def _research_sub_scores(
    window: List[dict],
    *,
    quote: Optional[dict] = None,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    factor_names: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Optional[float]]:
    """研究用：逐个算注册因子；默认绕开 score_bars / regime。"""
    names = factor_names or registered_factor_names()
    row: Dict[str, Optional[float]] = {}
    for key in names:
        try:
            score, _meta = compute_factor(
                key,
                window,
                quote=quote,
                index_bars=index_bars,
                fundamentals=fundamentals,
                sentiment=sentiment,
                money_flow=None,
            )
            row[key] = float(score)
        except Exception:
            row[key] = None
    return row


def _resolve_factor_names(
    *,
    respect_regime: bool,
    index_bars: Optional[List[dict]],
    config: Optional[dict],
) -> Tuple[str, ...]:
    """X4：respect_regime=True 时与 live enabled_factors 对齐。"""
    if not respect_regime:
        return registered_factor_names()
    try:
        from core.signal.config import load_signal_config
        from core.signal.regime import assess_regime

        cfg = config or load_signal_config()
        info = assess_regime(index_bars, (cfg or {}).get("regime"))
        adj = (info.get("adjustments") or {}) if isinstance(info, dict) else {}
        enabled = adj.get("enabled_factors")
        if enabled:
            names = tuple(str(x) for x in enabled if str(x).strip())
            if names:
                return names
    except Exception:
        pass
    return registered_factor_names()


def collect_subscore_forward_panel(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
    sentiment_pit: bool = False,
    respect_regime: bool = False,
    config: Optional[dict] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str]]:
    """对齐子因子与 forward return，供 OLS / 研究面板复用。

    返回 ``(xs, ys, decision_dates)``；dates 与 xs/ys 等长（YYYY-MM-DD），供日历切分 / WF。

    默认全量注册因子（不经 regime 白名单）；因子可缺测（None）。
    E2：默认按决策日 PIT 解析财务。
    FS2：``sentiment_pit=True`` 时按 as_of 注入 alt_sentiment（需标题历史）。
    X4：``respect_regime=True`` 时因子集对齐 live regime.enabled_factors。
    """
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    factor_names = _resolve_factor_names(
        respect_regime=respect_regime,
        index_bars=index_bars,
        config=config,
    )
    fund_cache: Dict[str, Optional[dict]] = {}
    sent_cache: Dict[str, Optional[dict]] = {}

    def _fund_for(decision_date: str) -> Optional[dict]:
        if not pit_fundamentals:
            return fundamentals
        if not stock_code:
            return None
        if decision_date in fund_cache:
            return fund_cache[decision_date]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(
                stock_code, as_of=decision_date, live_fallback=False
            )
            metrics = resolved.get("metrics") if resolved.get("ok") else None
        except Exception:
            metrics = None
        fund_cache[decision_date] = metrics
        return metrics

    def _sent_for(decision_date: str) -> Optional[dict]:
        if not sentiment_pit or not stock_code:
            return None
        if decision_date in sent_cache:
            return sent_cache[decision_date]
        try:
            from core.sentiment import sentiment_as_of

            pack = sentiment_as_of(stock_code, decision_date)
            sent = pack.get("sentiment") if pack.get("ok") else None
        except Exception:
            sent = None
        sent_cache[decision_date] = sent
        return sent

    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    n = len(bars or [])
    for i in range(min_history - 1, n - horizon_days):
        start = max(0, i - max_window + 1)
        window = bars[start : i + 1]
        if len(window) < 2:
            continue
        quote = {"change_raw": 0.0, "price_raw": bars[i]["close"]}
        if i >= 1:
            c0 = bars[i - 1]["close"]
            c1 = bars[i]["close"]
            if c0:
                quote["change_raw"] = round((c1 / c0 - 1.0) * 100.0, 4)

        idx_slice = index_bars[start : i + 1] if index_bars else None
        decision_date = str((bars[i] or {}).get("date") or "")[:10]

        fr = _forward_return(bars, i, horizon_days)
        if fr is None:
            continue

        row = _research_sub_scores(
            window,
            quote=quote,
            index_bars=idx_slice,
            fundamentals=_fund_for(decision_date),
            sentiment=_sent_for(decision_date),
            factor_names=factor_names,
        )
        if not any(v is not None for v in row.values()):
            continue
        xs.append(row)
        ys.append(fr)
        dates.append(decision_date)

    return xs, ys, dates
