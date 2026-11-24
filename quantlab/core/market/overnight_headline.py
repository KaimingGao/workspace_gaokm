"""昨夜→今早一句话摘要（Prior Desk / Bloomberg 式 headline，非 PIT）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _fmt_pct(v: Any, *, digits: int = 1) -> Optional[str]:
    try:
        if v is None:
            return None
        n = float(v)
    except (TypeError, ValueError):
        return None
    return f"{n:+.{digits}f}%"


def _regime_lab(code: Any) -> str:
    m = {
        "bear": "熊",
        "weak": "弱",
        "neutral": "中",
        "strong": "强",
        "bull": "牛",
    }
    return m.get(str(code or "").lower(), "")


def _sentiment_band(score: Any) -> str:
    try:
        s = float(score) if score is not None else None
    except (TypeError, ValueError):
        s = None
    if s is None:
        return ""
    if s < 40:
        return "偏弱"
    if s < 55:
        return "中性"
    if s < 70:
        return "偏热"
    return "过热"


def build_overnight_headline(
    *,
    macro: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    regime: Optional[dict] = None,
    session_clock: Optional[dict] = None,
    northbound: Optional[dict] = None,
) -> Dict[str, Any]:
    """由已有盘前字段拼一句可读摘要。"""
    m = macro if isinstance(macro, dict) else {}
    s = sentiment if isinstance(sentiment, dict) else {}
    r = regime if isinstance(regime, dict) else {}
    clock = session_clock if isinstance(session_clock, dict) else {}
    nb = northbound if isinstance(northbound, dict) else {}

    parts: List[str] = []
    tone = "flat"

    clock_lab = str(clock.get("label") or "").strip()
    if clock_lab:
        # 会话时钟已在 pill；headline 用更短前缀
        a = (clock.get("a_share") or {}).get("code")
        us = (clock.get("us_rth") or {}).get("code")
        if us == "closed" and a == "pre_open":
            parts.append("盘前")
        elif us == "open":
            parts.append("隔夜盘中")
        elif a in ("morning", "afternoon", "auction"):
            parts.append("盘中")
        else:
            parts.append(clock_lab.split("·")[0].strip() or "盘前")

    tech = _fmt_pct(m.get("overseas_tech_1d_pct"))
    if tech:
        parts.append(f"科技{tech}")
        try:
            if float(m.get("overseas_tech_1d_pct")) <= -2:
                tone = "down"
            elif float(m.get("overseas_tech_1d_pct")) >= 2:
                tone = "up"
        except (TypeError, ValueError):
            pass
    stale = list(m.get("stale_tech_keys") or [])
    if stale:
        parts.append("科技滞后")
        tone = "warn"

    a50 = _fmt_pct(m.get("a50_1d_pct"))
    if a50:
        parts.append(f"A50{a50}")

    series = m.get("series") if isinstance(m.get("series"), dict) else {}
    nq = m.get("nq_fut_1d_pct")
    if nq is None:
        nq_slot = series.get("nq_fut") if isinstance(series.get("nq_fut"), dict) else {}
        nq = nq_slot.get("change_1d_pct")
    nq_txt = _fmt_pct(nq)
    if nq_txt:
        parts.append(f"NQ{nq_txt}")

    lu = s.get("limit_up_count")
    band = _sentiment_band(s.get("sentiment_cycle_score"))
    if lu is not None or band:
        try:
            lu_i = int(lu) if lu is not None else None
        except (TypeError, ValueError):
            lu_i = None
        if lu_i is not None and band:
            parts.append(f"情绪{band}涨停{lu_i}")
        elif lu_i is not None:
            parts.append(f"涨停{lu_i}")
        elif band:
            parts.append(f"情绪{band}")
        if band in ("过热", "偏热") and tone == "flat":
            tone = "up"
        if band == "偏弱" and tone == "flat":
            tone = "down"

    reg_lab = _regime_lab(r.get("regime"))
    if reg_lab:
        parts.append(f"锚{reg_lab}")
        if reg_lab in ("熊", "弱") and tone == "flat":
            tone = "down"
        if reg_lab in ("牛", "强") and tone == "flat":
            tone = "up"

    cnh = series.get("cnh") if isinstance(series.get("cnh"), dict) else {}
    cnh_chg = m.get("cnh_1d_pct")
    if cnh_chg is None:
        cnh_chg = cnh.get("change_1d_pct")
    try:
        if cnh_chg is not None and float(cnh_chg) > 0.15:
            parts.append("CNH贬")
            tone = "warn"
    except (TypeError, ValueError):
        pass

    try:
        nb_net = float(nb["net_inflow_yi"]) if nb.get("net_inflow_yi") is not None else None
    except (TypeError, ValueError):
        nb_net = None
    if nb_net is not None and abs(nb_net) >= 1.0:
        parts.append(f"北向{nb_net:+.0f}亿")
        if nb_net < -20 and tone == "flat":
            tone = "down"
        if nb_net > 20 and tone == "flat":
            tone = "up"

    try:
        liq = float(m.get("liquidity_stress_score") or 0)
    except (TypeError, ValueError):
        liq = 0.0
    if liq > 0:
        parts.append(f"流动性{liq:g}")
        tone = "warn"

    text = " · ".join(parts) if parts else "盘前上下文未齐 · 刷新 ingest"
    return {
        "text": text,
        "tone": tone,
        "parts": parts,
    }
