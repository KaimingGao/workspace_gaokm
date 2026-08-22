"""薄 BacktestService 门面（Domain Facade · BS）：上层回测窗口。

委托 core.backtest.BacktestService。文档称 Domain Facade，与 Application Service 不同层。
历史兼容：本模块函数仍返回 dict（``.as_dict()``）。
类型化 API：``from core.backtest.service import BacktestService, BacktestResult``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.backtest.service import (
    get_default_backtest_service,
    metrics_snapshot,
    reset_metrics,
    set_default_backtest_service,
)
from core.backtest.types import BacktestResult

__all__ = [
    "BacktestResult",
    "metrics_snapshot",
    "reset_metrics",
    "run_signal_backtest",
    "run_topk",
    "set_default_backtest_service",
]


def run_topk(
    stock_bars: Dict[str, List[dict]],
    **kw: Any,
) -> Dict[str, Any]:
    return get_default_backtest_service().run_topk(stock_bars, **kw).as_dict()


def run_signal_backtest(**kw: Any) -> Dict[str, Any]:
    return get_default_backtest_service().run_signal_backtest(**kw).as_dict()
