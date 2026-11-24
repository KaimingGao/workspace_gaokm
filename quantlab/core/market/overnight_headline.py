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


def _pct_tone(v: Any) -> str:
    try:
        if v is None:
            return "flat"
        n = float(v)
    except (TypeError, ValueError):
        return "flat"
    if n > 0.05:
        return "up"
    if n < -0.05:
        return "down"
    return "flat"


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


def _seg(
    *,
    kind: str,
    text: str,
    tone: str = "flat",
    label: Optional[str] = None,
    value: Optional[str] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"kind": kind, "text": text, "tone": tone}
    if label is not None:
        out["label"] = label
    if value is not None:
        out["value"] = value
    return out


def build_overnight_headline(
    *,
    macro: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    regime: Optional[dict] = None,
    session_clock: Optional[dict] = None,
    northbound: Optional[dict] = None,
    tech_catalyst: Optional[dict] = None,
) -> Dict[str, Any]:
    """由已有盘前字段拼一句可读摘要；segments 供 UI 芯片渲染。"""
    m = macro if isinstance(macro, dict) else {}
    s = sentiment if isinstance(sentiment, dict) else {}
    r = regime if isinstance(regime, dict) else {}
    clock = session_clock if isinstance(session_clock, dict) else {}
    nb = northbound if isinstance(northbound, dict) else {}
    cat = tech_catalyst if isinstance(tech_catalyst, dict) else {}

    segments: List[Dict[str, Any]] = []
    tone = "flat"

    clock_lab = str(clock.get("label") or "").strip()
    if clock_lab:
        a = (clock.get("a_share") or {}).get("code")
        us = (clock.get("us_rth") or {}).get("code")
        if us == "closed" and a == "pre_open":
            sess = "盘前"
        elif us == "open":
            sess = "隔夜盘中"
        elif a in ("morning", "afternoon", "auction"):
            sess = "盘中"
        else:
            sess = clock_lab.split("·")[0].strip() or "盘前"
        segments.append(_seg(kind="session", text=sess, tone="muted"))

    tech_raw = m.get("overseas_tech_1d_pct")
    tech = _fmt_pct(tech_raw)
    if tech:
        t_tone = _pct_tone(tech_raw)
        segments.append(
            _seg(kind="pct", text=f"科技{tech}", label="科技", value=tech, tone=t_tone)
        )
        try:
            if float(tech_raw) <= -2:
                tone = "down"
            elif float(tech_raw) >= 2:
                tone = "up"
        except (TypeError, ValueError):
            pass
    stale = list(m.get("stale_tech_keys") or [])
    if stale:
        segments.append(_seg(kind="warn", text="科技滞后", tone="warn"))
        tone = "warn"

    a50_raw = m.get("a50_1d_pct")
    a50 = _fmt_pct(a50_raw)
    if a50:
        segments.append(
            _seg(
                kind="pct",
                text=f"A50{a50}",
                label="A50",
                value=a50,
                tone=_pct_tone(a50_raw),
            )
        )

    series = m.get("series") if isinstance(m.get("series"), dict) else {}
    nq = m.get("nq_fut_1d_pct")
    if nq is None:
        nq_slot = series.get("nq_fut") if isinstance(series.get("nq_fut"), dict) else {}
        nq = nq_slot.get("change_1d_pct")
    nq_txt = _fmt_pct(nq)
    if nq_txt:
        segments.append(
            _seg(
                kind="pct",
                text=f"NQ{nq_txt}",
                label="NQ",
                value=nq_txt,
                tone=_pct_tone(nq),
            )
        )

    lu = s.get("limit_up_count")
    band = _sentiment_band(s.get("sentiment_cycle_score"))
    try:
        lu_i = int(lu) if lu is not None else None
    except (TypeError, ValueError):
        lu_i = None
    if band:
        band_tone = "flat"
        if band in ("过热", "偏热"):
            band_tone = "up"
            if tone == "flat":
                tone = "up"
        elif band == "偏弱":
            band_tone = "down"
            if tone == "flat":
                tone = "down"
        segments.append(
            _seg(kind="sentiment", text=f"情绪{band}", label="情绪", value=band, tone=band_tone)
        )
    if lu_i is not None:
        segments.append(
            _seg(
                kind="limit_up",
                text=f"涨停{lu_i}",
                label="涨停",
                value=str(lu_i),
                tone="flat",
            )
        )

    reg_lab = _regime_lab(r.get("regime"))
    if reg_lab:
        reg_tone = "flat"
        if reg_lab in ("熊", "弱"):
            reg_tone = "down"
            if tone == "flat":
                tone = "down"
        elif reg_lab in ("牛", "强"):
            reg_tone = "up"
            if tone == "flat":
                tone = "up"
        segments.append(
            _seg(kind="regime", text=f"锚{reg_lab}", label="锚", value=reg_lab, tone=reg_tone)
        )

    cnh = series.get("cnh") if isinstance(series.get("cnh"), dict) else {}
    cnh_chg = m.get("cnh_1d_pct")
    if cnh_chg is None:
        cnh_chg = cnh.get("change_1d_pct")
    try:
        if cnh_chg is not None and float(cnh_chg) > 0.15:
            segments.append(_seg(kind="warn", text="CNH贬", tone="warn"))
            tone = "warn"
    except (TypeError, ValueError):
        pass

    try:
        nb_net = float(nb["net_inflow_yi"]) if nb.get("net_inflow_yi") is not None else None
    except (TypeError, ValueError):
        nb_net = None
    if nb_net is not None and abs(nb_net) >= 1.0:
        nb_tone = "flat"
        if nb_net < -20:
            nb_tone = "down"
            if tone == "flat":
                tone = "down"
        elif nb_net > 20:
            nb_tone = "up"
            if tone == "flat":
                tone = "up"
        nb_txt = f"北向{nb_net:+.0f}亿"
        segments.append(
            _seg(
                kind="flow",
                text=nb_txt,
                label="北向",
                value=f"{nb_net:+.0f}亿",
                tone=nb_tone,
            )
        )

    try:
        liq = float(m.get("liquidity_stress_score") or 0)
    except (TypeError, ValueError):
        liq = 0.0
    if liq > 0:
        segments.append(
            _seg(
                kind="warn",
                text=f"流动性{liq:g}",
                label="流动性",
                value=f"{liq:g}",
                tone="warn",
            )
        )
        tone = "warn"

    reason = str(cat.get("reason") or "").strip()
    catalyst_snip = ""
    if reason:
        snip = reason
        for sep in ("。", "；", ";", "，", ","):
            if sep in snip:
                snip = snip.split(sep, 1)[0]
                break
        snip = snip.strip()
        if len(snip) > 28:
            snip = snip[:27].rstrip() + "…"
        if snip:
            catalyst_snip = snip
            segments.append(
                _seg(kind="catalyst", text=f"因·{snip}", label="因", value=snip, tone="muted")
            )

    parts = [str(seg.get("text") or "") for seg in segments if seg.get("text")]
    text = " · ".join(parts) if parts else "盘前上下文未齐 · 刷新 ingest"
    return {
        "text": text,
        "tone": tone,
        "parts": parts,
        "segments": segments,
        "catalyst": catalyst_snip or None,
    }
