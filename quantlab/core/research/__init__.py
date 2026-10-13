"""Core research helpers (domain layer; no quant imports)."""

from core.research.factor_ols_fit import clamp_ridge_lambda, fit_factor_ols_from_panel
from core.research.oo_panel import collect_oo_panel
from core.research.portfolio_bars import load_portfolio_stock_bars, should_fetch_backtest_fundamentals

# 旧名兼容（= collect_oo_panel）
collect_subscore_forward_panel = collect_oo_panel

__all__ = [
    "clamp_ridge_lambda",
    "collect_oo_panel",
    "collect_subscore_forward_panel",
    "fit_factor_ols_from_panel",
    "load_portfolio_stock_bars",
    "should_fetch_backtest_fundamentals",
]
