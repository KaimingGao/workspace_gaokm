import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.portfolio_neutral_compare import compare_portfolio_neutralization
from tests.test_p10_quant import _aligned_bars


class TestP52PortfolioNeutralCompare(unittest.TestCase):
    def test_compare_api_shape(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        out = compare_portfolio_neutralization(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(out["success"])
        self.assertIn("neutralized", out)
        self.assertIn("absolute", out)
        self.assertIn("total_return_pct", out["delta"])

    def test_compare_with_fundamentals_count(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        fundamentals = {"600519": {"pe": 18.0, "roe": 15.0}}
        out = compare_portfolio_neutralization(
            stock_bars,
            fundamentals_by_code=fundamentals,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["fundamentals_count"], 1)


class TestP52NeutralCompareApi(unittest.TestCase):
    def test_portfolio_neutral_compare_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock_out = {
            "success": True,
            "winner": "neutralized",
            "delta": {"total_return_pct": 1.2},
            "neutralized": {"success": True, "metrics": {"total_return_pct": 5.0}, "equity_curve": []},
            "absolute": {"success": True, "metrics": {"total_return_pct": 3.8}, "equity_curve": []},
            "fundamentals_count": 2,
        }
        with patch.object(deps.quant, "run_portfolio_neutral_compare", return_value=mock_out):
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/portfolio-neutral-compare",
                json={"top_k": 2, "horizon_days": 3, "min_score": 40},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertIn("neutralized", res.json())


if __name__ == "__main__":
    unittest.main()
