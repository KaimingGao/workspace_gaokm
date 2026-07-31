import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.weight_suggest import suggest_weights_from_ic
from tests.test_signal import _rising_bars


def _aligned_bars(prefix: str, n: int = 40, step: float = 0.4):
    bars = []
    for i in range(n):
        close = 100 + i * step
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 30,
            }
        )
    return bars


class TestPortfolioCrossSection(unittest.TestCase):
    def test_cross_section_portfolio_backtest(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["trade_count"], 1)
        self.assertIn("metrics", result)
        self.assertEqual(result["params"]["top_k"], 2)
        self.assertIn(result["strategy"], ("cross_section_topk", "cross_section_topk_neutral"))
        self.assertGreaterEqual(len(result.get("equity_curve") or []), 2)

    def test_insufficient_common_dates(self):
        a = _rising_bars()[:10]
        b = _rising_bars()
        b[0]["date"] = "2099-01-01"
        result = backtest_topk_equal_weight(
            {"600519": a, "600036": b},
            min_history=8,
        )
        self.assertFalse(result["success"])

    def test_drops_thin_series_instead_of_collapsing_calendar(self):
        stock_bars = {
            "600519": _aligned_bars("a", n=40),
            "600036": _aligned_bars("b", n=40, step=0.35),
            "300750": _aligned_bars("c", n=40, step=0.45),
            "688825": _aligned_bars("thin", n=3),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            neutralize=False,
        )
        self.assertTrue(result["success"], result.get("error"))
        dropped = {d.get("stock_code") for d in (result.get("dropped_stocks") or [])}
        self.assertIn("688825", dropped)
        self.assertGreaterEqual(result.get("params", {}).get("common_dates") or 0, 16)


class TestWeightSuggest(unittest.TestCase):
    def test_suggest_from_positive_ic(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.12, "sample_count": 20},
                {"factor": "volume_price", "ic": -0.08, "sample_count": 20},
                {"factor": "score", "ic": 0.05, "sample_count": 20},
            ],
        }
        out = suggest_weights_from_ic(exp, current_weights={
            "momentum": 0.4,
            "volume_price": 0.3,
            "relative_strength": 0.2,
            "volatility": 0.1,
        })
        self.assertTrue(out["success"])
        self.assertGreater(out["suggested_weights"]["momentum"], out["current_weights"]["momentum"])
        self.assertLess(out["suggested_weights"]["volume_price"], out["current_weights"]["volume_price"])
        self.assertAlmostEqual(sum(out["suggested_weights"].values()), 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
