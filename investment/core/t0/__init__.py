"""做 T（T+0 底仓）规则与日线代理回测。"""

import logging

logger = logging.getLogger(__name__)
from core.t0.backtest import backtest_t0_on_bars, derive_t0_quality_metrics
from core.t0.config import DEFAULT_T0_RULES, load_t0_rules
from core.t0.minute_path import simulate_t0_day_minute
from core.t0.rules import (
    atr_pct_from_bars,
    choose_direction,
    resolve_direction,
    score_t0_direction,
    simulate_t0_day,
    simulate_t0_on_holdings,
)

__all__ = [
    "DEFAULT_T0_RULES",
    "load_t0_rules",
    "atr_pct_from_bars",
    "choose_direction",
    "resolve_direction",
    "score_t0_direction",
    "simulate_t0_day",
    "simulate_t0_day_minute",
    "simulate_t0_on_holdings",
    "backtest_t0_on_bars",
    "derive_t0_quality_metrics",
]
