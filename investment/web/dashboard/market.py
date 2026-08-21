"""仪表盘：市场概览与盘前上下文。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

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
        from core.data_service import batch_get_quotes

        quotes = batch_get_quotes([s[0] for s in specs]) or {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        quotes = {}

    for symbol, name, code in specs:
        q = quotes.get(symbol) if isinstance(quotes, dict) else None
        if not isinstance(q, dict):
            # 个别失败时单拉一次（仍远快于 AkShare 日线）
            try:
                from core.data_service import get_quote

                q = get_quote(symbol) or {}
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
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


def _build_market_overview() -> Dict[str, Any]:
    """市场监控：指数 / 涨跌家数 / 成交额 / 涨停跌停。"""
    indices = _index_quotes_for_overview()

    # Market breadth from paper holdings (disk)
    paper = _load_raw_paper()
    holdings = _holdings_from_paper(paper)
    up_count = 0
    down_count = 0
    flat_count = 0
    total_turnover = 0.0

    for h in holdings:
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
            change = float(change or 0)
        except (TypeError, ValueError):
            change = 0.0
        if change > 0.01:
            up_count += 1
        elif change < -0.01:
            down_count += 1
        else:
            flat_count += 1
        try:
            total_turnover += _holding_market_value(h)
        except (TypeError, ValueError):
            pass

    limit_up = 0
    limit_down = 0
    for h in holdings:
        try:
            ch = float(h.get("change_pct") if h.get("change_pct") is not None else (h.get("pnl_pct") or 0))
        except (TypeError, ValueError):
            ch = 0.0
        if ch >= 9.5:
            limit_up += 1
        elif ch <= -9.5:
            limit_down += 1

    return {
        "ok": True,
        "indices": indices,
        "breadth": {
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_holdings": len(holdings),
        },
        "turnover": {
            "total_value": round(total_turnover, 2),
            "holding_count": len(holdings),
        },
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


def _build_market_context_dashboard() -> Dict[str, Any]:
    """盘前市场上下文：macro / 情绪 / 公告 prior 快照 + 新鲜度。"""
    from core.market_context import build_market_priors, load_market_context
    from core.market_context_merge import prune_macro_errors

    ctx = load_market_context(use_cache=False)
    macro = ctx.get("macro") or {}
    sentiment = ctx.get("market_sentiment") or {}
    announcement = ctx.get("announcement") or {}
    reg = announcement.get("regulatory") or {}
    priors = build_market_priors(ctx)
    macro_hist = None
    try:
        from skills.macro.history import load_macro_history_index

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
        regime_snapshot = {
            "regime": regime_info.get("regime"),
            "index_return_pct": regime_info.get("index_return_pct"),
            "score_penalty": regime_info.get("score_penalty"),
            "reason": regime_info.get("reason"),
            "benchmark": idx_pack.get("benchmark"),
            "macro_overlay_applied": bool(overlay.get("applied")),
            "macro_overlay_deferred": bool(overlay.get("deferred_to_cross_market_prior")),
            "macro_overlay_tech_1d_pct": overlay.get("tech_1d_pct"),
        }
    except Exception:
        logger.debug("dashboard regime snapshot skipped", exc_info=True)
    return {
        "ok": True,
        "data_ready": data_ready,
        "macro_degraded": macro_degraded,
        "macro_errors": macro_errors,
        "freshness": fresh,
        "macro": {
            "overseas_tech_1d_pct": macro.get("overseas_tech_1d_pct"),
            "a50_1d_pct": macro.get("a50_1d_pct"),
            "lead_lag_expected_gap_pct": macro.get("lead_lag_expected_gap_pct"),
            "liquidity_stress_score": macro.get("liquidity_stress_score"),
            "series": macro.get("series"),
            "errors": macro.get("errors"),
        },
        "market_sentiment": {
            "sentiment_cycle_score": sentiment.get("sentiment_cycle_score"),
            "limit_up_open_premium_pct": sentiment.get("limit_up_open_premium_pct"),
            "broken_limit_rate": sentiment.get("broken_limit_rate"),
            "limit_up_count": sentiment.get("limit_up_count"),
            "data_source": sentiment.get("data_source"),
            "market_breadth": sentiment.get("market_breadth"),
        },
        "announcement": {
            "regulatory": reg,
            "ipo": (announcement.get("ipo") or {}),
            "concept_index_size": len(announcement.get("concept_index") or {}),
            "penalty_concepts": list(reg.get("penalty_concepts") or [])[:8],
            "penalty_codes": list(reg.get("penalty_codes") or [])[:8],
        },
        "prior_flags": {
            "cross_market": bool((priors.get("cross_market_prior") or {}).get("active")),
            "market_sentiment": bool((priors.get("market_sentiment_prior") or {}).get("active")),
            "regulatory": bool((priors.get("regulatory_prior") or {}).get("active")),
            "ipo_drain": bool((priors.get("ipo_drain_prior") or {}).get("active")),
            "any_active": bool(priors.get("market_prior_active")),
        },
        "prior_warnings": list(priors.get("market_prior_warnings") or [])[:6],
        "regime": regime_snapshot,
        "macro_sparkline": macro_sparkline,
        "macro_history": [
            {
                "date": str(r.get("date") or "")[:10],
                "overseas_tech_1d_pct": r.get("overseas_tech_1d_pct"),
                "a50_1d_pct": r.get("a50_1d_pct"),
            }
            for r in hist_rows[-30:]
            if r.get("overseas_tech_1d_pct") is not None or r.get("a50_1d_pct") is not None
        ],
        "macro_history_rows": len(hist_rows),
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "note": "非 PIT；盘前 prior 上下文。过期请 POST schedule pre_market_ingest。",
    }

