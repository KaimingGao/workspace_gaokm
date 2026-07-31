import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.config import load_signal_config
from core.signal.cross_section_batch import score_and_rank_watching
from tests.test_p10_quant import _aligned_bars


class TestP49PortfolioNeutralization(unittest.TestCase):
    def test_portfolio_backtest_uses_neutralization_by_default(self):
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
        self.assertEqual(result["strategy"], "cross_section_topk_neutral")
        self.assertTrue(result["params"]["neutralize"])
        self.assertGreaterEqual(result["params"]["neutralized_rebalances"], 1)

    def test_portfolio_backtest_can_disable_neutralization(self):
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
            neutralize=False,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["strategy"], "cross_section_topk")
        self.assertEqual(result["params"]["neutralized_rebalances"], 0)

    def test_score_and_rank_changes_relative_order(self):
        cfg = load_signal_config()
        cfg = {**cfg, "weights": {"momentum": 1.0}}
        entries = [
            {
                "stock_code": "A",
                "score": 72.0,
                "sub_scores": {"momentum": 70.0},
                "regime": {},
            },
            {
                "stock_code": "B",
                "score": 68.0,
                "sub_scores": {"momentum": 85.0},
                "regime": {},
            },
            {
                "stock_code": "C",
                "score": 66.0,
                "sub_scores": {"momentum": 60.0},
                "regime": {},
            },
        ]
        raw_rank, _ = score_and_rank_watching(
            entries, min_score=0, neutralize=False, config=cfg
        )
        neu_rank, meta = score_and_rank_watching(
            entries, min_score=0, neutralize=True, config=cfg
        )
        self.assertTrue(meta["applied"])
        self.assertEqual(raw_rank[0][0], "A")
        self.assertEqual(neu_rank[0][0], "B")


if __name__ == "__main__":
    unittest.main()
