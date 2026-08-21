"""因子 IC/IR 报告（P7.2 / P50 全因子 + fundamentals）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.signal.factor_corr import pearson_with_reason
from core.signal.factor_registry import registered_factor_names
from core.signal.scorer import score_bars


def _forward_return(bars: List[dict], idx: int, horizon: int) -> Optional[float]:
    if idx + horizon >= len(bars):
        return None
    entry = bars[idx].get("close")
    exit_p = bars[idx + horizon].get("close")
    if not entry:
        return None
    return (exit_p / entry - 1.0) * 100.0


def _resolve_fund_for_day(
    stock_code: Optional[str],
    decision_date: str,
    *,
    pit_fundamentals: bool,
    fundamentals_fallback: Optional[dict],
    cache: Dict[str, Optional[dict]],
) -> Optional[dict]:
    """E2：按决策日 PIT 解析财务；缺史不静默回退最新快照（除非显式关闭 PIT）。"""
    if not pit_fundamentals:
        return fundamentals_fallback
    if not stock_code:
        return None
    if decision_date in cache:
        return cache[decision_date]
    try:
        from core.fundamentals_pit import resolve_fundamentals_for_score

        resolved = resolve_fundamentals_for_score(
            stock_code,
            as_of=decision_date,
            live_fallback=False,
        )
        metrics = resolved.get("metrics") if resolved.get("ok") else None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in factor_report.py", exc_info=True)
        metrics = None
    cache[decision_date] = metrics
    return metrics


def compute_factor_ic_report(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """对每个 sub_score 与 forward return 算 Pearson IC（研究用）。

    E2：默认按 bar 日期走财务 PIT；`pit_fundamentals=False` 时沿用传入快照。
    """
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))

    factor_names = registered_factor_names()
    factor_keys = tuple(factor_names) + ("score",)
    series: Dict[str, List[float]] = {k: [] for k in factor_keys}
    factor_forwards: Dict[str, List[float]] = {k: [] for k in factor_keys}

    fund_cache: Dict[str, Optional[dict]] = {}
    pit_hits = 0
    pit_miss = 0

    n = len(bars or [])
    for i in range(min_history - 1, n - horizon_days):
        start = max(0, i - max_window + 1)
        window = bars[start : i + 1]
        quote = {
            "change_raw": 0.0,
            "price_raw": bars[i]["close"],
        }
        if i >= 1:
            c0 = bars[i - 1]["close"]
            c1 = bars[i]["close"]
            if c0:
                quote["change_raw"] = round((c1 / c0 - 1.0) * 100.0, 4)

        idx_slice = index_bars[start : i + 1] if index_bars else None
        decision_date = str((bars[i] or {}).get("date") or "")[:10]
        day_fund = _resolve_fund_for_day(
            stock_code,
            decision_date,
            pit_fundamentals=pit_fundamentals,
            fundamentals_fallback=fundamentals,
            cache=fund_cache,
        )
        if pit_fundamentals:
            if day_fund:
                pit_hits += 1
            else:
                pit_miss += 1

        scored = score_bars(
            window,
            horizon_days=horizon_days,
            quote=quote,
            index_bars=idx_slice,
            fundamentals=day_fund,
        )
        if scored.get("hard_reject"):
            continue

        fr = _forward_return(bars, i, horizon_days)
        if fr is None:
            continue

        sub = scored.get("sub_scores") or {}
        score_val = float(scored.get("score") or 0)
        series["score"].append(score_val)
        factor_forwards["score"].append(fr)
        for key in factor_names:
            val = sub.get(key)
            if val is None:
                continue
            series[key].append(float(val))
            factor_forwards[key].append(fr)

    rows = []
    exclusion_reasons: Dict[str, str] = {}
    for key in factor_keys:
        xs = series[key]
        ys = factor_forwards[key]
        ic, reason = pearson_with_reason(xs, ys)
        row = {
            "factor": key,
            "sample_count": len(xs),
            "ic": round(ic, 4) if ic is not None else None,
        }
        if reason:
            row["exclusion_reason"] = reason
            exclusion_reasons[key] = reason
        rows.append(row)

    used_any = any(factor_forwards[k] for k in factor_keys)
    note = "Pearson IC 仅供研究；样本少时不具统计意义。"
    if pit_fundamentals:
        note += " value/quality/growth 等按决策日财务 PIT（缺史跳过该日基本面因子，不静默用最新快照）。"
    elif fundamentals:
        note += " value/quality 使用传入快照估值（非 point-in-time）。"

    return {
        "success": True,
        "horizon_days": horizon_days,
        "sample_count": len(factor_forwards["score"]),
        "factor_count": len(factor_names),
        "fundamentals_used": used_any and (pit_fundamentals or fundamentals is not None),
        "pit_fundamentals": bool(pit_fundamentals),
        "pit_resolve_hits": pit_hits,
        "pit_resolve_miss": pit_miss,
        "stock_code": stock_code,
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "note": note,
    }
