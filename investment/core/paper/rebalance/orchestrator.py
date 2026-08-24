"""Unified paper rebalance orchestrator with explicit modes (C3)."""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

RebalanceMode = Literal["holding_rules", "cross_section", "cluster_book"]

# 确认落账复用预演落盘簿的最长年龄（秒）；超时仍重打分
_CLUSTER_BOOK_REUSE_MAX_AGE_SEC = 2 * 3600
# 盘中冲击失效：抽样簿内 |涨跌幅|≥阈值的占比 / 涨跌停只数
_CLUSTER_BOOK_SHOCK_ABS_CHG_PCT = 5.0
_CLUSTER_BOOK_SHOCK_FRAC = 0.25
_CLUSTER_BOOK_LIMIT_EVENT_MIN = 3
_CLUSTER_BOOK_SHOCK_SAMPLE = 40


def _quote_change_pct(quote: Optional[dict]) -> Optional[float]:
    if not isinstance(quote, dict):
        return None
    for key in ("change_raw", "change_pct", "pct_chg"):
        raw = quote.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _cluster_book_market_shock_reason(
    book: List[dict],
    *,
    abs_chg_pct: float = _CLUSTER_BOOK_SHOCK_ABS_CHG_PCT,
    shock_frac: float = _CLUSTER_BOOK_SHOCK_FRAC,
    limit_min: int = _CLUSTER_BOOK_LIMIT_EVENT_MIN,
    sample_n: int = _CLUSTER_BOOK_SHOCK_SAMPLE,
) -> Optional[str]:
    """复用前轻量行情检查：巨震/涨跌停潮 → 失效缓存。取行情失败则放行（fail-open）。"""
    codes = [
        str(b.get("stock_code") or "").strip()
        for b in (book or [])
        if str(b.get("stock_code") or "").strip()
    ][: max(1, int(sample_n))]
    if not codes:
        return None
    try:
        from core.data.service import get_default_service

        quotes = dict(get_default_service().batch_get_quotes(codes) or {})
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance_orchestrator.py", exc_info=True)
        return None
    if not quotes:
        return None

    shocked = 0
    limits = 0
    scored = 0
    for code in codes:
        q = quotes.get(code) or {}
        if not q.get("success") and q.get("price") is None and q.get("price_raw") is None:
            continue
        scored += 1
        chg = _quote_change_pct(q)
        if chg is not None and abs(chg) >= float(abs_chg_pct):
            shocked += 1
        # 粗判涨跌停：|涨跌幅|接近阈值，或文案含停牌/涨停/跌停
        tip = str(q.get("trade_status") or q.get("status") or q.get("message") or "")
        if any(k in tip for k in ("涨停", "跌停", "停牌")) or chg is not None and abs(chg) >= 9.5:
            limits += 1
    if scored <= 0:
        return None
    if shocked / float(scored) >= float(shock_frac):
        return f"book_shock:{shocked}/{scored}|chg≥{abs_chg_pct:g}%"
    if limits >= int(limit_min):
        return f"book_limit_events:{limits}"
    return None


def resolve_rebalance_mode(
    paper: dict,
    *,
    cluster_mode: bool = False,
) -> RebalanceMode:
    """Pick cross_section vs cluster_book from paper rules and live cluster scoring."""
    rules = paper.get("rules") or {}
    scoring_mode = "off"
    try:
        from core.signal.cluster.live import get_cluster_scoring_cfg

        scoring_mode = str(
            (get_cluster_scoring_cfg() or {}).get("mode") or "off"
        ).strip().lower()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance_orchestrator.py", exc_info=True)
        scoring_mode = "off"
    use_cluster = bool(cluster_mode) or bool(
        rules.get("cluster_mode") if isinstance(rules, dict) else False
    ) or scoring_mode == "active"
    return "cluster_book" if use_cluster else "cross_section"


def _resolve_top_k_limit(
    paper: dict,
    *,
    top_k: Optional[int],
    limit: Optional[int],
    ranking_len: Optional[int] = None,
    respect_max_positions: bool = True,
) -> tuple[int, int]:
    rules = paper.get("rules") or {}
    max_pos = max(1, int(rules.get("max_positions") or 5))
    k = max(1, int(top_k if top_k is not None else max_pos))
    k = min(k, max_pos, 30)
    if not respect_max_positions and ranking_len is not None:
        k = max(1, min(int(ranking_len or k), 80))
    lim = max(int(limit or 0), k, max(20, max_pos))
    return k, lim


def _supplement_holding_scores(
    paper: dict,
    score_rows: List[dict],
    *,
    horizon_days: int = 3,
) -> List[dict]:
    """为纸面持仓补打分：分池宇宙常不含全部持仓，缺分会显示「—」且滞回卖不出去。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.signal.service import get_default_signal_service

    svc = get_default_signal_service()
    have = {
        str(r.get("stock_code") or "").strip()
        for r in (score_rows or [])
        if str(r.get("stock_code") or "").strip()
    }
    missing = []
    name_by: Dict[str, str] = {}
    for h in paper.get("holdings") or []:
        code = str(h.get("stock_code") or "").strip()
        if not code or code in have:
            continue
        missing.append(code)
        if h.get("stock_name"):
            name_by[code] = str(h.get("stock_name"))
    if not missing:
        return list(score_rows or [])

    horizon = max(1, min(int(horizon_days or 3), 3))

    def _one(code: str) -> dict:
        try:
            result = svc.score_one(
                code,
                horizon_days=horizon,
                cluster_mode="active",
                skip_fundamentals=True,
                skip_sentiment=True,
            )
        except Exception as e:
            logger.exception('unexpected error in _one')
            return {
                "stock_code": code,
                "stock_name": name_by.get(code),
                "score": None,
                "error": str(e),
                "holding_supplement": True,
            }
        item = result.item or {}
        if not result.success and not item:
            return {
                "stock_code": code,
                "stock_name": name_by.get(code),
                "score": None,
                "error": (result.raw or {}).get("error") or "score_failed",
                "holding_supplement": True,
            }
        out = svc.pack_holding_row(item, cluster_mode=result.cluster_mode or "active")
        out["stock_code"] = out.get("stock_code") or code
        out["stock_name"] = out.get("stock_name") or name_by.get(code)
        out["holding_supplement"] = True
        if out.get("score") is None and result.rank_key is not None:
            out["score"] = result.rank_key
            out["decision_score"] = result.rank_key
        return out

    extra: List[dict] = []
    workers = max(1, min(8, len(missing)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, c): c for c in missing}
        for fut in as_completed(futs):
            try:
                extra.append(fut.result())
            except Exception as e:
                logger.exception('unexpected error in _supplement_holding_scores')
                code = futs[fut]
                extra.append(
                    {
                        "stock_code": code,
                        "stock_name": name_by.get(code),
                        "score": None,
                        "error": str(e),
                        "holding_supplement": True,
                    }
                )
    return list(score_rows or []) + extra


def run_paper_rebalance(
    paper: dict,
    *,
    mode: RebalanceMode,
    dry_run: bool = False,
    simulate_buy: bool = False,
    strategy: str = "short",
    on_progress=None,
    top_k: Optional[int] = None,
    limit: Optional[int] = None,
    cluster_mode: bool = False,
    ranked: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Route paper rebalance to the correct simulator for ``mode``.

    ``ranked``：可选，分池模式预计算好的建簿结果（写锁外 prepare），避免占锁重打分。
    """
    if mode == "holding_rules":
        return _run_holding_rules(
            paper,
            simulate_buy=simulate_buy,
            strategy=strategy,
            on_progress=on_progress,
            dry_run=dry_run,
        )
    if mode == "cluster_book":
        return _run_cluster_book(
            paper,
            dry_run=dry_run,
            top_k=top_k,
            limit=limit,
            cluster_mode=cluster_mode,
            ranked=ranked,
        )
    return _run_cross_section(
        paper,
        dry_run=dry_run,
        top_k=top_k,
        limit=limit,
    )


def _run_holding_rules(
    paper: dict,
    *,
    simulate_buy: bool,
    strategy: str,
    on_progress,
    dry_run: bool,
) -> Dict[str, Any]:
    from core.paper.cycle import run_daily_cycle

    result = run_daily_cycle(
        paper,
        simulate_buy=simulate_buy,
        strategy=strategy,
        on_progress=on_progress,
    )
    return {
        **result,
        "mode": "holding_rules",
        "dry_run": dry_run,
        "success": True,
        "ok": True,
    }


def _run_cross_section(
    paper: dict,
    *,
    dry_run: bool,
    top_k: Optional[int],
    limit: Optional[int],
) -> Dict[str, Any]:
    from core.paper.rebalance import simulate_cross_section_rebalance
    from core.signal.service import get_default_signal_service
    from core.strategy import apply_strategy_to_paper

    sid = str(paper.get("strategy_id") or "short_conservative").strip()
    try:
        apply_strategy_to_paper(paper, sid)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance_orchestrator.py", exc_info=True)
        pass

    k, lim = _resolve_top_k_limit(paper, top_k=top_k, limit=limit)
    ranked = get_default_signal_service().rank_cross_section(None, limit=lim).as_dict()
    if not ranked.get("success"):
        return {
            "success": False,
            "ok": False,
            "mode": "cross_section",
            "cluster_mode": False,
            **ranked,
        }

    ranking = ranked.get("ranking") or []
    result = simulate_cross_section_rebalance(paper, ranking, top_k=k)
    return {
        "success": True,
        "ok": True,
        "mode": "cross_section",
        "dry_run": dry_run,
        "top_k": k,
        "cluster_mode": False,
        "cross_section": ranked,
        "ranking": ranking,
        **result,
    }


def _parse_iso_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _cluster_book_age_sec(doc: dict) -> Optional[float]:
    ts = _parse_iso_ts(doc.get("updated_at"))
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        # 存盘多为 naive UTC / 本地；按 UTC naive 与 now 对齐
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds())


def try_reuse_active_cluster_book(
    *,
    max_age_sec: float = _CLUSTER_BOOK_REUSE_MAX_AGE_SEC,
) -> Optional[Dict[str, Any]]:
    """确认落账：若预演刚写入的 active book 仍新鲜，直接复用（跳过 N 票重打分）。"""
    from core.signal.cluster.live import (
        load_active_cluster_book,
        load_active_cluster_weights,
    )

    doc = load_active_cluster_book()
    if not isinstance(doc, dict):
        return None
    book = list(doc.get("book") or [])
    if not book:
        return None
    age = _cluster_book_age_sec(doc)
    if age is None or age > float(max_age_sec):
        return None

    shock = _cluster_book_market_shock_reason(book)
    if shock:
        return None

    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    active = load_active_cluster_weights() or {}
    scored = list(doc.get("scored_all") or []) or book
    return {
        "success": True,
        "ok": True,
        "task": "rank_cluster_pools",
        "mode": "cluster_score_global_rank",
        "from_cache": True,
        "book_age_sec": round(age, 1),
        "cluster_version": meta.get("version") or active.get("version"),
        "top_n_per_group": meta.get("top_n_per_group"),
        "max_names": meta.get("max_names"),
        "min_score": meta.get("min_score"),
        "min_score_disabled": bool(meta.get("min_score_disabled")),
        "groups": [],
        "book": book,
        "ranking": book,
        "scored_all": scored,
        "unmapped_count": 0,
        "below_min_score_count": 0,
        "rejected": [],
        "book_path": None,
        "note": "复用预演落盘目标簿（跳过重打分）",
    }


def prepare_cluster_book_rank(
    paper: dict,
    *,
    dry_run: bool = True,
    reuse_max_age_sec: float = _CLUSTER_BOOK_REUSE_MAX_AGE_SEC,
) -> Dict[str, Any]:
    """分池建簿 / 复用。应在 paper 写锁外调用，避免长时间占锁卡住 /api/paper。"""
    from core.signal.cluster.live import assess_cluster_live_health
    from core.signal.score_display import selection_min_score
    from core.signal.service import get_default_signal_service

    health = assess_cluster_live_health(compute_ic=False)
    ranked: Optional[Dict[str, Any]] = None
    # 预演/落账：状态栏「簿 n/m」同源 active book 仍新鲜则复用，避免再次 N 票打分建簿
    ranked = try_reuse_active_cluster_book(max_age_sec=reuse_max_age_sec)
    if ranked is None:
        cluster_min = selection_min_score(paper)
        ranked = get_default_signal_service().rank_cluster_pools(
            None,
            persist_book=True,
            min_score=cluster_min,
        ).as_dict()
    if not isinstance(ranked, dict):
        ranked = {"success": False, "error": "rank_failed"}
    ranked = {**ranked, "health": health}
    return ranked


def _run_cluster_book(
    paper: dict,
    *,
    dry_run: bool,
    top_k: Optional[int],
    limit: Optional[int],
    cluster_mode: bool,
    ranked: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from core.paper.rebalance import simulate_cross_section_rebalance
    from core.strategy import apply_strategy_to_paper

    sid = str(paper.get("strategy_id") or "short_conservative").strip()
    try:
        apply_strategy_to_paper(paper, sid)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance_orchestrator.py", exc_info=True)
        pass

    if ranked is None:
        ranked = prepare_cluster_book_rank(paper, dry_run=dry_run)
    health = ranked.get("health") if isinstance(ranked, dict) else None
    if health is None:
        try:
            from core.signal.cluster.live import assess_cluster_live_health

            health = assess_cluster_live_health()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance_orchestrator.py", exc_info=True)
            health = {"alerts": []}

    if not ranked.get("success"):
        return {
            "success": False,
            "ok": False,
            "mode": "cluster_book",
            "cluster_mode": True,
            **ranked,
        }

    ranking = ranked.get("book") or ranked.get("ranking") or []
    score_rows: List[dict] = list(ranked.get("scored_all") or [])
    if not score_rows:
        for g in ranked.get("groups") or []:
            score_rows.extend(list(g.get("ranking") or []))
    reused = bool(ranked.get("from_cache"))
    # 复用簿时跳过补打分；确认落账再跳过舆情重拉（预演仍可跑先验）
    if not reused:
        score_rows = _supplement_holding_scores(paper, score_rows)

    k, _lim = _resolve_top_k_limit(
        paper,
        top_k=top_k,
        limit=limit,
        ranking_len=len(ranking),
        respect_max_positions=False,
    )
    result = simulate_cross_section_rebalance(
        paper,
        ranking,
        top_k=k,
        respect_max_positions=False,
        score_lookup=score_rows,
        skip_sentiment_prior=(reused and not dry_run),
        skip_market_prior=(reused and not dry_run),
    )
    return {
        "success": True,
        "ok": True,
        "mode": "cluster_book",
        "dry_run": dry_run,
        "top_k": k,
        "cluster_mode": True,
        "cluster_pools": ranked,
        "health": health,
        "ranking": ranking,
        "score_rows": score_rows,
        "book_reused": bool(ranked.get("from_cache")),
        **result,
    }
