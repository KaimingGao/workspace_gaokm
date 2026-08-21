"""市场上下文编排：加载快照 + 构建 prior 包。"""


import logging
from typing import Any, Dict, List, Optional

from core.cross_market_prior import build_cross_market_prior
from core.market_context_merge import (
    freshness_report,
    merge_macro_snapshots,
)
from core.market_context_store import (
    load_announcement_snapshot,
    load_macro_snapshot,
    load_market_sentiment_snapshot,
    save_market_snapshot,
)
from core.market_sentiment_prior import build_market_sentiment_prior
from core.regulatory_prior import build_ipo_drain_prior, build_regulatory_prior

logger = logging.getLogger(__name__)

_CTX_CACHE: Optional[tuple] = None
_CTX_TTL_SEC = 60.0


def load_market_context(*, max_age_hours: float = 36.0, use_cache: bool = True) -> Dict[str, Any]:
    import time

    global _CTX_CACHE
    if use_cache and _CTX_CACHE is not None:
        ts, cached = _CTX_CACHE
        if (time.time() - ts) < _CTX_TTL_SEC:
            return cached

    macro, macro_meta = load_macro_snapshot(max_age_hours=max_age_hours)
    sentiment, sentiment_meta = load_market_sentiment_snapshot(max_age_hours=max_age_hours)
    announcement, ann_meta = load_announcement_snapshot(max_age_hours=max_age_hours)
    out = {
        "macro": macro,
        "market_sentiment": sentiment,
        "announcement": announcement,
        "meta": {
            "macro": macro_meta,
            "market_sentiment": sentiment_meta,
            "announcement": ann_meta,
        },
    }
    if use_cache:
        _CTX_CACHE = (time.time(), out)
    out["freshness"] = freshness_report(out)
    return out


def invalidate_market_context_cache() -> None:
    global _CTX_CACHE
    _CTX_CACHE = None


def ingest_macro(*, lookback: int = 30) -> Dict[str, Any]:
    from core.ports.market import build_macro_snapshot

    prior, _meta = load_macro_snapshot(max_age_hours=168.0)
    snap = build_macro_snapshot(lookback=lookback)
    if prior and (snap.get("errors") or not snap.get("success")):
        snap = merge_macro_snapshots(snap, prior)
    path = save_market_snapshot("macro", snap, data_source="macro_ingest")
    invalidate_market_context_cache()
    return {"ok": bool(snap.get("success")), "path": path, "snapshot": snap}


def ingest_market_sentiment(*, trade_date: Optional[str] = None) -> Dict[str, Any]:
    from core.ports.market import build_market_sentiment_snapshot

    snap = build_market_sentiment_snapshot(trade_date=trade_date)
    path = save_market_snapshot("market_sentiment", snap, data_source="market_sentiment_ingest")
    invalidate_market_context_cache()
    return {"ok": bool(snap.get("success")), "path": path, "snapshot": snap}


def ingest_announcement(*, sector_daily_amount: Optional[float] = None) -> Dict[str, Any]:
    from core.ports.market import build_announcement_snapshot

    snap = build_announcement_snapshot(sector_daily_amount=sector_daily_amount)
    path = save_market_snapshot("announcement", snap, data_source="announcement_ingest")
    invalidate_market_context_cache()
    return {"ok": bool(snap.get("success")), "path": path, "snapshot": snap}


def ingest_pre_market_bundle(*, lookback: int = 30) -> Dict[str, Any]:
    """盘前一键：macro + 情绪 + 公告。"""
    macro_r = ingest_macro(lookback=lookback)
    sent_r = ingest_market_sentiment()
    ann_r = ingest_announcement()
    concept_r = None
    try:
        from core.signal.config import load_signal_config

        pm = (load_signal_config() or {}).get("pre_market") or {}
        if pm.get("concept_graph_on_ingest", True):
            concept_r = refresh_concept_graph(max_concepts=int(pm.get("concept_graph_cap") or 8))
    except Exception:
        logger.debug("concept graph on ingest skipped", exc_info=True)
    minute_r = None
    try:
        from core.signal.config import load_signal_config

        pm = (load_signal_config() or {}).get("pre_market") or {}
        if pm.get("minute_warmup_on_ingest", True):
            from core.schedule_jobs import _minute_warmup_core

            minute_r = _minute_warmup_core(
                cap=int(pm.get("minute_warmup_cap") or 25),
            )
    except Exception:
        logger.debug("minute warmup on ingest skipped", exc_info=True)
    ctx = load_market_context(use_cache=False)
    return {
        "ok": bool(macro_r.get("ok") or sent_r.get("ok") or ann_r.get("ok")),
        "macro": macro_r,
        "market_sentiment": sent_r,
        "announcement": ann_r,
        "concept_graph": concept_r,
        "minute_warmup": minute_r,
        "freshness": ctx.get("freshness"),
    }


def ensure_pre_market_context(
    *,
    lookback: int = 30,
    stale_hours: float = 18.0,
) -> Dict[str, Any]:
    """若快照过期则自动 ingest；供 paper_daily / 刷簿前调用。"""
    try:
        from core.signal.config import load_signal_config

        cfg = (load_signal_config() or {}).get("pre_market") or {}
        if cfg.get("auto_ingest_on_paper_daily") is False:
            ctx = load_market_context(use_cache=True)
            return {"ok": True, "skipped": True, "freshness": ctx.get("freshness")}
        stale_hours = float(cfg.get("stale_hours") or stale_hours)
    except Exception:
        logger.debug("pre_market config read failed", exc_info=True)

    ctx = load_market_context(use_cache=True)
    fresh = ctx.get("freshness") or freshness_report(ctx, stale_hours=stale_hours)
    if fresh.get("needs_ingest"):
        return ingest_pre_market_bundle(lookback=lookback)
    return {"ok": True, "skipped": True, "freshness": fresh}


def build_market_priors(
    context: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    sector: Optional[str] = None,
    rev_limit_up_count: Optional[int] = None,
    rev_consecutive_limit: Optional[int] = None,
) -> Dict[str, Any]:
    ctx = context or {}
    macro = ctx.get("macro")
    sentiment = ctx.get("market_sentiment")
    announcement = ctx.get("announcement")

    cross = build_cross_market_prior(
        macro, config=config, stock_code=stock_code, sector=sector
    )
    msp = build_market_sentiment_prior(
        sentiment,
        config=config,
        stock_code=stock_code,
        rev_limit_up_count=rev_limit_up_count,
        rev_consecutive_limit=rev_consecutive_limit,
    )
    reg = build_regulatory_prior(
        announcement, config=config, stock_code=stock_code, sector=sector
    )
    ipo = build_ipo_drain_prior(
        announcement, config=config, stock_code=stock_code, sector=sector
    )

    warnings = []
    for p in (cross, msp, reg, ipo):
        warnings.extend(list(p.get("warnings") or []))

    return {
        "cross_market_prior": cross,
        "market_sentiment_prior": msp,
        "regulatory_prior": reg,
        "ipo_drain_prior": ipo,
        "market_prior_warnings": warnings,
        "market_prior_active": any(
            bool(p.get("active")) for p in (cross, msp, reg, ipo)
        ),
    }


def summarize_market_context(
    context: Optional[dict] = None,
    *,
    stock_code: Optional[str] = None,
    sector: Optional[str] = None,
) -> Dict[str, Any]:
    """压缩盘前上下文供 LLM / API / UI。"""
    ctx = context if context is not None else load_market_context(use_cache=True)
    macro = ctx.get("macro") or {}
    sentiment = ctx.get("market_sentiment") or {}
    announcement = ctx.get("announcement") or {}
    reg = announcement.get("regulatory") or {}
    ipo = announcement.get("ipo") or {}
    priors = build_market_priors(
        ctx,
        stock_code=stock_code,
        sector=sector,
    )
    fresh = ctx.get("freshness") or freshness_report(ctx)
    return {
        "freshness": fresh,
        "macro": {
            "overseas_tech_1d_pct": macro.get("overseas_tech_1d_pct"),
            "a50_1d_pct": macro.get("a50_1d_pct"),
            "lead_lag_expected_gap_pct": macro.get("lead_lag_expected_gap_pct"),
            "liquidity_stress_score": macro.get("liquidity_stress_score"),
        },
        "sentiment": {
            "sentiment_cycle_score": sentiment.get("sentiment_cycle_score"),
            "broken_limit_rate": sentiment.get("broken_limit_rate"),
            "limit_up_open_premium_pct": sentiment.get("limit_up_open_premium_pct"),
        },
        "regulatory": {
            "active": bool(reg.get("active")),
            "count": reg.get("count"),
            "penalty_codes": list(reg.get("penalty_codes") or [])[:6],
            "penalty_concepts": list(reg.get("penalty_concepts") or [])[:6],
        },
        "ipo": {
            "extreme_ipo_day": bool(ipo.get("extreme_ipo_day")),
            "liquidity_drain_ratio": ipo.get("liquidity_drain_ratio")
            or ipo.get("liquidity_drain_ratio_proxy"),
        },
        "prior_active": bool(priors.get("market_prior_active")),
        "prior_warnings": list(priors.get("market_prior_warnings") or [])[:8],
        "prior_flags": {
            "cross_market": bool((priors.get("cross_market_prior") or {}).get("active")),
            "market_sentiment": bool(
                (priors.get("market_sentiment_prior") or {}).get("active")
            ),
            "regulatory": bool((priors.get("regulatory_prior") or {}).get("active")),
            "ipo_drain": bool((priors.get("ipo_drain_prior") or {}).get("active")),
        },
        "note": "M 层 prior 不改 ŷ；仅调仓执行缩放。",
    }


def ingest_macro_backfill(*, lookback: int = 90) -> Dict[str, Any]:
    """加长 lookback ingest + 写入 macro 历史索引。"""
    from core.research.macro_history import save_macro_history_index

    r = ingest_macro(lookback=max(30, int(lookback or 90)))
    snap = r.get("snapshot") or {}
    hist_path = save_macro_history_index(snap, source_as_of=snap.get("as_of"))
    rows = len((snap.get("series") or {}).get("sox", {}).get("recent_bars") or [])
    return {
        **r,
        "history_path": hist_path,
        "history_bars_hint": rows,
        "lookback": lookback,
    }


def refresh_concept_graph(
    *,
    concepts: Optional[List[str]] = None,
    max_concepts: int = 8,
    force: bool = False,
) -> Dict[str, Any]:
    """刷新概念成分图谱磁盘缓存。"""
    from core.concept_graph_store import (
        DEFAULT_CONCEPT_HINTS,
        load_concept_graph_cache,
        save_concept_graph_cache,
    )
    from core.ports.market import build_code_concept_index

    hints = list(concepts or DEFAULT_CONCEPT_HINTS)
    if not force:
        cached, meta = load_concept_graph_cache()
        if cached and not meta.get("stale") and meta.get("code_count", 0) > 50:
            return {
                "ok": True,
                "skipped": True,
                "code_count": meta.get("code_count"),
                "concept_count": meta.get("concept_count"),
            }
    index = build_code_concept_index(
        hints,
        max_concepts=max_concepts,
        use_cache=False,
    )
    concept_members: Dict[str, List[str]] = {}
    for code, tags in index.items():
        for t in tags:
            concept_members.setdefault(t, [])
            if code not in concept_members[t]:
                concept_members[t].append(code)
    path = save_concept_graph_cache(concepts=concept_members, code_index=index)
    return {
        "ok": bool(index),
        "path": path,
        "code_count": len(index),
        "concept_count": len(concept_members),
    }
