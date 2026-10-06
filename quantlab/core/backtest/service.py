"""BacktestService：组合/TopK 回测统一出口（A3）。

委托 ``topk_backtest`` / ``engine``；不重写撮合逻辑。router / QuantService 只组装参数。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from core.backtest.types import BacktestResult

logger = logging.getLogger(__name__)

_DEFAULT: Optional["BacktestService"] = None
_METRICS: Dict[str, int] = {
    "topk_ok": 0,
    "topk_fail": 0,
    "signal_ok": 0,
    "signal_fail": 0,
}
_METRICS_LOCK = threading.Lock()


def _bump(key: str, n: int = 1) -> None:
    with _METRICS_LOCK:
        _METRICS[key] = int(_METRICS.get(key) or 0) + int(n)


def metrics_snapshot() -> Dict[str, int]:
    with _METRICS_LOCK:
        return dict(_METRICS)


def reset_metrics() -> None:
    with _METRICS_LOCK:
        for k in list(_METRICS.keys()):
            _METRICS[k] = 0


class BacktestService:
    """生产默认回测口。"""

    def run_topk(
        self,
        stock_bars: Dict[str, List[dict]],
        **kwargs: Any,
    ) -> BacktestResult:
        from core.backtest.topk_backtest import backtest_topk_equal_weight

        try:
            raw = backtest_topk_equal_weight(stock_bars, **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.exception("BacktestService.run_topk failed")
            _bump("topk_fail")
            return BacktestResult.from_topk(
                {"success": False, "ok": False, "error": str(e)}
            )
        result = BacktestResult.from_topk(raw)
        _bump("topk_ok" if result.success else "topk_fail")
        return result

    def run_signal_backtest(self, **kwargs: Any) -> BacktestResult:
        """单票 signal_v1 walk-forward（``core.backtest.engine.backtest_signal_on_bars``）。"""
        from core.backtest.engine import backtest_signal_on_bars

        try:
            raw = backtest_signal_on_bars(**kwargs)
        except Exception as e:  # noqa: BLE001
            logger.exception("BacktestService.run_signal_backtest failed")
            _bump("signal_fail")
            return BacktestResult.from_topk(
                {"success": False, "ok": False, "error": str(e)}
            )
        result = BacktestResult.from_topk(raw if isinstance(raw, dict) else {})
        _bump("signal_ok" if result.success else "signal_fail")
        return result


def get_default_backtest_service() -> BacktestService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = BacktestService()
    return _DEFAULT


def set_default_backtest_service(svc: Optional[BacktestService]) -> None:
    global _DEFAULT
    _DEFAULT = svc
