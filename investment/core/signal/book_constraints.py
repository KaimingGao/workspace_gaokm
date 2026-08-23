"""分池建簿约束：可成交过滤 · 行业集中度 · τ 闸排序 · 与纸面 risk 同配置源。

纸面阶段：不碰实盘；约束前移到 ``rank_cluster_pools``，避免簿内占坑买时被 clip。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_BOOK_CONSTRAINTS: Dict[str, Any] = {
    "enabled": True,
    "filter_untradeable": True,
    "enforce_sector_cap": True,
    # defer=τ 不过闸的排到 τ 通过票之后再装填；exclude=不进簿；off=不区分
    "tau_fail_mode": "defer",
}


def get_book_constraints_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    out = dict(DEFAULT_BOOK_CONSTRAINTS)
    try:
        from core.signal.config import load_signal_config

        cfg = config if isinstance(config, dict) else load_signal_config()
        raw = ((cfg or {}).get("cluster_scoring") or {}).get("book_constraints")
        if isinstance(raw, dict):
            out.update(raw)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        pass
    mode = str(out.get("tau_fail_mode") or "defer").strip().lower()
    if mode not in ("defer", "exclude", "off"):
        mode = "defer"
    out["tau_fail_mode"] = mode
    out["enabled"] = bool(out.get("enabled", True))
    out["filter_untradeable"] = bool(out.get("filter_untradeable", True))
    out["enforce_sector_cap"] = bool(out.get("enforce_sector_cap", True))
    return out


def resolve_book_risk_limits(
    *,
    strategy: Optional[str] = None,
    paper: Optional[dict] = None,
) -> Dict[str, Any]:
    """与纸面调仓同一套上限：StrategySpec.risk + paper.rules 换手。"""
    max_position_pct = 25.0
    max_sector_pct = 40.0
    max_positions = 40
    max_turnover_pct = None
    try:
        from core.strategy import get_strategy_spec

        spec = get_strategy_spec(strategy) if strategy else get_strategy_spec()
        risk = (spec or {}).get("risk") or {}
        if risk.get("max_position_pct") is not None:
            max_position_pct = float(risk["max_position_pct"])
        if risk.get("max_sector_pct") is not None:
            max_sector_pct = float(risk["max_sector_pct"])
        if risk.get("max_positions") is not None:
            max_positions = int(risk["max_positions"])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        pass
    rules = {}
    if isinstance(paper, dict):
        rules = paper.get("rules") or {}
    else:
        try:
            from core.paper import load_paper

            rules = (load_paper() or {}).get("rules") or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
            rules = {}
    raw_mto = rules.get("max_turnover_pct", rules.get("max_turnover"))
    if raw_mto is not None:
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None
    try:
        from core.signal.cluster_live import get_cluster_scoring_cfg

        cs = get_cluster_scoring_cfg()
        if cs.get("max_names") is not None:
            max_positions = max(max_positions, int(cs.get("max_names") or max_positions))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        pass
    return {
        "max_position_pct": float(max_position_pct),
        "max_sector_pct": float(max_sector_pct),
        "max_positions": int(max_positions),
        "max_turnover_pct": max_turnover_pct,
    }


def tradeable_block_reason(code: str, quote: Optional[dict]) -> Optional[str]:
    """与纸面 ``_buy_match_block_reason`` 同口径（涨停/停牌/无价）。"""
    try:
        from core.paper.rebalance import _buy_match_block_reason

        return _buy_match_block_reason(code, quote)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        q = quote or {}
        if not q:
            return None
        px = q.get("price_raw") or q.get("price") or q.get("open")
        if px is None:
            return "无行情价，跳过建簿"
        return None


def _tau_ok(row: dict) -> bool:
    try:
        from core.signal.dual_score import buy_passes_tau_gate

        ok, _ = buy_passes_tau_gate(row)
        return bool(ok)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        return True


def _y_check_ok(row: dict, dual_cfg: Optional[dict] = None) -> bool:
    try:
        from core.signal.y_state import buy_passes_y_check, stamp_y_state

        if row.get("y_check") is None:
            stamp_y_state(row, config={"dual_score": dual_cfg} if dual_cfg else None)
        ok, _ = buy_passes_y_check(
            row, config={"dual_score": dual_cfg} if dual_cfg else None
        )
        return bool(ok)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        return True


def _y_check_rank(row: dict, dual_cfg: Optional[dict] = None) -> int:
    try:
        from core.signal.y_state import book_check_sort_key

        return int(
            book_check_sort_key(
                row, config={"dual_score": dual_cfg} if dual_cfg else None
            )
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        return 9


def _rank_score(row: dict, dual_cfg: Optional[dict] = None) -> float:
    try:
        from core.signal.dual_score import rank_key_for_item

        v = rank_key_for_item(row, config={"dual_score": dual_cfg} if dual_cfg else None)
        return float(v) if v is not None else float(row.get("score") or 0.0)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        try:
            return float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0


def _sector_of(row: dict, sector_map: Optional[dict]) -> str:
    s = row.get("sector")
    if s:
        return str(s)
    code = str(row.get("stock_code") or "")
    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        sm = sector_map if sector_map is not None else load_sector_map()
        return str(_sector_for(code, sm) or "其他")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in book_constraints.py", exc_info=True)
        return "其他"


def max_names_per_sector(max_names: int, max_sector_pct: float) -> int:
    """等权簿近似：行业名额 ≤ floor(max_names × max_sector_pct/100)，至少 1。"""
    n = max(1, int(max_names or 1))
    pct = max(1.0, min(100.0, float(max_sector_pct or 40.0)))
    return max(1, int(n * pct / 100.0))


def fill_book_with_constraints(
    eligible: Sequence[dict],
    *,
    max_names: int,
    quotes: Optional[Dict[str, dict]] = None,
    dual_cfg: Optional[dict] = None,
    risk_limits: Optional[dict] = None,
    constraints: Optional[dict] = None,
    sector_map: Optional[dict] = None,
) -> Dict[str, Any]:
    """约束感知装填选股簿。

    返回 ``book`` · ``skipped`` · ``stats``。
    """
    cfg = get_book_constraints_cfg() if constraints is None else {**DEFAULT_BOOK_CONSTRAINTS, **constraints}
    limits = risk_limits or resolve_book_risk_limits()
    max_n = max(1, int(max_names or limits.get("max_positions") or 40))
    max_sec_pct = float(limits.get("max_sector_pct") or 40.0)
    per_sec_cap = max_names_per_sector(max_n, max_sec_pct)
    qmap = quotes or {}
    skipped: List[dict] = []
    stats = {
        "untradeable": 0,
        "tau_fail_deferred": 0,
        "tau_fail_excluded": 0,
        "y_check_deferred": 0,
        "y_check_excluded": 0,
        "sector_cap": 0,
        "filled": 0,
        "per_sector_cap": per_sec_cap,
        "max_sector_pct": max_sec_pct,
        "constraints": {
            "enabled": cfg.get("enabled"),
            "filter_untradeable": cfg.get("filter_untradeable"),
            "enforce_sector_cap": cfg.get("enforce_sector_cap"),
            "tau_fail_mode": cfg.get("tau_fail_mode"),
        },
        "sector_counts": {},
    }

    rows = [dict(r) for r in eligible if isinstance(r, dict)]
    if not cfg.get("enabled"):
        rows.sort(
            key=lambda x: (
                -_rank_score(x, dual_cfg),
                str(x.get("stock_code") or ""),
            )
        )
        book = rows[:max_n]
        for i, b in enumerate(book):
            b["rank"] = i + 1
        stats["filled"] = len(book)
        return {"book": book, "skipped": skipped, "stats": stats}

    # 可成交过滤
    candidates: List[dict] = []
    for r in rows:
        code = str(r.get("stock_code") or "")
        if cfg.get("filter_untradeable"):
            reason = tradeable_block_reason(code, qmap.get(code))
            if reason:
                stats["untradeable"] += 1
                skipped.append(
                    {
                        **{k: r.get(k) for k in ("stock_code", "stock_name", "score", "sector")},
                        "skip_reason": reason,
                        "skip_stage": "tradeable",
                    }
                )
                r["book_skip_reason"] = reason
                continue
        r["sector"] = _sector_of(r, sector_map)
        r["_tau_ok"] = _tau_ok(r)
        r["_y_ok"] = _y_check_ok(r, dual_cfg)
        r["_y_rank"] = _y_check_rank(r, dual_cfg)
        r["_rank_score"] = _rank_score(r, dual_cfg)
        candidates.append(r)

    mode = cfg.get("tau_fail_mode") or "defer"
    if mode == "exclude":
        kept = []
        for r in candidates:
            if r.get("_tau_ok") and r.get("_y_ok"):
                kept.append(r)
            else:
                if not r.get("_tau_ok"):
                    stats["tau_fail_excluded"] += 1
                    skipped.append(
                        {
                            "stock_code": r.get("stock_code"),
                            "stock_name": r.get("stock_name"),
                            "score": r.get("score"),
                            "predicted_score_tau": r.get("predicted_score_tau"),
                            "skip_reason": "ŷ_τ 未过买入闸，建簿排除",
                            "skip_stage": "tau_gate",
                        }
                    )
                elif not r.get("_y_ok"):
                    stats["y_check_excluded"] = int(stats.get("y_check_excluded") or 0) + 1
                    skipped.append(
                        {
                            "stock_code": r.get("stock_code"),
                            "stock_name": r.get("stock_name"),
                            "score": r.get("score"),
                            "y_check": r.get("y_check"),
                            "skip_reason": f"Y 校验未过（{r.get('y_check')}），建簿排除",
                            "skip_stage": "y_check",
                        }
                    )
        candidates = kept
        candidates.sort(
            key=lambda x: (
                int(x.get("_y_rank") or 9),
                -float(x.get("_rank_score") or 0.0),
                str(x.get("stock_code") or ""),
            )
        )
    elif mode == "defer":
        for r in candidates:
            if not r.get("_tau_ok"):
                stats["tau_fail_deferred"] += 1
            if not r.get("_y_ok"):
                stats["y_check_deferred"] = int(stats.get("y_check_deferred") or 0) + 1
        candidates.sort(
            key=lambda x: (
                0 if x.get("_tau_ok") else 1,
                0 if x.get("_y_ok") else 1,
                int(x.get("_y_rank") or 9),
                -float(x.get("_rank_score") or 0.0),
                str(x.get("stock_code") or ""),
            )
        )
    else:
        candidates.sort(
            key=lambda x: (
                int(x.get("_y_rank") or 9),
                -float(x.get("_rank_score") or 0.0),
                str(x.get("stock_code") or ""),
            )
        )

    book: List[dict] = []
    sec_count: Dict[str, int] = {}
    for r in candidates:
        if len(book) >= max_n:
            break
        sec = str(r.get("sector") or "其他")
        if cfg.get("enforce_sector_cap") and sec_count.get(sec, 0) >= per_sec_cap:
            stats["sector_cap"] += 1
            skipped.append(
                {
                    "stock_code": r.get("stock_code"),
                    "stock_name": r.get("stock_name"),
                    "score": r.get("score"),
                    "sector": sec,
                    "skip_reason": f"行业 {sec} 建簿名额已满（≤{per_sec_cap}，约 {max_sec_pct:g}%）",
                    "skip_stage": "sector_cap",
                }
            )
            continue
        row = dict(r)
        if not row.get("_tau_ok"):
            row["tau_gate_fail"] = True
        for k in ("_tau_ok", "_rank_score"):
            row.pop(k, None)
        book.append(row)
        sec_count[sec] = sec_count.get(sec, 0) + 1

    for i, b in enumerate(book):
        b["rank"] = i + 1
        b["book_constraint"] = True
    stats["filled"] = len(book)
    stats["sector_counts"] = dict(sec_count)
    return {"book": book, "skipped": skipped, "stats": stats}
