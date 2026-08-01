"""回测 fundamentals 接线（原 P51 前半）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch
from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.config import load_signal_config
from core.signal.cross_section_batch import score_window_as_item
from core.signal.fundamentals_bridge import fetch_fundamentals_batch
from quant.research.portfolio_data import load_portfolio_stock_bars
from quant.research.portfolio_neutral_compare import compare_portfolio_neutralization
from tests.test_p10_quant import _aligned_bars

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- p51_fund.py::TestP51BacktestFundamentals ---
class TestP51BacktestFundamentals(unittest.TestCase):
    def test_config_enables_backtest_fundamentals(self):
        cfg = load_signal_config(reload=True)
        self.assertTrue(cfg.get("fundamentals", {}).get("use_in_backtest"))
    def test_fetch_fundamentals_batch(self):
        with patch(
            "core.signal.fundamentals_bridge.fetch_score_fundamentals",
            side_effect=lambda c, as_of=None: (
                {"pe": 15.0, "pb": 1.2} if c == "600519" else None
            ),
        ):
            out = fetch_fundamentals_batch(["600519", "600036"])
        self.assertIn("600519", out)
        self.assertNotIn("600036", out)
    def test_backtest_records_fundamentals_count(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        fundamentals = {
            "600519": {"pe": 18.0, "pb": 1.5, "roe": 20.0},
            "600036": {"pe": 6.0, "pb": 0.8},
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
            fundamentals_by_code=fundamentals,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["params"]["fundamentals_used"])
        self.assertEqual(result["params"]["fundamentals_count"], 2)
    def test_score_window_uses_fundamentals_for_value(self):
        bars = _aligned_bars("a")
        item = score_window_as_item(
            "600519",
            bars,
            horizon_days=3,
            quote={"change_raw": 1.0, "price_raw": bars[-1]["close"]},
            fundamentals={"pe": 16.0, "pb": 1.2, "roe": 18.0},
        )
        self.assertIsNotNone(item)
        self.assertGreater(item["sub_scores"].get("value", 50), 50)
    @patch("quant.research.portfolio_data.fetch_fundamentals_batch")
    @patch("skills.common.history.fetch_daily_bars")
    @patch("skills.common.quote_api.StockAPI.query")
    def test_load_portfolio_attaches_fundamentals(self, mock_quote, mock_bars, mock_fund):
        mock_quote.side_effect = lambda raw: {
            "success": True,
            "stock_code": str(raw),
        }
        mock_bars.side_effect = lambda raw, limit=155, **kwargs: (
            _aligned_bars(str(raw)[:3]),
            "mock",
        )
        mock_fund.return_value = {"600519": {"pe": 20.0}, "600036": {"pe": 8.0}}
        stock_bars, _failures, fund = load_portfolio_stock_bars(
            ["600519", "600036"],
            lookback=120,
            fetch_fundamentals=True,
        )
        self.assertEqual(len(stock_bars), 2)
        mock_fund.assert_called_once()
        self.assertEqual(len(fund), 2)


if __name__ == "__main__":
    unittest.main()
