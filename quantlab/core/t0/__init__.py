"""底仓做 T（A 股 T+1：卖旧仓 / 换仓，非无底仓当日买卖）。"""

from core.t0.backtest import backtest_t0_on_bars, derive_t0_quality_metrics
from core.t0.config import DEFAULT_T0_RULES, load_t0_rules
from core.t0.minute_path import simulate_t0_day_minute
from core.t0.rules import (
    atr_pct_from_bars,
    simulate_t0_day,
    simulate_t0_on_holdings,
)

__all__ = [
    "DEFAULT_T0_RULES",
    "load_t0_rules",
    "atr_pct_from_bars",
    "simulate_t0_day",
    "simulate_t0_day_minute",
    "simulate_t0_on_holdings",
    "backtest_t0_on_bars",
    "derive_t0_quality_metrics",
]
