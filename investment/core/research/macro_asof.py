"""宏观快照 as-of 视图（研究 / 历史 regime 用；非 PIT）。"""


from typing import Any, Dict, List, Optional


def _norm_date(raw: Any) -> str:
    return str(raw or "")[:10]


def bars_up_to(recent_bars: List[dict], as_of: str) -> List[dict]:
    cutoff = _norm_date(as_of)
    if not cutoff:
        return list(recent_bars or [])
    out = []
    for b in recent_bars or []:
        d = _norm_date(b.get("date"))
        if d and d <= cutoff:
            out.append(b)
    return out


def pct_change_from_bars(bars: List[dict], *, days: int = 1) -> Optional[float]:
    if not bars or len(bars) < days + 1:
        return None
    try:
        c0 = float(bars[-(days + 1)].get("close") or 0)
        c1 = float(bars[-1].get("close") or 0)
    except (TypeError, ValueError):
        return None
    if not c0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 4)


def macro_view_asof(macro: Optional[dict], as_of: str) -> Dict[str, Any]:
    """由 macro 快照 recent_bars 重建 as-of 聚合字段。"""
    if not isinstance(macro, dict) or not as_of:
        return {}
    series = dict(macro.get("series") or {})
    out_series: Dict[str, Any] = {}
    tech_rets: List[float] = []
    for key in ("sox", "ndx", "qqq", "kweb", "a50"):
        raw = series.get(key)
        if not isinstance(raw, dict):
            continue
        recent = list(raw.get("recent_bars") or [])
        if recent:
            trimmed = bars_up_to(recent, as_of)
            chg1 = pct_change_from_bars(trimmed, days=1)
            chg5 = pct_change_from_bars(trimmed, days=min(5, max(len(trimmed) - 1, 1)))
            close = trimmed[-1].get("close") if trimmed else raw.get("close")
            entry = dict(raw)
            entry["change_1d_pct"] = chg1
            entry["change_5d_pct"] = chg5
            entry["close"] = close
            entry["as_of"] = _norm_date(as_of)
            out_series[key] = entry
            if key in ("sox", "ndx", "qqq", "kweb") and chg1 is not None:
                tech_rets.append(float(chg1))
        else:
            out_series[key] = raw
            chg = raw.get("change_1d_pct")
            if key in ("sox", "ndx", "qqq", "kweb") and chg is not None:
                tech_rets.append(float(chg))

    overseas = round(sum(tech_rets) / len(tech_rets), 4) if tech_rets else macro.get(
        "overseas_tech_1d_pct"
    )
    a50_entry = out_series.get("a50") or series.get("a50") or {}
    a50_1d = a50_entry.get("change_1d_pct")

    liquidity_stress = float(macro.get("liquidity_stress_score") or 0.0)
    cnh = out_series.get("cnh") or series.get("cnh") or {}
    cnh_c = cnh.get("change_1d_pct")
    if cnh_c is not None and float(cnh_c) > 0.15:
        liquidity_stress = max(liquidity_stress, 1.0)

    return {
        "as_of": _norm_date(as_of),
        "series": out_series,
        "overseas_tech_1d_pct": overseas,
        "a50_1d_pct": a50_1d,
        "liquidity_stress_score": liquidity_stress,
        "lead_lag_expected_gap_pct": macro.get("lead_lag_expected_gap_pct"),
        "synthetic": True,
    }


def load_macro_view_asof(as_of: str) -> Dict[str, Any]:
    """加载磁盘 macro 快照并切 as-of 视图。"""
    from core.market_context_store import load_macro_snapshot

    snap, _meta = load_macro_snapshot(max_age_hours=168.0)
    if not snap:
        return {}
    return macro_view_asof(snap, as_of)
