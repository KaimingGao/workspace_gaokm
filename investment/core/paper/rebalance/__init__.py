"""横截面驱动的纸面调仓模拟（P11.3，非实盘）。

拆分：换手/选码/撮合/评分索引见同名前缀模块；门禁桥见 ``paper_rebalance_gate``；收尾见 ``paper_rebalance_report``；会话态见 ``RebalanceState``。
本文件保留 ``simulate_cross_section_rebalance`` 编排（预取 → 卖腿 → 门禁桥 → 买腿 → finalize）。
对外 API / 单测 patch 路径仍以本模块为准（再导出）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.paper.costs import cost_params, resolve_cost_model
from core.paper.rebalance.force_trim import (  # noqa: F401 — facade re-export
    select_force_trim_codes,
    select_force_trim_codes_sellable,
)
from core.paper.rebalance.match import (  # noqa: F401 — facade re-export
    _batch_query_quotes,
    _buy_match_block_reason,
    _sell_match_block_reason,
    attach_change_pct_to_rebalance_report,
    quote_change_pct,
)
from core.paper.rebalance.reasons import (  # noqa: F401 — facade re-export
    _SOFT_HOLD_ELIGIBLE_REASONS,
    SELL_REASON_BELOW_HOLD,
    SELL_REASON_HARD_REJECT,
    SELL_REASON_MARKET_TRIM,
    SELL_REASON_NOT_IN_TOPK,
    SELL_REASON_SENTIMENT_TRIM,
)
from core.paper.rebalance.buy import run_buy_leg
from core.paper.rebalance.gate import apply_post_sell_gate
from core.paper.rebalance.report import finalize_cross_section_rebalance_report
from core.paper.rebalance.sell import run_sell_leg
from core.paper.rebalance.state import RebalanceState, ScoreIndexes, build_score_indexes
from core.paper.rebalance.turnover import (  # noqa: F401 — facade re-export
    build_rebalance_cash_impact,
    clip_shares_to_turnover_budget,
    compute_turnover_stats,
    resolve_buy_turnover_budget,
)
from core.ports.market import quote_price as _quote_price  # noqa: F401 — facade re-export

logger = logging.getLogger(__name__)

__all__ = [
    "SELL_REASON_BELOW_HOLD",
    "SELL_REASON_HARD_REJECT",
    "SELL_REASON_MARKET_TRIM",
    "SELL_REASON_NOT_IN_TOPK",
    "SELL_REASON_SENTIMENT_TRIM",
    "_SOFT_HOLD_ELIGIBLE_REASONS",
    "_batch_query_quotes",
    "_buy_match_block_reason",
    "_quote_price",
    "_sell_match_block_reason",
    "attach_change_pct_to_rebalance_report",
    "build_rebalance_cash_impact",
    "clip_shares_to_turnover_budget",
    "compute_turnover_stats",
    "quote_change_pct",
    "resolve_buy_turnover_budget",
    "RebalanceState",
    "ScoreIndexes",
    "build_score_indexes",
    "finalize_cross_section_rebalance_report",
    "apply_post_sell_gate",
    "run_buy_leg",
    "run_sell_leg",
    "select_force_trim_codes",
    "select_force_trim_codes_sellable",
    "simulate_cross_section_rebalance",
]


def simulate_cross_section_rebalance(
    paper: dict,
    ranking: List[dict],
    *,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    respect_max_positions: bool = True,
    score_lookup: Optional[List[dict]] = None,
    skip_sentiment_prior: bool = False,
    skip_market_prior: bool = False,
) -> Dict[str, Any]:
    """
    按横截面 TopK / 分池目标簿调仓。

    - 横截面（``respect_max_positions=True``）：卖出不在 TopK，或 ŷ_trade 低于 min_hold_score；
      再从 TopK 买入（EOD≥min_score 且过 τ 闸）的未持仓。
    - 分池（``respect_max_positions=False``）：滞回——买入仍看目标簿且 ŷ_EOD≥min_score（+τ 闸）；
      **卖出仅当 ŷ_trade < min_hold_score**，不因「未进簿/截断」清仓。
      （卖/表/排序同一轴；买入门槛仍用隔夜 ŷ_EOD。）
      分池买入 sizing：``min(cash, equity × ratio)``，ratio 优先吃 optimize 目标仓，否则 1/簿长，再受 position_pct 封顶。
    买入前强制 check_account_risk；超限则拦截加仓并写 risk_block 日志。

    ``score_lookup``：可选全量打分行（含低于 min_score 未进簿的票），供卖出腿带分。
    ``skip_sentiment_prior``：确认落账复用预演簿时跳过舆情重拉（只成交）。
    ``skip_market_prior``：与 skip_sentiment_prior 对称，跳过市场级 prior 执行。
    """
    from core.data.facade import get_quote  # noqa: F401 — 保留历史引用点

    rules = paper.get("rules") or {}
    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    max_pos = max(1, int(rules.get("max_positions") or 5))
    if top_k is None:
        top_k = max_pos
    else:
        top_k = max(1, int(top_k))
    if respect_max_positions:
        top_k = min(top_k, max_pos, 30)
    else:
        # 分池：按簿长持有；上限对齐 cluster max_names（80），勿再用 30 砍掉簿尾
        top_k = min(top_k, 80)
    if min_score is None:
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

        min_score = resolve_buy_floor(paper, heuristic_default=55.0)
        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)
    else:
        min_score = float(min_score)
        from core.signal.score_display import resolve_hold_floor

        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)

    # 双轨：predicted 门槛可被 rebalance_tracks 覆盖；heuristic 在买卖循环按票解析
    try:
        from core.signal.rebalance_tracks import get_rebalance_tracks_cfg, predicted_floors

        _tracks_cfg = get_rebalance_tracks_cfg()
        _pred_buy, _pred_hold = predicted_floors(_tracks_cfg)
        if _tracks_cfg.get("predicted_buy_floor") is not None:
            min_score = float(_pred_buy)
        min_hold_score = float(_pred_hold)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        _tracks_cfg = {}
    # 分池：持仓上限=簿长；买入门槛见 resolve_buy_floor / 双轨
    if not respect_max_positions:
        max_positions = top_k
    else:
        max_positions = max_pos
    position_pct = float(rules.get("position_pct") or 0.15)
    max_turnover_pct: Optional[float] = None
    raw_mto = rules.get("max_turnover_pct", rules.get("max_turnover"))
    if raw_mto is not None and raw_mto != "":
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None

    indexes = build_score_indexes(
        ranking, top_k=top_k, score_lookup=score_lookup
    )
    top_items = indexes.top_items
    top_codes = indexes.top_codes
    item_by_code = indexes.item_by_code
    trade_score_by_code = indexes.trade_score_by_code
    eod_score_by_code = indexes.eod_score_by_code
    tau_by_code = indexes.tau_by_code
    hard_reject_by_code = indexes.hard_reject_by_code
    # 兼容旧引用名：卖出主分 = ŷ_trade
    score_by_code = indexes.score_by_code

    holdings = paper.get("holdings") or []
    cash_before = float(paper.get("cash") or 0)
    position_count_before = len(
        [h for h in holdings if float(h.get("shares") or 0) > 0]
    )
    equity_before: Optional[float] = None
    try:
        from core.paper.ledger import mark_to_market as _mtm0

        equity_before = float((_mtm0(paper) or {}).get("equity") or 0) or None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        equity_before = None
    cash = cash_before
    sell_trades: List[dict] = []
    kept = []
    turnover_capped = False
    turnover_skipped: List[str] = []
    # 卖出腿（含分池膨胀减仓）可能写入 warnings；须在首次引用前初始化
    risk_gate: Dict[str, Any] = {"ok": True, "blocks": [], "warnings": []}
    force_trim_sold: set = set()
    force_trim_cut_in_book = False
    # P3-1：换手预算背包再分配 — 首轮被换手软上限跳过的候选，保留重试上下文，半仓榨干剩余预算
    _turnover_retry_pool: List[dict] = []
    # P3-3：卖出侧涨跌停/停牌跳过记录
    sell_match_skips: List[dict] = []
    # 本轮膨胀减仓砍掉的簿内票，买腿禁止立刻买回
    force_trim_no_rebuy: set = set()

    # 舆情先验：卖/买前一次性批量拉取（gate+scale_holds 时含持仓），禁止循环内 N×串行 AkShare
    from core.paper.rebalance.prefetch import (
        prefetch_sell_quotes_and_breadth,
        prefetch_sentiment_priors,
    )

    prior_by_code, sentiment_prior_summary, prior_cfg_live = prefetch_sentiment_priors(
        holdings=holdings,
        top_items=top_items,
        skip_sentiment_prior=skip_sentiment_prior,
    )

    # P0 · 批量预取行情（避免循环内串行网络往返）
    _sell_codes, _quote_cache, _sector_breadth_by_code = prefetch_sell_quotes_and_breadth(
        holdings,
        batch_query=_batch_query_quotes,
    )
    event_prior_soft_holds: List[dict] = []

    state = RebalanceState(
        paper=paper,
        ranking=list(ranking or []),
        rules=rules if isinstance(rules, dict) else {},
        cost_model=cost_model,
        fee_params=fee_params if isinstance(fee_params, dict) else {},
        top_k=int(top_k),
        min_score=float(min_score),
        min_hold_score=float(min_hold_score),
        max_positions=int(max_positions),
        position_pct=float(position_pct),
        max_turnover_pct=max_turnover_pct,
        respect_max_positions=bool(respect_max_positions),
        skip_sentiment_prior=bool(skip_sentiment_prior),
        skip_market_prior=bool(skip_market_prior),
        tracks_cfg=_tracks_cfg if isinstance(_tracks_cfg, dict) else {},
        indexes=indexes,
        holdings=list(holdings),
        cash=float(cash),
        cash_before=float(cash_before),
        position_count_before=int(position_count_before),
        equity_before=equity_before,
        sell_trades=sell_trades,
        kept=kept,
        turnover_capped=turnover_capped,
        turnover_skipped=turnover_skipped,
        turnover_retry_pool=_turnover_retry_pool,
        risk_gate=risk_gate,
        force_trim_sold=force_trim_sold,
        force_trim_cut_in_book=force_trim_cut_in_book,
        force_trim_no_rebuy=force_trim_no_rebuy,
        sell_match_skips=sell_match_skips,
        event_prior_soft_holds=[],
        prior_by_code=prior_by_code if isinstance(prior_by_code, dict) else {},
        sentiment_prior_summary=sentiment_prior_summary,
        prior_cfg_live=prior_cfg_live if isinstance(prior_cfg_live, dict) else {},
        quote_cache=_quote_cache if isinstance(_quote_cache, dict) else {},
        sector_breadth_by_code=_sector_breadth_by_code if isinstance(_sector_breadth_by_code, dict) else {},
    )

    run_sell_leg(state)

    target_w = apply_post_sell_gate(state)
    run_buy_leg(state, target_w=target_w)

    holdings = state.holdings
    cash = state.cash
    buy_trades = state.buy_trades
    sell_trades = state.sell_trades
    sentiment_restore_trades = state.sentiment_restore_trades
    risk_gate = state.risk_gate
    risk_budget_skips = state.risk_budget_skips
    buys_blocked = state.buys_blocked
    turnover_capped = state.turnover_capped
    turnover_skipped = state.turnover_skipped
    _tau_floor_meta = state.tau_floor_meta
    sell_match_skips = state.sell_match_skips
    event_prior_soft_holds = state.event_prior_soft_holds
    sentiment_prior_summary = state.sentiment_prior_summary

    # 风控拦截时仍给出目标权重建议 → 运维报告 / 返回载荷
    state.holdings = list(holdings)
    state.cash = float(cash)
    state.buy_trades = buy_trades
    state.sell_trades = sell_trades
    state.sentiment_restore_trades = sentiment_restore_trades
    state.risk_gate = risk_gate
    state.risk_budget_skips = risk_budget_skips
    state.buys_blocked = bool(buys_blocked)
    state.turnover_capped = bool(turnover_capped)
    state.turnover_skipped = list(turnover_skipped or [])
    state.tau_floor_meta = _tau_floor_meta if isinstance(_tau_floor_meta, dict) else {}
    state.sell_match_skips = sell_match_skips
    state.event_prior_soft_holds = event_prior_soft_holds
    state.sentiment_prior_summary = sentiment_prior_summary
    return finalize_cross_section_rebalance_report(paper, **state.finalize_kwargs())
