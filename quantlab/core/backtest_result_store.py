"""Persist last product / T0 backtest so /replay and /follow can restore after refresh.

薄 re-export：实现见 ``core.persistence.result_snapshot``。
"""

from core.persistence.result_snapshot import (  # noqa: F401
    load_last_portfolio_backtest,
    load_last_t0_backtest,
    save_last_portfolio_backtest,
    save_last_t0_backtest,
    slim_portfolio_backtest_result,
)

__all__ = [
    "load_last_portfolio_backtest",
    "load_last_t0_backtest",
    "save_last_portfolio_backtest",
    "save_last_t0_backtest",
    "slim_portfolio_backtest_result",
]
