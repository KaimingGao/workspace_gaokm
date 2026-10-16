"""持久化门面：bars / JSON 文档 / 回测结果快照。"""

from core.persistence.bars import (
    bars_backend,
    load_daily_bars,
    load_daily_cache,
    save_daily_bars,
    save_daily_cache,
)
from core.persistence.json_doc import atomic_write_json, read_json_file, read_json_object
from core.persistence.result_snapshot import (
    load_last_portfolio_backtest,
    load_last_t0_backtest,
    save_last_portfolio_backtest,
    save_last_t0_backtest,
    slim_portfolio_backtest_result,
)

__all__ = [
    "atomic_write_json",
    "bars_backend",
    "load_daily_bars",
    "load_daily_cache",
    "load_last_portfolio_backtest",
    "load_last_t0_backtest",
    "read_json_file",
    "read_json_object",
    "save_daily_bars",
    "save_daily_cache",
    "save_last_portfolio_backtest",
    "save_last_t0_backtest",
    "slim_portfolio_backtest_result",
]
