"""模拟账户领域包（内部 canonical 名 paper；产品对外称「模拟」）。

对外稳定入口：``from core.paper import load_paper, mark_to_market, …``
子模块：``ledger`` · ``exec`` · ``cycle`` · ``costs`` · ``sizing`` · ``rebalance``
"""

from core.paper.costs import resolve_cost_model
from core.paper.cycle import run_daily_cycle
from core.paper.exec import (
    manual_buy,
    manual_sell,
    mark_to_market,
    simulate_buys,
    simulate_sells,
)
from core.paper.tplus1 import (  # noqa: F401 — 再导出供测试 / 调用方
    TPLUS1_LOCK_REASON,
    sellable_shares,
)
from core.paper.ledger import (
    DEFAULT_PAPER_PATH,
    EXAMPLE_PATH,
    MAX_OPERATION_LOG,
    MAX_SIGNAL_LOG,
    MAX_SNAPSHOTS,
    MAX_TRADES,
    OPERATION_LOG_TYPES,
    ORIGIN_LABELS,
    ORIGIN_MANUAL,
    ORIGIN_MIXED,
    ORIGIN_STRATEGY,
    _now_iso,
    _quote_price,
    append_operation_log,
    append_snapshot,
    append_trade_legs_to_operation_log,
    build_ops_report,
    capture_mark_snapshot,
    holding_codes,
    init_from_example,
    load_paper,
    merge_origin,
    mutate_paper,
    paper_write_lock,
    run_signal_scan,
    save_paper,
    snapshots_for_ui,
    trim_paper_lists,
)
from core.paper.sizing import (
    DEFAULT_SYNC_AMOUNT,
    DEFAULT_SYNC_LOT_SHARES,
    buy_codes_direct,
    plan_buy_codes,
)

__all__ = [
    "DEFAULT_PAPER_PATH",
    "DEFAULT_SYNC_AMOUNT",
    "DEFAULT_SYNC_LOT_SHARES",
    "EXAMPLE_PATH",
    "MAX_OPERATION_LOG",
    "MAX_SIGNAL_LOG",
    "MAX_SNAPSHOTS",
    "MAX_TRADES",
    "OPERATION_LOG_TYPES",
    "ORIGIN_LABELS",
    "ORIGIN_MANUAL",
    "ORIGIN_MIXED",
    "ORIGIN_STRATEGY",
    "_now_iso",
    "_quote_price",
    "append_operation_log",
    "append_snapshot",
    "append_trade_legs_to_operation_log",
    "build_ops_report",
    "buy_codes_direct",
    "capture_mark_snapshot",
    "holding_codes",
    "init_from_example",
    "load_paper",
    "manual_buy",
    "manual_sell",
    "mark_to_market",
    "merge_origin",
    "mutate_paper",
    "paper_write_lock",
    "plan_buy_codes",
    "resolve_cost_model",
    "run_daily_cycle",
    "run_signal_scan",
    "save_paper",
    "simulate_buys",
    "simulate_sells",
    "snapshots_for_ui",
    "sellable_shares",
    "TPLUS1_LOCK_REASON",
    "trim_paper_lists",
]
