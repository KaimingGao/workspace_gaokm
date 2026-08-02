import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.paper import init_from_example, load_paper
from core.paper_rebalance import simulate_cross_section_rebalance
from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic
from tests.test_p10_quant import _aligned_bars


class TestEquityCurve(unittest.TestCase):
    def test_portfolio_backtest_has_equity_curve(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(result["success"])
        curve = result.get("equity_curve") or []
        self.assertGreaterEqual(len(curve), 2)
        self.assertEqual(curve[0]["equity"], 100.0)
        self.assertIn("date", curve[-1])


class TestWeightDiff(unittest.TestCase):
    def test_format_weight_config_diff(self):
        suggestion = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [
                    {"factor": "momentum", "ic": 0.1, "sample_count": 20},
                    {"factor": "volume_price", "ic": -0.06, "sample_count": 20},
                ],
            },
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "relative_strength": 0.2,
                "volatility": 0.1,
            },
        )
        diff = format_weight_config_diff(suggestion)
        self.assertTrue(diff["success"])
        self.assertIn("patch", diff)
        self.assertIn("weights", diff["patch"])
        self.assertIn("apply_note", diff)


class TestPaperRebalance(unittest.TestCase):
    def test_cross_section_rebalance_sells_and_buys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "600036",
                    "stock_name": "招商银行",
                    "shares": 100,
                    "cost": 30.0,
                    "bought_at": "2026-01-01T10:00:00",
                }
            ]
            paper["cash"] = 50000.0
            # 示例账本 initial_cash=100万，现金改小会触发回撤拦买
            paper["initial_cash"] = 50000.0
            paper["snapshots"] = []

            ranking = [
                {"stock_code": "600519", "stock_name": "茅台", "score": 72},
                {"stock_code": "300750", "stock_name": "宁德", "score": 68},
            ]

            def fake_query(code):
                prices = {"600519": 50.0, "300750": 40.0, "600036": 28.0}
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": prices.get(str(code), 100.0),
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ):
                result = simulate_cross_section_rebalance(paper, ranking, top_k=2)

            self.assertTrue(result["success"])
            self.assertEqual(len(result["sell_trades"]), 1)
            self.assertEqual(result["sell_trades"][0]["stock_code"], "600036")
            self.assertGreaterEqual(len(result["buy_trades"]), 1)
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertIn("600519", held)


if __name__ == "__main__":
    unittest.main()
