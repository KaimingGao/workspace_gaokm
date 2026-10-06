"""纸面调仓工具再导出。

Follow / 日报 ``paper.rebalance()`` 走观察池 ``rank_lots``。
日循环 ``/api/paper/run`` 走 ``orchestrator`` 的持仓规则。
横截面 TopK 调仓链（``simulate_cross_section_rebalance`` / λ 闸）已删除。
"""

from __future__ import annotations

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
from core.paper.rebalance.path_matrix import (  # noqa: F401 — facade re-export
    get_path_matrix_cfg,
)
from core.paper.rebalance.turnover import (  # noqa: F401 — facade re-export
    build_rebalance_cash_impact,
    clip_shares_to_turnover_budget,
    compute_turnover_stats,
    resolve_buy_turnover_budget,
)

__all__ = [
    "_batch_query_quotes",
    "_buy_match_block_reason",
    "_sell_match_block_reason",
    "attach_change_pct_to_rebalance_report",
    "build_rebalance_cash_impact",
    "clip_shares_to_turnover_budget",
    "compute_turnover_stats",
    "get_path_matrix_cfg",
    "quote_change_pct",
    "resolve_buy_turnover_budget",
    "select_force_trim_codes",
    "select_force_trim_codes_sellable",
]
