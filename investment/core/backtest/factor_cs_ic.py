"""S1 · 池内逐因子日频截面 IC（Pearson + Spearman）。

与单票时序 IC（factor_report）不同：每个决策日横截面 corr(因子分, 前瞻收益)，再对日序列汇总。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from core.backtest.pool_ic import (
    _bars_by_date,
    _common_dates,
    _forward_return_pct,
    _pearson,
)


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None

    def _ranks(vals: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    return _pearson(_ranks(xs), _ranks(ys))


def _agg_ic_series(daily: List[float]) -> Dict[str, Any]:
    if len(daily) < 3:
        return {
            "ic_mean": None,
            "ic_std": None,
            "icir": None,
            "day_count": len(daily),
            "positive_ic_ratio": None,
        }
    mean = sum(daily) / len(daily)
    var = sum((x - mean) ** 2 for x in daily) / len(daily)
    std = math.sqrt(var)
    icir = (mean / std) if std > 1e-12 else None
    pos = sum(1 for x in daily if x > 0)
    return {
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 4) if icir is not None else None,
        "day_count": len(daily),
        "positive_ic_ratio": round(pos / len(daily), 4),
    }


def compute_factor_cross_section_ic(
    stock_bars: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    min_names: int = 5,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    pit_fundamentals: bool = True,
    factor_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """对每个注册因子算池内日频截面 IC（Pearson / Spearman）。"""
    from core.backtest.engine import _mock_quote_from_bars
    from core.signal.cross_section_batch import score_window_as_item
    from core.signal.factor_registry import registered_factor_names

    if not stock_bars or len(stock_bars) < 2:
        return {"success": False, "ok": False, "error": "标的不足", "factors": []}

    names = list(factor_names or registered_factor_names())
    if not names:
        return {"success": False, "ok": False, "error": "无注册因子", "factors": []}

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    # 组内宇宙可能只有 2～3 只；显式传入时可低至 2（日截面仍偏噪）
    min_names = max(2, int(min_names or 5))

    date_maps = {c: _bars_by_date(b) for c, b in stock_bars.items()}
    dates = _common_dates(stock_bars)
    try:
        from core.market_calendar import filter_trading_dates

        dates = filter_trading_dates(dates)
        calendar_tag = "cn_lite"
    except Exception:
        calendar_tag = "none"
    n = len(dates)
    need = min_history + horizon_days
    if n < need:
        return {
            "success": False,
            "ok": False,
            "error": f"共同交易日不足（{n}<{need}）",
            "factors": [],
            "common_dates": n,
            "calendar": calendar_tag,
        }

    fund_cache: Dict[Tuple[str, str], Optional[dict]] = {}

    def _fund_for(code: str, decision_date: str) -> Optional[dict]:
        if not pit_fundamentals:
            return (fundamentals_by_code or {}).get(code)
        key = (code, decision_date)
        if key in fund_cache:
            return fund_cache[key]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(
                code, as_of=decision_date, live_fallback=False
            )
            metrics = resolved.get("metrics") if resolved.get("ok") else None
            if not metrics and fundamentals_by_code:
                metrics = fundamentals_by_code.get(code)
            fund_cache[key] = metrics
            return metrics
        except Exception:
            metrics = (fundamentals_by_code or {}).get(code)
            fund_cache[key] = metrics
            return metrics

    pearson_series: Dict[str, List[float]] = {f: [] for f in names}
    spearman_series: Dict[str, List[float]] = {f: [] for f in names}
    score_pearson: List[float] = []
    score_spearman: List[float] = []
    day_meta: List[dict] = []

    last_i = n - horizon_days - 1
    for i in range(min_history - 1, last_i + 1):
        decision_date = dates[i]
        buckets: Dict[str, Tuple[List[float], List[float]]] = {
            f: ([], []) for f in names
        }
        score_xs: List[float] = []
        score_ys: List[float] = []
        for code, dm in date_maps.items():
            start = max(0, i - max_window + 1)
            window = [dm[d] for d in dates[start : i + 1] if d in dm]
            if len(window) < 2:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            fund = _fund_for(code, decision_date)
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                fundamentals=fund,
            )
            if not item or item.get("hard_reject"):
                continue
            fr = _forward_return_pct(dm, dates, i, horizon_days)
            if fr is None:
                continue
            score_xs.append(float(item.get("score") or 0))
            score_ys.append(fr)
            subs = item.get("sub_scores") or {}
            for f in names:
                if f not in subs or subs[f] is None:
                    continue
                buckets[f][0].append(float(subs[f]))
                buckets[f][1].append(fr)

        if len(score_xs) >= min_names:
            p = _pearson(score_xs, score_ys)
            s = _spearman(score_xs, score_ys)
            if p is not None:
                score_pearson.append(p)
            if s is not None:
                score_spearman.append(s)

        day_row = {"date": decision_date, "n_names": len(score_xs), "factors": {}}
        for f in names:
            xs, ys = buckets[f]
            if len(xs) < min_names:
                continue
            p = _pearson(xs, ys)
            s = _spearman(xs, ys)
            if p is not None:
                pearson_series[f].append(p)
            if s is not None:
                spearman_series[f].append(s)
            if p is not None or s is not None:
                day_row["factors"][f] = {
                    "pearson": round(p, 4) if p is not None else None,
                    "spearman": round(s, 4) if s is not None else None,
                    "n": len(xs),
                }
        if day_row["factors"] or len(score_xs) >= min_names:
            day_meta.append(day_row)

    factors_out: List[Dict[str, Any]] = []
    for f in names:
        pear = _agg_ic_series(pearson_series[f])
        spear = _agg_ic_series(spearman_series[f])
        reason = None
        if pear["ic_mean"] is None:
            reason = "sparse" if len(pearson_series[f]) < 3 else "other"
        factors_out.append(
            {
                "factor": f,
                "pearson": pear,
                "spearman": spear,
                "ic": pear.get("ic_mean"),
                "icir": pear.get("icir"),
                "sample_count": pear.get("day_count") or 0,
                "exclusion_reason": reason,
            }
        )

    score_block = {
        "pearson": _agg_ic_series(score_pearson),
        "spearman": _agg_ic_series(score_spearman),
    }
    ok = any((r.get("pearson") or {}).get("ic_mean") is not None for r in factors_out)
    return {
        "success": True,
        "ok": ok,
        "mode": "factor_cross_section",
        "horizon_days": horizon_days,
        "min_names": min_names,
        "stock_count": len(stock_bars),
        "day_count": len(day_meta),
        "pit_fundamentals": bool(pit_fundamentals),
        "calendar": calendar_tag,
        "score_ic": score_block,
        "factors": factors_out,
        "daily_tail": day_meta[-40:],
        "note": (
            "池内逐因子日频截面 IC（Pearson + Spearman）。"
            "与单票时序 IC 不同；不等于 Fama–MacBeth；不写生产权重。"
        ),
    }
