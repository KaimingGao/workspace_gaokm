"""Unified paper rebalance orchestrator with explicit modes (C3)."""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Literal, Optional

# cluster_book 仅保留为停用枚举：显式传入时返回错误，无实现入口。
# 与 live `cluster_book_active` 产物无关。
RebalanceMode = Literal["holding_rules", "cross_section", "cluster_book"]


def resolve_rebalance_mode(
    paper: dict,
    *,
    cluster_mode: bool = False,
) -> RebalanceMode:
    """纸面调仓模式。

    分池簿路径已停用；保留 ``cluster_mode`` 参数仅为 API 兼容，一律走横截面
    （Follow 主路径由 ``PaperTradesMixin.rebalance(matrix_mode=…)`` 接管）。
    """
    _ = paper, cluster_mode
    return "cross_section"


def _resolve_top_k_limit(
    paper: dict,
    *,
    top_k: Optional[int],
    limit: Optional[int],
) -> tuple[int, int]:
    rules = paper.get("rules") or {}
    max_pos = max(1, int(rules.get("max_positions") or 5))
    k = max(1, int(top_k if top_k is not None else max_pos))
    k = min(k, max_pos, 30)
    lim = max(int(limit or 0), k, max(20, max_pos))
    return k, lim


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
) -> Dict[str, Any]:
    """Route paper rebalance to the correct simulator for ``mode``.

    ``cluster_mode`` 仅为 API 兼容；``mode="cluster_book"`` 直接失败。
    """
    _ = cluster_mode
    if mode == "holding_rules":
        return _run_holding_rules(
            paper,
            simulate_buy=simulate_buy,
            strategy=strategy,
            on_progress=on_progress,
            dry_run=dry_run,
        )
    if mode == "cluster_book":
        return {
            "success": False,
            "ok": False,
            "mode": "cluster_book",
            "dry_run": dry_run,
            "error": "分池簿调仓已停用；请用观察池 path_matrix",
            "confirm_supported": False,
        }
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
