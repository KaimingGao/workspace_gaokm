"""仪表盘：市场概览与盘前上下文。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from web.dashboard.paper_helpers import (
    _holding_market_value,
    _holdings_from_paper,
    _load_raw_paper,
)


def _index_quotes_for_overview() -> List[Dict[str, Any]]:
    """仪表盘指数卡：优先腾讯现价（快），失败再退化空卡。经 DataService。"""
    # (腾讯符号, 展示名, 内部 code)
    specs = [
        ("sh000001", "上证指数", "上证"),
        ("sz399001", "深证成指", "深证"),
        ("sz399006", "创业板指", "创业板"),
    ]
    out: List[Dict[str, Any]] = []
    quotes: Dict[str, Any] = {}
    try:
        from core.data.facade import batch_get_quotes

        quotes = batch_get_quotes([s[0] for s in specs]) or {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        quotes = {}

    for symbol, name, code in specs:
        q = quotes.get(symbol) if isinstance(quotes, dict) else None
        if not isinstance(q, dict):
            # 个别失败时单拉一次（仍远快于 AkShare 日线）
            try:
                from core.data.facade import get_quote

                q = get_quote(symbol) or {}
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                q = {}
        close = None
        change_pct = None
        volume = 0.0
        if isinstance(q, dict) and q.get("success") is not False:
            raw = q.get("price_raw")
            if raw is None and q.get("price") is not None:
                try:
                    raw = float(str(q.get("price")).replace("元", "").replace(",", ""))
                except (TypeError, ValueError):
                    raw = None
            try:
                close = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                close = None
            ch = q.get("change_raw")
            if ch is None and isinstance(q.get("change"), str) and q["change"].endswith("%"):
                try:
                    ch = float(q["change"].rstrip("%").replace("+", ""))
                except (TypeError, ValueError):
                    ch = None
            try:
                change_pct = float(ch) if ch is not None else None
            except (TypeError, ValueError):
                change_pct = None
            try:
                volume = float(q.get("volume_raw") or 0)
            except (TypeError, ValueError):
                volume = 0.0
            if q.get("stock_name"):
                name = str(q.get("stock_name"))
        out.append(
            {
                "name": name,
                "code": code,
                "close": round(close, 2) if close is not None else None,
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "volume": volume,
            }
        )
    return out


def _holding_day_change_pct(h: Dict[str, Any]) -> float:
    """持仓日涨跌%：优先 change_pct，其次 pnl_pct，再退成本盈亏。"""
    change = h.get("change_pct")
    if change is None:
        change = h.get("pnl_pct")
    if change is None:
        try:
            mv = float(h.get("market_value") or 0)
            shares = float(h.get("shares") or 0)
            cost = float(h.get("cost") or 0)
            cost_v = shares * cost
            change = ((mv / cost_v - 1) * 100) if cost_v > 0 and mv else 0
        except (TypeError, ValueError):
            change = 0
    try:
        return float(change or 0)
    except (TypeError, ValueError):
        return 0.0


def _build_market_overview() -> Dict[str, Any]:
    """市场监控：指数 / 持仓涨跌家数 / 持仓市值 / 涨停跌停。

    持仓市值与涨跌家数与交易执行页同源（``mark_to_market``）。
    落盘 holdings 常无 ``market_value`` / ``change_pct``；只读成本会把「持仓市值」
    错成成本市值、涨跌家数错成全平。盯市失败时再回退成本口径。
    """
    indices = _index_quotes_for_overview()

    paper = _load_raw_paper()
    raw_holdings = _holdings_from_paper(paper)
    live_summary: Optional[Dict[str, Any]] = None
    try:
        from core.paper import mark_to_market

        if paper:
            live_summary = mark_to_market(paper)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        live_summary = None

    if isinstance(live_summary, dict):
        holdings = [
            h for h in (live_summary.get("holdings") or []) if isinstance(h, dict)
        ]
    else:
        holdings = raw_holdings

    up_count = 0
    down_count = 0
    flat_count = 0
    limit_up = 0
    limit_down = 0
    holdings_mv = 0.0
    stock_value = None
    if isinstance(live_summary, dict) and live_summary.get("stock_value") is not None:
        try:
            stock_value = float(live_summary["stock_value"])
        except (TypeError, ValueError):
            stock_value = None

    for h in holdings:
        change = _holding_day_change_pct(h)
        if change > 0.01:
            up_count += 1
        elif change < -0.01:
            down_count += 1
        else:
            flat_count += 1
        if change >= 9.5:
            limit_up += 1
        elif change <= -9.5:
            limit_down += 1
        if stock_value is None:
            try:
                holdings_mv += _holding_market_value(h)
            except (TypeError, ValueError):
                pass

    if stock_value is not None:
        holdings_mv = stock_value

    holding_count = len(holdings)
    if isinstance(live_summary, dict) and live_summary.get("position_count") is not None:
        try:
            holding_count = int(live_summary["position_count"])
        except (TypeError, ValueError):
            holding_count = len(holdings)

    return {
        "ok": True,
        "indices": indices,
        "breadth": {
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_holdings": holding_count,
        },
        # 持仓盯市市值（旧键名 turnover 已废弃，语义曾误为成交额）
        "holdings_mv": {
            "total_value": round(holdings_mv, 2),
            "holding_count": holding_count,
        },
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


_SERIES_LABELS = {
    "sox": "费城半导体",
    "ndx": "纳斯达克",
    "qqq": "QQQ",
    "kweb": "中概互联",
    "a50": "富时A50",
    "nq_fut": "小型纳指",
    "es_fut": "小型标普",
    "wti": "WTI原油",
    "copper": "COMEX铜",
    "qvix": "50ETF波动",
    "cnh": "离岸人民币",
    "us10y": "美债10Y",
}

_SERIES_GROUPS = {
    "sox": "美股科技",
    "ndx": "美股科技",
    "qqq": "美股科技",
    "kweb": "中概",
    "a50": "A 股期货",
    "nq_fut": "外盘期货",
    "es_fut": "外盘期货",
    "wti": "大宗",
    "copper": "大宗",
    "qvix": "波动",
    "cnh": "汇率",
    "us10y": "利率",
}


def _short_source(src: Optional[str]) -> Optional[str]:
    raw = str(src or "").strip()
    if not raw:
        return None
    # vendor:symbol → 短标签
    if ":" in raw:
        left, right = raw.split(":", 1)
        vendor = left.rsplit("_", 1)[-1] if "_" in left else left
        vendor = vendor.replace("index_us_stock_", "").replace("futures_global_spot_", "")
        vendor = vendor.replace("currency_", "").replace("bond_zh_", "")
        return f"{vendor}:{right}"[:36]
    return raw[:36]


def _series_rows(macro: Optional[dict]) -> List[Dict[str, Any]]:
    series = (macro or {}).get("series") if isinstance(macro, dict) else None
    if not isinstance(series, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for key in (
        "sox",
        "ndx",
        "qqq",
        "kweb",
        "nq_fut",
        "es_fut",
        "a50",
        "wti",
        "copper",
        "qvix",
        "cnh",
        "us10y",
    ):
        pack = series.get(key)
        if not isinstance(pack, dict):
            continue
        src = pack.get("source")
        rows.append(
            {
                "key": key,
                "label": _SERIES_LABELS.get(key, key.upper()),
                "group": _SERIES_GROUPS.get(key, "其他"),
                "change_1d_pct": pack.get("change_1d_pct"),
                "change_5d_pct": pack.get("change_5d_pct"),
                "close": pack.get("close"),
                "source": src,
                "source_short": _short_source(src if isinstance(src, str) else None),
                "bars_count": pack.get("bars_count"),
            }
        )
    return rows


def _macro_strip_payload(macro: dict) -> Dict[str, Any]:
    """概览 strip / market-context 用的 macro 精简载荷。"""
    series = macro.get("series") if isinstance(macro.get("series"), dict) else {}
    cnh = series.get("cnh") if isinstance(series.get("cnh"), dict) else {}
    us10y = series.get("us10y") if isinstance(series.get("us10y"), dict) else {}
    nq = series.get("nq_fut") if isinstance(series.get("nq_fut"), dict) else {}
    es = series.get("es_fut") if isinstance(series.get("es_fut"), dict) else {}
    wti = series.get("wti") if isinstance(series.get("wti"), dict) else {}
    copper = series.get("copper") if isinstance(series.get("copper"), dict) else {}
    qvix = series.get("qvix") if isinstance(series.get("qvix"), dict) else {}
    return {
        "overseas_tech_1d_pct": macro.get("overseas_tech_1d_pct"),
        "a50_1d_pct": macro.get("a50_1d_pct"),
        "lead_lag_expected_gap_pct": macro.get("lead_lag_expected_gap_pct"),
        "lead_lag_beta": macro.get("lead_lag_beta"),
        "liquidity_stress_score": macro.get("liquidity_stress_score"),
        # 汇率·美债：概览 strip 直读（series 里也有完整槽）
        "cnh_1d_pct": cnh.get("change_1d_pct"),
        "cnh_close": cnh.get("close"),
        "us10y_1d_pct": us10y.get("change_1d_pct"),
        "us10y_close": us10y.get("close"),
        # 外盘期货当月连续（Wind 式 ES/NQ 条）
        "nq_fut_1d_pct": nq.get("change_1d_pct"),
        "es_fut_1d_pct": es.get("change_1d_pct"),
        # 大宗 + 波动（QVIX 代理美股 VIX）
        "wti_1d_pct": wti.get("change_1d_pct"),
        "copper_1d_pct": copper.get("change_1d_pct"),
        "qvix_close": qvix.get("close"),
        "qvix_1d_pct": qvix.get("change_1d_pct"),
        "series": macro.get("series"),
        "errors": macro.get("errors"),
        "as_of": macro.get("as_of"),
        # 科技成分会话日 + 滞后键（如 NDX 仍停在上周五）
        "tech_session_as_of": macro.get("tech_session_as_of"),
        "stale_tech_keys": list(macro.get("stale_tech_keys") or [])[:8],
    }


def _lead_lag_calib(
    hist_rows: List[dict],
    *,
    beta: float = 0.55,
    tech_today: Optional[float] = None,
    a50_today: Optional[float] = None,
    gap_today: Optional[float] = None,
) -> Dict[str, Any]:
    errs: List[float] = []
    for r in hist_rows[-60:]:
        tech = r.get("overseas_tech_1d_pct")
        a50 = r.get("a50_1d_pct")
        if tech is None or a50 is None:
            continue
        try:
            expected = float(tech) * float(beta)
            errs.append(float(a50) - expected)
        except (TypeError, ValueError):
            continue
    expected_today = None
    try:
        if tech_today is not None:
            expected_today = round(float(tech_today) * float(beta), 4)
    except (TypeError, ValueError):
        expected_today = None
    base: Dict[str, Any] = {
        "n": len(errs),
        "beta": beta,
        "mae": None,
        "mean_error": None,
        "hit_rate_1pct": None,
        "sample_thin": len(errs) < 8,
        "expected_today_pct": expected_today,
        "overseas_tech_1d_pct": tech_today,
        "a50_1d_pct": a50_today,
        "expected_gap_pct": gap_today,
        "note": "A50 实现 − 海外科技×β；非 PIT，仅诊断。",
    }
    if not errs:
        base["reading"] = "样本不足 · 待 A50 历史回填"
        return base
    mae = sum(abs(x) for x in errs) / len(errs)
    mean_err = sum(errs) / len(errs)
    hit = sum(1 for x in errs if abs(x) <= 1.0) / len(errs)
    base.update(
        {
            "mae": round(mae, 4),
            "mean_error": round(mean_err, 4),
            "hit_rate_1pct": round(hit, 3),
        }
    )
    if len(errs) < 8:
        base["reading"] = f"样本偏薄（n={len(errs)}）· 仅供参考"
    elif mean_err > 0.5:
        base["reading"] = "近窗 A50 系统性强于海外映射"
    elif mean_err < -0.5:
        base["reading"] = "近窗 A50 系统性弱于海外映射"
    else:
        base["reading"] = "近窗映射大致中性"
    return base


def _trust_rows(ctx: dict, fresh: dict) -> List[Dict[str, Any]]:
    parts = (fresh or {}).get("parts") or {}
    labels = {
        "macro": "跨市场",
        "market_sentiment": "情绪",
        "announcement": "公告",
    }
    rows: List[Dict[str, Any]] = []
    for key, label in labels.items():
        part = parts.get(key) or {}
        snap = (ctx or {}).get(key) if isinstance(ctx, dict) else None
        src = None
        detail = None
        if isinstance(snap, dict):
            src = snap.get("data_source") or snap.get("note")
            src_short = None
            if key == "macro" and isinstance(snap.get("series"), dict):
                sources = [
                    str((snap["series"].get(k) or {}).get("source") or "")
                    for k in ("sox", "ndx", "qqq", "a50")
                    if isinstance(snap["series"].get(k), dict)
                ]
                sources = [s for s in sources if s]
                if sources:
                    uniq = list(dict.fromkeys(sources))
                    src = " · ".join(uniq[:4])
                    shorts = [_short_source(s) for s in uniq[:4]]
                    src_short = " · ".join(s for s in shorts if s)
                    detail = f"{len(uniq)} 源"
            elif key == "market_sentiment":
                sc = snap.get("sentiment_cycle_score")
                lu = snap.get("limit_up_count")
                bits = []
                if sc is not None:
                    bits.append(f"周期 {sc}")
                if lu is not None:
                    bits.append(f"涨停 {lu}")
                detail = " · ".join(bits) if bits else None
                src_short = _short_source(str(src) if src else None) or (
                    str(src)[:24] if src else None
                )
            elif key == "announcement":
                reg = snap.get("regulatory") or {}
                ipo = snap.get("ipo") or {}
                bits = []
                if reg.get("count") is not None:
                    bits.append(f"监管 {reg.get('count')}")
                if ipo.get("ipo_today_count") is not None:
                    bits.append(f"IPO {ipo.get('ipo_today_count')}")
                detail = " · ".join(bits) if bits else None
                src_short = "公告扫描"
            else:
                src_short = _short_source(str(src) if src else None)
        else:
            src_short = None
        ready = bool(isinstance(snap, dict) and (snap.get("success") is not False))
        stale = bool(part.get("stale"))
        warn = bool(part.get("warn"))
        if stale:
            status = "stale"
        elif warn:
            status = "warn"
        elif not ready:
            status = "missing"
        else:
            status = "fresh"
        rows.append(
            {
                "key": key,
                "label": label,
                "as_of": part.get("as_of") or (snap or {}).get("as_of"),
                "age_hours": part.get("age_hours"),
                "warn": warn,
                "stale": stale,
                "status": status,
                "source": (str(src)[:96] if src else None),
                "source_short": src_short,
                "detail": detail,
                "ready": ready,
            }
        )
    return rows


def _sentiment_band(score: Optional[float]) -> Dict[str, Any]:
    try:
        s = float(score) if score is not None else None
    except (TypeError, ValueError):
        s = None
    if s is None:
        return {"band": "—", "tone": "flat", "hint": "无周期分"}
    if s < 40:
        return {"band": "偏弱", "tone": "down", "hint": "<40 情绪收缩"}
    if s < 55:
        return {"band": "中性", "tone": "flat", "hint": "40–55 平衡"}
    if s < 70:
        return {"band": "偏热", "tone": "up", "hint": "55–70 升温"}
    return {"band": "过热", "tone": "up", "hint": "≥70 拥挤"}


def _normalize_event_hit(h: dict) -> Dict[str, Any]:
    code = str(h.get("stock_code") or h.get("code") or "")[-6:]
    title = str(h.get("title") or h.get("summary") or "").strip()
    name = str(h.get("stock_name") or h.get("name") or "").strip()
    return {
        "code": code or None,
        "name": name or None,
        "title": title or None,
        "date": h.get("date") or h.get("announce_date") or h.get("list_date"),
        "tags": [str(x) for x in (h.get("tags") or h.get("concepts") or []) if x][:3],
        "kind": h.get("kind") or h.get("type"),
    }


def _normalize_ipo_event(e: dict) -> Optional[Dict[str, Any]]:
    code = str(e.get("stock_code") or e.get("code") or "")[-6:]
    name = str(e.get("name") or e.get("stock_name") or "").strip()
    title = str(e.get("title") or "").strip()
    if not code and not name and not title:
        return None
    mv = e.get("market_cap_est") or e.get("market_cap") or e.get("est_market_cap")
    try:
        mv_f = float(mv) if mv is not None else None
    except (TypeError, ValueError):
        mv_f = None
    return {
        "code": code or None,
        "name": name or None,
        "title": title or None,
        "list_date": e.get("list_date") or e.get("date"),
        "market_cap_est": mv_f,
        "market_cap_yi": round(mv_f / 1e8, 1) if mv_f else None,
        "concepts": [str(x) for x in (e.get("concepts") or []) if x][:3],
        "issue_amount": e.get("issue_amount"),
    }


def _events_desk(announcement: dict) -> Dict[str, Any]:
    reg = (announcement or {}).get("regulatory") or {}
    ipo = (announcement or {}).get("ipo") or {}
    hits = [_normalize_event_hit(h) for h in (reg.get("hits") or []) if isinstance(h, dict)]
    hits = [h for h in hits if h.get("title") or h.get("code")][:10]
    ipo_events = []
    for e in ipo.get("ipo_events") or []:
        if not isinstance(e, dict):
            continue
        norm = _normalize_ipo_event(e)
        if norm:
            ipo_events.append(norm)
        if len(ipo_events) >= 10:
            break
    drain = dict(ipo.get("drain_ratios_by_concept") or {})
    drain_top = []
    for c, ratio in drain.items():
        try:
            r = float(ratio) if ratio is not None else None
        except (TypeError, ValueError):
            continue
        if r is None:
            continue
        drain_top.append({"concept": str(c), "ratio": round(r, 2)})
    drain_top.sort(key=lambda x: x["ratio"], reverse=True)
    return {
        "regulatory_hits": hits,
        "ipo_events": ipo_events,
        "penalty_concepts": list(reg.get("penalty_concepts") or [])[:10],
        "penalty_codes": [str(x)[-6:] for x in (reg.get("penalty_codes") or []) if x][:10],
        "concept_tags": list(reg.get("concept_tags") or [])[:8],
        "summary": {
            "reg_count": reg.get("count") if reg.get("count") is not None else len(hits),
            "reg_active": bool(reg.get("active")),
            "ipo_today": ipo.get("ipo_today_count"),
            "extreme_ipo_day": bool(ipo.get("extreme_ipo_day")),
            "drain_ratio": ipo.get("liquidity_drain_ratio")
            or ipo.get("liquidity_drain_ratio_proxy"),
            "max_ipo_yi": (
                round(float(ipo["max_ipo_market_cap"]) / 1e8, 1)
                if ipo.get("max_ipo_market_cap") is not None
                else None
            ),
        },
        "drain_top": drain_top[:8],
    }


def _exposure_desk(announcement: dict) -> Dict[str, Any]:
    """观察池 / 纸面持仓相对监管·IPO 概念的只读暴露。"""
    code_concepts = {}
    if isinstance(announcement, dict):
        code_concepts = dict(announcement.get("concept_index") or {})
        if not code_concepts:
            code_concepts = dict((announcement.get("regulatory") or {}).get("code_concepts") or {})
    reg = (announcement or {}).get("regulatory") or {}
    ipo = (announcement or {}).get("ipo") or {}
    hot_concepts = set(reg.get("penalty_concepts") or []) | set(reg.get("concept_tags") or [])
    drain = dict(ipo.get("drain_ratios_by_concept") or {})
    for c, ratio in drain.items():
        try:
            if ratio is not None and float(ratio) >= 3.0:
                hot_concepts.add(str(c))
        except (TypeError, ValueError):
            continue
    watching_codes: List[str] = []
    watching_names: Dict[str, str] = {}
    try:
        from core.watching.repository import read_watching

        w = read_watching()
        watching_codes = [str(c)[-6:] for c in (w.get("watchlist") or []) if c]
        names = w.get("watchlist_names") or []
        for i, c in enumerate(watching_codes):
            if i < len(names) and names[i]:
                watching_names[c] = str(names[i])
    except Exception:
        logger.debug("prior desk watching load skipped", exc_info=True)
    holding_codes: List[str] = []
    holding_names: Dict[str, str] = {}
    try:
        paper = _load_raw_paper()
        for h in _holdings_from_paper(paper) or []:
            code = str(h.get("code") or "")[-6:]
            if not code:
                continue
            holding_codes.append(code)
            if h.get("name"):
                holding_names[code] = str(h.get("name"))
    except Exception:
        logger.debug("prior desk paper load skipped", exc_info=True)

    penalty_code_set = {str(x)[-6:] for x in (reg.get("penalty_codes") or []) if x}

    def _hits(codes: List[str], names: Dict[str, str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for code in codes:
            concepts = [str(x) for x in (code_concepts.get(code) or []) if x]
            hit = [c for c in concepts if c in hot_concepts]
            via_code = code in penalty_code_set
            if not hit and via_code:
                hit = ["监管代码命中"]
            if not hit:
                continue
            out.append(
                {
                    "code": code,
                    "name": names.get(code) or code,
                    "concepts": hit[:4],
                    "via_code": via_code,
                }
            )
            if len(out) >= 12:
                break
        return out

    watching_hits = _hits(watching_codes, watching_names)
    holding_hits = _hits(holding_codes, holding_names)
    concept_chips = []
    for c in sorted(hot_concepts)[:16]:
        chip = {"concept": c, "kind": "reg"}
        if c in drain:
            try:
                chip["drain"] = round(float(drain[c]), 2)
                chip["kind"] = "ipo"
            except (TypeError, ValueError):
                pass
        concept_chips.append(chip)
    return {
        "hot_concepts": sorted(hot_concepts)[:12],
        "concept_chips": concept_chips,
        "watching_n": len(watching_codes),
        "holding_n": len(holding_codes),
        "watching_hits": watching_hits,
        "holding_hits": holding_hits,
        "watching_hit_n": len(watching_hits),
        "holding_hit_n": len(holding_hits),
        "note": "只读暴露：概念/代码与观察池、纸面持仓交集；不改仓。",
    }


def _regime_limits_desk(current: Optional[str], apply_scale: bool) -> List[Dict[str, Any]]:
    # 与 Web Regime 看板一致的仓位缩放心智（弱/熊压仓）
    table = [
        ("bear", "熊", 0.8, "防守 · 压单票/行业"),
        ("weak", "弱", 0.8, "防守 · 压单票/行业"),
        ("neutral", "中", 0.9, "中性缩放"),
        ("strong", "强", 1.0, "趋势环境"),
        ("bull", "牛", 1.0, "趋势环境"),
    ]
    cur = str(current or "").lower()
    rows = []
    for key, lab, scale, note in table:
        rows.append(
            {
                "regime": key,
                "label": lab,
                "position_scale": scale if apply_scale else 1.0,
                "note": note if apply_scale else "缩放关闭",
                "current": key == cur,
            }
        )
    return rows


def _concept_heatmap_desk(concept_flow: Optional[dict]) -> Dict[str, Any]:
    flow = concept_flow if isinstance(concept_flow, dict) else {}
    rows = list(flow.get("rows") or [])[:24]
    nets = [abs(float(r.get("net_yi") or 0)) for r in rows if r.get("net_yi") is not None]
    max_abs = max(nets) if nets else 1.0
    cells = []
    for r in rows:
        net = r.get("net_yi")
        try:
            net_f = float(net) if net is not None else None
        except (TypeError, ValueError):
            net_f = None
        intensity = abs(net_f) / max_abs if net_f is not None and max_abs > 0 else 0.0
        cells.append(
            {
                "concept": r.get("concept"),
                "net_yi": net_f,
                "chg_pct": r.get("chg_pct"),
                "side": "in" if (net_f or 0) >= 0 else "out",
                "intensity": round(min(1.0, intensity), 3),
            }
        )
    return {
        "as_of": flow.get("as_of"),
        "cells": cells,
        "note": flow.get("note") or "概念净流入热力",
    }


def _build_prior_desk(
    *,
    ctx: dict,
    fresh: dict,
    macro: dict,
    sentiment: dict,
    announcement: dict,
    regime: dict,
    hist_rows: List[dict],
    concept_flow: Optional[dict] = None,
    northbound: Optional[dict] = None,
    macro_calendar: Optional[dict] = None,
) -> Dict[str, Any]:
    beta = 0.55
    try:
        beta = float(macro.get("lead_lag_beta") or 0.55)
    except (TypeError, ValueError):
        beta = 0.55
    band = _sentiment_band(sentiment.get("sentiment_cycle_score"))
    nb = northbound if isinstance(northbound, dict) else {}
    cal = macro_calendar if isinstance(macro_calendar, dict) else {}
    return {
        "trust": _trust_rows(ctx, fresh),
        "series_rows": _series_rows(macro),
        "lead_lag": _lead_lag_calib(
            hist_rows,
            beta=beta,
            tech_today=macro.get("overseas_tech_1d_pct"),
            a50_today=macro.get("a50_1d_pct"),
            gap_today=macro.get("lead_lag_expected_gap_pct"),
        ),
        "sentiment_desk": {
            "cycle_score": sentiment.get("sentiment_cycle_score"),
            "band": band.get("band"),
            "band_tone": band.get("tone"),
            "band_hint": band.get("hint"),
            "limit_up_count": sentiment.get("limit_up_count"),
            "prev_limit_count": sentiment.get("prev_limit_count"),
            "broken_limit_count": sentiment.get("broken_limit_count"),
            "broken_limit_rate": sentiment.get("broken_limit_rate"),
            "limit_up_open_premium_pct": sentiment.get("limit_up_open_premium_pct"),
            "avg_board_height": sentiment.get("avg_board_height"),
            "data_source": sentiment.get("data_source"),
            "as_of": sentiment.get("as_of"),
        },
        "events": _events_desk(announcement if isinstance(announcement, dict) else {}),
        "exposure": _exposure_desk(announcement if isinstance(announcement, dict) else {}),
        "concept_heatmap": _concept_heatmap_desk(concept_flow),
        "northbound": {
            "net_inflow_yi": nb.get("net_inflow_yi"),
            "status": nb.get("status"),
            "as_of": nb.get("as_of"),
            "unit": nb.get("unit") or "亿元",
        },
        "calendar": {
            "events": list(cal.get("events") or [])[:6],
            "as_of": cal.get("as_of"),
            "note": cal.get("note"),
        },
        "regime_limits": _regime_limits_desk(
            (regime or {}).get("regime"),
            (regime or {}).get("apply_position_scale") is not False,
        ),
        "regime_meta": {
            "regime": (regime or {}).get("regime"),
            "index_return_pct": (regime or {}).get("index_return_pct"),
            "volatility_pct": (regime or {}).get("volatility_pct"),
            "score_penalty": (regime or {}).get("score_penalty"),
            "benchmark": (regime or {}).get("benchmark"),
            "window_days": (regime or {}).get("window_days"),
            "overlay_deferred": bool((regime or {}).get("macro_overlay_deferred")),
            "overlay_tech_1d_pct": (regime or {}).get("macro_overlay_tech_1d_pct"),
            "apply_position_scale": (regime or {}).get("apply_position_scale") is not False,
            "reason": (regime or {}).get("reason"),
        },
        "boundary": {
            "pit": False,
            "affects_yhat": False,
            "wired_to_rank_lots": False,
            "note": "非 PIT · 不进 ŷ · 未接调仓；仅盘前人看。",
        },
    }


def _build_market_context_dashboard() -> Dict[str, Any]:
    """盘前市场上下文：macro / 情绪 / 公告 prior 快照 + 新鲜度。"""
    from core.market.context import build_market_priors, load_market_context
    from core.market.context_merge import prune_macro_errors

    ctx = load_market_context(use_cache=False)
    macro = ctx.get("macro") or {}
    sentiment = ctx.get("market_sentiment") or {}
    announcement = ctx.get("announcement") or {}
    northbound = ctx.get("northbound") or {}
    concept_flow = ctx.get("concept_flow") or {}
    macro_calendar = ctx.get("macro_calendar") or {}
    reg = announcement.get("regulatory") or {}
    priors = build_market_priors(ctx)
    macro_hist = None
    try:
        from adapters.macro.history import load_macro_history_index

        macro_hist = load_macro_history_index()
    except Exception:
        logger.exception('unexpected error in _build_market_context_dashboard')
        macro_hist = None
    hist_rows = (macro_hist or {}).get("rows") or []
    macro_sparkline = [
        float(r["overseas_tech_1d_pct"])
        for r in hist_rows[-14:]
        if r.get("overseas_tech_1d_pct") is not None
    ]
    macro_errors = list(macro.get("errors") or [])[:8]
    macro_series = (macro or {}).get("series") if isinstance(macro, dict) else None
    macro_errors = prune_macro_errors(macro_errors, macro_series)[:8]
    macro_degraded = bool(
        isinstance(macro, dict)
        and macro.get("success")
        and macro.get("overseas_tech_1d_pct") is None
        and macro_errors
    )
    sentiment_ready = isinstance(sentiment, dict) and (
        sentiment.get("sentiment_cycle_score") is not None
        or sentiment.get("broken_limit_rate") is not None
    )
    announcement_ready = isinstance(announcement, dict) and (
        (announcement.get("regulatory") or {}).get("active")
        or (announcement.get("ipo") or {}).get("ipo_today_count") is not None
    )
    data_ready = bool(
        sentiment_ready
        or announcement_ready
        or (isinstance(macro, dict) and macro.get("overseas_tech_1d_pct") is not None)
    )
    fresh = ctx.get("freshness") or {}
    if isinstance(fresh, dict) and macro_degraded:
        fresh = dict(fresh)
        fresh["macro_degraded"] = True
        fresh["needs_ingest"] = bool(fresh.get("needs_ingest")) or macro_degraded
    regime_snapshot: Dict[str, Any] = {}
    try:
        from core.signal.config import load_signal_config
        from core.signal.live_features import fetch_live_index_bars
        from core.signal.regime import assess_regime

        sig_cfg = load_signal_config()
        idx_pack = fetch_live_index_bars(limit=75, use_cache=True)
        regime_info = assess_regime(
            idx_pack.get("bars") or [],
            sig_cfg.get("regime"),
            macro=macro if isinstance(macro, dict) else None,
        )
        overlay = regime_info.get("macro_overlay") or {}
        blend = regime_info.get("blend") or {}
        rcfg = sig_cfg.get("regime") or {}
        try:
            window_days = int(rcfg.get("weak_trend_days") or 20)
        except (TypeError, ValueError):
            window_days = 20
        regime_snapshot = {
            "regime": regime_info.get("regime"),
            "index_return_pct": regime_info.get("index_return_pct"),
            "volatility_pct": regime_info.get("volatility_pct"),
            "score_penalty": regime_info.get("score_penalty"),
            "reason": regime_info.get("reason"),
            "benchmark": idx_pack.get("benchmark"),
            "window_days": window_days,
            "apply_position_scale": rcfg.get("apply_position_scale") is not False,
            "blend_blended": bool(blend.get("blended")),
            "macro_overlay_applied": bool(overlay.get("applied")),
            "macro_overlay_deferred": bool(overlay.get("deferred_to_cross_market_prior")),
            "macro_overlay_tech_1d_pct": overlay.get("tech_1d_pct"),
        }
    except Exception:
        logger.debug("dashboard regime snapshot skipped", exc_info=True)
    macro_history = [
        {
            "date": str(r.get("date") or "")[:10],
            "overseas_tech_1d_pct": r.get("overseas_tech_1d_pct"),
            "a50_1d_pct": r.get("a50_1d_pct"),
        }
        for r in hist_rows[-30:]
        if r.get("overseas_tech_1d_pct") is not None or r.get("a50_1d_pct") is not None
    ]
    desk = _build_prior_desk(
        ctx=ctx,
        fresh=fresh if isinstance(fresh, dict) else {},
        macro=macro if isinstance(macro, dict) else {},
        sentiment=sentiment if isinstance(sentiment, dict) else {},
        announcement=announcement if isinstance(announcement, dict) else {},
        regime=regime_snapshot,
        hist_rows=hist_rows if isinstance(hist_rows, list) else [],
        concept_flow=concept_flow if isinstance(concept_flow, dict) else {},
        northbound=northbound if isinstance(northbound, dict) else {},
        macro_calendar=macro_calendar if isinstance(macro_calendar, dict) else {},
    )
    macro_payload = _macro_strip_payload(macro if isinstance(macro, dict) else {})
    session_clock: Dict[str, Any] = {}
    try:
        from core.market.session_clock import build_session_clock

        session_clock = build_session_clock(
            tech_session_as_of=macro_payload.get("tech_session_as_of"),
            macro_as_of=macro_payload.get("as_of"),
        )
    except Exception:
        logger.debug("session clock skipped", exc_info=True)
    overnight_headline: Dict[str, Any] = {}
    try:
        from core.market.overnight_headline import build_overnight_headline

        overnight_headline = build_overnight_headline(
            macro=macro_payload,
            sentiment=sentiment if isinstance(sentiment, dict) else {},
            regime=regime_snapshot,
            session_clock=session_clock,
            northbound=northbound if isinstance(northbound, dict) else {},
        )
    except Exception:
        logger.debug("overnight headline skipped", exc_info=True)
    return {
        "ok": True,
        "data_ready": data_ready,
        "macro_degraded": macro_degraded,
        "macro_errors": macro_errors,
        "freshness": fresh,
        "session_clock": session_clock,
        "overnight_headline": overnight_headline,
        "northbound": {
            "net_inflow_yi": (northbound or {}).get("net_inflow_yi"),
            "status": (northbound or {}).get("status"),
            "as_of": (northbound or {}).get("as_of"),
            "unit": (northbound or {}).get("unit") or "亿元",
        },
        "macro_calendar": {
            "events": list((macro_calendar or {}).get("events") or [])[:6],
            "as_of": (macro_calendar or {}).get("as_of"),
            "note": (macro_calendar or {}).get("note"),
        },
        "macro": macro_payload,
        "market_sentiment": {
            "sentiment_cycle_score": sentiment.get("sentiment_cycle_score"),
            "limit_up_open_premium_pct": sentiment.get("limit_up_open_premium_pct"),
            "broken_limit_rate": sentiment.get("broken_limit_rate"),
            "limit_up_count": sentiment.get("limit_up_count"),
            "avg_board_height": sentiment.get("avg_board_height"),
            "prev_limit_count": sentiment.get("prev_limit_count"),
            "broken_limit_count": sentiment.get("broken_limit_count"),
            "data_source": sentiment.get("data_source"),
            "market_breadth": sentiment.get("market_breadth"),
            "as_of": sentiment.get("as_of"),
        },
        "announcement": {
            "regulatory": {
                **dict(reg or {}),
                "hits": list((reg or {}).get("hits") or [])[:8],
                "concept_tags": list((reg or {}).get("concept_tags") or [])[:8],
                "penalty_concepts": list((reg or {}).get("penalty_concepts") or [])[:8],
                "penalty_codes": list((reg or {}).get("penalty_codes") or [])[:8],
            },
            "ipo": {
                **dict((announcement.get("ipo") or {})),
                "ipo_events": list(((announcement.get("ipo") or {}).get("ipo_events") or []))[
                    :8
                ],
            },
            "concept_index_size": len(announcement.get("concept_index") or {}),
            "penalty_concepts": list(reg.get("penalty_concepts") or [])[:8],
            "penalty_codes": list(reg.get("penalty_codes") or [])[:8],
            "as_of": announcement.get("as_of"),
        },
        "prior_flags": {
            "cross_market": bool((priors.get("cross_market_prior") or {}).get("active")),
            "market_sentiment": bool((priors.get("market_sentiment_prior") or {}).get("active")),
            "regulatory": bool((priors.get("regulatory_prior") or {}).get("active")),
            "ipo_drain": bool((priors.get("ipo_drain_prior") or {}).get("active")),
            "any_active": bool(priors.get("market_prior_active")),
        },
        "prior_warnings": list(priors.get("market_prior_warnings") or [])[:8],
        "regime": regime_snapshot,
        "desk": desk,
        "macro_sparkline": macro_sparkline,
        "macro_history": macro_history,
        "macro_history_rows": len(hist_rows),
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "note": "非 PIT；盘前 prior 上下文。过期请 POST schedule pre_market_ingest。",
    }

