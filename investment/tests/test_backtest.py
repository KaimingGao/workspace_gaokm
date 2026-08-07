import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.engine import backtest_signal_on_bars
from core.backtest.strategies import list_strategies, run_strategy_backtest
from skills.backtest.handler import BacktestHandler


def _rising_bars(n=40, start=100.0, step=0.5):
    bars = []
    price = start
    for i in range(n):
        price += step
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": price - 0.2,
                "high": price + 0.3,
                "low": price - 0.3,
                "close": price,
                "volume": 1000 + i * 20,
            }
        )
    return bars


class TestBacktestEngine(unittest.TestCase):
    def test_backtest_rising_series_produces_trades(self):
        bars = _rising_bars(50)
        result = backtest_signal_on_bars(
            bars, horizon_days=3, min_score=50.0, min_history=10
        )
        self.assertTrue(result["success"])
        self.assertGreater(result["metrics"]["trade_count"], 0)
        self.assertIsNotNone(result["metrics"]["win_rate_pct"])
        self.assertIn("benchmark", result)
        self.assertIn("score_buckets", result)
        self.assertIsNotNone(result["benchmark"].get("buy_hold_period_pct"))

    def test_stratify_and_scan(self):
        from core.backtest.engine import scan_signal_parameters, stratify_by_score

        bars = _rising_bars(50)
        result = backtest_signal_on_bars(
            bars, horizon_days=3, min_score=50.0, min_history=10
        )
        buckets = stratify_by_score(
            [
                {"score": 60, "return_pct": 1.0},
                {"score": 70, "return_pct": 2.0},
                {"score": 80, "return_pct": -1.0},
            ]
        )
        self.assertGreaterEqual(len(buckets), 2)
        scan = scan_signal_parameters(
            bars,
            min_scores=[50.0, 55.0],
            horizon_days_list=[2, 3],
        )
        self.assertGreaterEqual(len(scan), 2)
        self.assertIn("total_return_pct", scan[0])

    def test_backtest_insufficient_bars(self):
        result = backtest_signal_on_bars(_rising_bars(8), horizon_days=3)
        self.assertFalse(result["success"])
        self.assertIn("日线不足", result["error"])

    def test_strategy_registry(self):
        bars = _rising_bars(50)
        names = [s["name"] for s in list_strategies()]
        self.assertIn("short", names)
        self.assertIn("short_conservative", names)
        conservative = run_strategy_backtest(
            bars, "short_conservative", min_history=10
        )
        self.assertTrue(conservative.get("success"))
        self.assertEqual(conservative.get("strategy"), "short_conservative")

    def test_handler_mocked_fetch(self):
        from unittest.mock import patch

        bars = _rising_bars(45)
        with patch(
            "skills.backtest.engine.get_bars",
            return_value={"bars": bars, "data_source": "mock_daily"},
        ), patch(
            "skills.backtest.engine.fetch_index_bars",
            return_value=([], ""),
        ), patch(
            "skills.backtest.engine.get_quote",
            return_value={
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "CN",
            },
        ), patch(
            "skills.backtest.engine.resolve_market_code",
            return_value=("CN", "600519"),
        ):
            raw = BacktestHandler().execute(
                {
                    "name": "backtest",
                    "parameters": {
                        "stock_codes": ["茅台"],
                        "lookback_days": 40,
                        "min_score": 50,
                    },
                }
            )
        data = json.loads(raw)
        self.assertTrue(data["success"])
        self.assertEqual(data["strategy"], "short")
        self.assertEqual(len(data["results"]), 1)
        self.assertTrue(data["results"][0]["success"])


if __name__ == "__main__":
    unittest.main()
