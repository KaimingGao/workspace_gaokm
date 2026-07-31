import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic
from quant.research.portfolio_data import summarize_portfolio_backtest
from tests.test_p10_quant import _aligned_bars


class TestPortfolioDailySummary(unittest.TestCase):
    def test_summarize_offline(self):
        stock_bars = {"600519": _aligned_bars("a"), "600036": _aligned_bars("b")}
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ):
            out = summarize_portfolio_backtest(codes=["600519", "600036"], min_score=40)
        self.assertTrue(out["success"])
        self.assertIn("total_return_pct", out)
        self.assertIn("equity_curve_tail", out)


class TestWeightPreview(unittest.TestCase):
    def test_config_diff_for_preview(self):
        suggestion = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [{"factor": "momentum", "ic": 0.09, "sample_count": 20}],
            }
        )
        diff = format_weight_config_diff(suggestion)
        self.assertTrue(diff.get("success"))
        self.assertIn("weights", diff.get("patch") or {})


if __name__ == "__main__":
    unittest.main()
