"""调仓会话状态与评分索引（卖/买腿共享，避免 simulate 局部变量爆炸）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ScoreIndexes:
    """由 ranking / score_lookup 构建的只读索引。"""

    top_items: List[dict]
    top_codes: Set[str]
    item_by_code: Dict[str, dict]
    trade_score_by_code: Dict[str, float]
    eod_score_by_code: Dict[str, float]
    tau_by_code: Dict[str, float]
    hard_reject_by_code: Dict[str, str]

    @property
    def score_by_code(self) -> Dict[str, float]:
        """兼容旧名：卖出主分 = ŷ_trade。"""
        return self.trade_score_by_code


def build_score_indexes(
    ranking: Optional[List[dict]],
    *,
    top_k: int,
    score_lookup: Optional[List[dict]] = None,
) -> ScoreIndexes:
    """构建 TopK / 票级分数索引；同码以 ranking 覆盖 lookup。"""
    top_items = (ranking or [])[:top_k]
    top_codes = {str(x.get("stock_code") or "") for x in top_items if x.get("stock_code")}
    item_by_code: Dict[str, dict] = {}
    for src in list(score_lookup or []) + list(ranking or []):
        code = str(src.get("stock_code") or "")
        if code:
            item_by_code[code] = src

    trade_score_by_code: Dict[str, float] = {}
    eod_score_by_code: Dict[str, float] = {}
    tau_by_code: Dict[str, float] = {}
    hard_reject_by_code: Dict[str, str] = {}

    for src in list(score_lookup or []) + list(ranking or []):
        code = str(src.get("stock_code") or "")
        if not code:
            continue
        if src.get("hard_reject"):
            hard_reject_by_code[code] = str(src.get("reject_reason") or "硬拒绝")
        else:
            hard_reject_by_code.pop(code, None)
        try:
            from core.signal.dual_score import decision_score_for_item
            from core.signal.rebalance_tracks import (
                TRACK_HEURISTIC,
                resolve_score_track,
            )

            if resolve_score_track(src) == TRACK_HEURISTIC:
                from core.signal.rebalance_tracks import heuristic_score_value

                hs = heuristic_score_value(src)
                if hs is not None:
                    trade_score_by_code[code] = float(hs)
            else:
                d_sc = decision_score_for_item(src)
                if d_sc is not None:
                    trade_score_by_code[code] = float(d_sc)
                elif src.get("predicted_score_blend") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score_blend"))
                elif src.get("predicted_score") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score"))
        except (TypeError, ValueError):
            pass
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance_state.py", exc_info=True)
            try:
                from core.signal.rebalance_tracks import (
                    TRACK_HEURISTIC,
                    resolve_score_track,
                )

                if resolve_score_track(src) == TRACK_HEURISTIC:
                    from core.signal.rebalance_tracks import heuristic_score_value

                    hs = heuristic_score_value(src)
                    if hs is not None:
                        trade_score_by_code[code] = float(hs)
                elif src.get("predicted_score") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score"))
            except (TypeError, ValueError):
                pass
        try:
            from core.signal.dual_score import eod_gate_score_for_item
            from core.signal.rebalance_tracks import (
                TRACK_HEURISTIC,
                resolve_score_track,
            )

            if resolve_score_track(src) == TRACK_HEURISTIC:
                from core.signal.rebalance_tracks import heuristic_score_value

                hs = heuristic_score_value(src)
                if hs is not None:
                    eod_score_by_code[code] = float(hs)
            else:
                gate = eod_gate_score_for_item(src)
                if gate is not None:
                    eod_score_by_code[code] = float(gate)
        except (TypeError, ValueError):
            pass
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance_state.py", exc_info=True)
        try:
            from core.signal.dual_score import resolve_predicted_score_tau

            yt = resolve_predicted_score_tau(src)
            if yt is not None:
                tau_by_code[code] = float(yt)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance_state.py", exc_info=True)

    return ScoreIndexes(
        top_items=top_items,
        top_codes=top_codes,
        item_by_code=item_by_code,
        trade_score_by_code=trade_score_by_code,
        eod_score_by_code=eod_score_by_code,
        tau_by_code=tau_by_code,
        hard_reject_by_code=hard_reject_by_code,
    )


@dataclass
class RebalanceState:
    """横截面/分池调仓可变会话：卖腿 → 风控 → 买腿 → finalize 共享。"""

    paper: dict
    ranking: List[dict]
    rules: dict
    cost_model: str
    fee_params: dict
    top_k: int
    min_score: float
    min_hold_score: float
    max_positions: int
    position_pct: float
    max_turnover_pct: Optional[float]
    respect_max_positions: bool
    skip_sentiment_prior: bool
    skip_market_prior: bool
    tracks_cfg: dict
    indexes: ScoreIndexes

    holdings: List[dict] = field(default_factory=list)
    cash: float = 0.0
    cash_before: float = 0.0
    position_count_before: int = 0
    equity_before: Optional[float] = None

    sell_trades: List[dict] = field(default_factory=list)
    buy_trades: List[dict] = field(default_factory=list)
    sentiment_restore_trades: List[dict] = field(default_factory=list)
    kept: List[dict] = field(default_factory=list)

    turnover_capped: bool = False
    turnover_skipped: List[str] = field(default_factory=list)
    turnover_retry_pool: List[dict] = field(default_factory=list)

    risk_gate: Dict[str, Any] = field(
        default_factory=lambda: {"ok": True, "blocks": [], "warnings": []}
    )
    risk_budget_skips: List[dict] = field(default_factory=list)
    sell_match_skips: List[dict] = field(default_factory=list)
    event_prior_soft_holds: List[dict] = field(default_factory=list)

    force_trim_sold: Set[str] = field(default_factory=set)
    force_trim_cut_in_book: bool = False
    force_trim_no_rebuy: Set[str] = field(default_factory=set)

    buys_blocked: bool = False
    tau_floor: Optional[float] = None
    tau_floor_meta: Dict[str, Any] = field(default_factory=dict)

    prior_by_code: Dict[str, Any] = field(default_factory=dict)
    sentiment_prior_summary: Any = None
    prior_cfg_live: Dict[str, Any] = field(default_factory=dict)
    quote_cache: Dict[str, dict] = field(default_factory=dict)
    sector_breadth_by_code: Dict[str, Any] = field(default_factory=dict)
    buy_quote_cache: Dict[str, dict] = field(default_factory=dict)

    mid_summary: Dict[str, Any] = field(default_factory=dict)
    risk_limits: Dict[str, Any] = field(default_factory=dict)

    def finalize_kwargs(self) -> Dict[str, Any]:
        """供 ``finalize_cross_section_rebalance_report`` 解包。"""
        return {
            "ranking": self.ranking,
            "holdings": self.holdings,
            "cash": self.cash,
            "top_codes": self.indexes.top_codes,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "min_hold_score": self.min_hold_score,
            "max_positions": self.max_positions,
            "buys_blocked": self.buys_blocked,
            "risk_gate": self.risk_gate,
            "sell_trades": self.sell_trades,
            "buy_trades": self.buy_trades,
            "sentiment_restore_trades": self.sentiment_restore_trades,
            "turnover_capped": self.turnover_capped,
            "turnover_skipped": self.turnover_skipped,
            "max_turnover_pct": self.max_turnover_pct,
            "sell_match_skips": self.sell_match_skips,
            "cash_before": self.cash_before,
            "position_count_before": self.position_count_before,
            "equity_before": self.equity_before,
            "fee_params": self.fee_params,
            "rules": self.rules,
            "risk_budget_skips": self.risk_budget_skips,
            "skip_market_prior": self.skip_market_prior,
            "sentiment_prior_summary": self.sentiment_prior_summary,
            "event_prior_soft_holds": self.event_prior_soft_holds,
            "respect_max_positions": self.respect_max_positions,
            "tau_floor_meta": self.tau_floor_meta,
        }
