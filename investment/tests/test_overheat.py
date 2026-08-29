"""过热因子与纸面闸单测。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.factors.meta.registry import registered_factor_names
from core.signal.factors.meta.taxonomy import classify_factor
from core.signal.factors.overheat import score_from_raw, score_overheat
from core.signal.overheat_gate import (
    annotate_item_overheat,
    evaluate_overheat_gate,
    paper_overheat_block,
)
from core.signal.scorer import score_bars
from tests.test_signal import _overheated_bars


class TestOverheatFactor(unittest.TestCase):
    def test_registered_and_risk_family(self):
        self.assertIn("overheat", registered_factor_names())
        self.assertEqual(classify_factor("overheat")["family"], "risk")

    def test_score_drops_when_hot(self):
        cool = score_from_raw(2.0)
        hot = score_from_raw(9.0)
        self.assertGreater(cool, hot)
        s, meta = score_overheat(_overheated_bars())
        self.assertLess(s, 40)
        self.assertTrue(meta.get("ok"))
        self.assertGreaterEqual(float(meta.get("momentum_5d") or 0), 10)

    def test_gate_mom5_hits_paper_threshold(self):
        # 对齐 000938 类：mom5≈9 不触 mom3=15，但触 mom5=10 需略抬；9 用 day+raw
        g = evaluate_overheat_gate(
            mom3=5.0,
            mom5=10.5,
            day_gain=1.0,
            config={"hard_reject": {"mom5_gain_max_pct": 10, "paper_buy_enforce": True}},
        )
        self.assertTrue(g["hit"])
        self.assertIn("近5日", g["reason"])

    def test_annotate_paper_hard_reject_without_production_hard_reject(self):
        item = {
            "stock_code": "000938",
            "predicted_score": 1.07,
            "hard_reject": False,
            "factors": {"momentum_3d": 5.0, "momentum_5d": 10.2, "last_change": 0.5},
        }
        gate = annotate_item_overheat(
            item,
            config={
                "hard_reject": {
                    "mom5_gain_max_pct": 10,
                    "paper_buy_enforce": True,
                    "soft_scale_yhat": False,
                }
            },
        )
        self.assertTrue(gate["hit"])
        self.assertTrue(item.get("paper_hard_reject"))
        self.assertFalse(item.get("hard_reject"))
        self.assertEqual(item.get("predicted_score"), 1.07)
        self.assertIsNotNone(item.get("predicted_score_overheat_scaled"))
        self.assertLess(item["predicted_score_overheat_scaled"], 1.07)

    def test_paper_block_helper(self):
        item = {
            "factors": {"momentum_5d": 11.0, "momentum_3d": 4.0, "last_change": 1.0},
            "predicted_score": 1.5,
        }
        blocked, reason = paper_overheat_block(
            item,
            config={"hard_reject": {"mom5_gain_max_pct": 10, "paper_buy_enforce": True}},
        )
        self.assertTrue(blocked)
        self.assertTrue(reason)

    def test_score_bars_production_path_keeps_overheat_meta(self):
        # 生产同款：mom3_hard_reject=False 不掐死，但仍标注
        result = score_bars(_overheated_bars(), horizon_days=1, mom3_hard_reject=False)
        self.assertFalse(result.get("hard_reject"))
        self.assertTrue(result.get("paper_hard_reject") or result.get("mom3_chase_risk"))
        self.assertIn("overheat", result.get("sub_scores") or {})


if __name__ == "__main__":
    unittest.main()
