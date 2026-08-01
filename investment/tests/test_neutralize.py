"""截面中性化核心（合并原 P47/P49）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch
from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config
from core.signal.cross_section import rank_cross_section
from core.signal.neutralize import apply_cross_section_neutralization
from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.config import load_signal_config
from core.signal.cross_section_batch import score_and_rank_watching
from tests.test_p10_quant import _aligned_bars

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
def _item(code: str, score: float, subs: dict) -> dict:
    return {
        "stock_code": code,
        "stock_name": code,
        "score": score,
        "hard_reject": False,
        "sub_scores": subs,
        "factor_contrib": {},
        "regime": {"score_penalty": 0},
    }
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p47_quant.py::TestP47Neutralize ---
class TestP47Neutralize(unittest.TestCase):
    def test_skips_when_sample_too_small(self):
        items = [_item("A", 70, {"momentum": 70})]
        out = apply_cross_section_neutralization(
            items,
            weights={"momentum": 1.0},
            min_samples=3,
        )
        self.assertFalse(out["applied"])
        self.assertEqual(out["items"][0]["score"], 70)

    def test_zscore_boosts_relative_outperformer(self):
        weights = {"momentum": 1.0}
        items = [
            _item("low", 60, {"momentum": 55}),
            _item("mid", 65, {"momentum": 60}),
            _item("high", 70, {"momentum": 75}),
        ]
        out = apply_cross_section_neutralization(items, weights=weights, min_samples=3)
        self.assertTrue(out["applied"])
        by_code = {x["stock_code"]: x for x in out["items"]}
        self.assertGreater(by_code["high"]["score"], by_code["low"]["score"])
        self.assertIn("score_raw", by_code["high"])
        self.assertIn("sub_scores_raw", by_code["high"])

    def test_rank_method_spreads_scores(self):
        weights = {"momentum": 1.0}
        items = [
            _item("a", 50, {"momentum": 40}),
            _item("b", 50, {"momentum": 50}),
            _item("c", 50, {"momentum": 60}),
        ]
        out = apply_cross_section_neutralization(
            items,
            weights=weights,
            method="rank",
            min_samples=3,
        )
        scores = [x["score"] for x in out["items"]]
        self.assertGreater(max(scores), min(scores))

    def test_reapplies_regime_penalty(self):
        weights = {"momentum": 1.0}
        items = [
            _item("a", 70, {"momentum": 60}),
            _item("b", 72, {"momentum": 62}),
            _item("c", 74, {"momentum": 64}),
        ]
        items[2]["regime"] = {"score_penalty": 5}
        out = apply_cross_section_neutralization(items, weights=weights, min_samples=3)
        top = max(out["items"], key=lambda x: x["stock_code"])
        self.assertEqual(top["stock_code"], "c")
        self.assertLess(top["score"], top["sub_scores"]["momentum"])

    def test_default_config_enables_neutralization(self):
        cfg = load_signal_config(reload=True)
        cs = cfg.get("cross_section") or {}
        self.assertTrue(cs.get("neutralize"))
        self.assertIn(cs.get("method"), ("zscore", "rank"))

    def test_cross_section_returns_neutralization_meta(self):
        fake_items = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "score": 70,
                "hard_reject": False,
                "sub_scores": {"momentum": 70, "volume_price": 60},
                "regime": {},
            },
            {
                "stock_code": "600036",
                "stock_name": "招行",
                "score": 68,
                "hard_reject": False,
                "sub_scores": {"momentum": 65, "volume_price": 62},
                "regime": {},
            },
            {
                "stock_code": "601398",
                "stock_name": "工行",
                "score": 66,
                "hard_reject": False,
                "sub_scores": {"momentum": 60, "volume_price": 64},
                "regime": {},
            },
        ]

        def fake_score(raw, **kwargs):
            code = str(raw)
            mapping = {it["stock_code"]: it for it in fake_items}
            for key, item in mapping.items():
                if key in code or item["stock_name"] in code:
                    return {"success": True, "stock_code": item["stock_code"], "signal_item": dict(item)}
            return {"success": True, "stock_code": code, "signal_item": dict(fake_items[0])}

        with patch("core.signal.cross_section.score_stock", side_effect=fake_score):
            result = rank_cross_section(
                ["600519", "600036", "601398"],
                limit=5,
                min_score=40,
            )

        self.assertTrue(result["success"])
        self.assertIn("neutralization", result)
        self.assertTrue(result["neutralization"].get("applied"))
        self.assertIn("截面中性化", result["note"])
        top = result["ranking"][0]
        self.assertIn("score_raw", top)

# --- test_p49_quant.py::TestP49PortfolioNeutralization ---
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
