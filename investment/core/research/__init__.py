"""Core research helpers (domain layer; no quant imports)."""

import logging

logger = logging.getLogger(__name__)
from core.research.factor_ols_fit import clamp_ridge_lambda, fit_factor_ols_from_panel
from core.research.oos_slim import slim_oos_gate
from core.research.panel import collect_subscore_forward_panel
from core.research.portfolio_bars import load_portfolio_stock_bars, should_fetch_backtest_fundamentals

__all__ = [
    "clamp_ridge_lambda",
    "collect_subscore_forward_panel",
    "fit_factor_ols_from_panel",
    "load_portfolio_stock_bars",
    "should_fetch_backtest_fundamentals",
    "slim_oos_gate",
]
