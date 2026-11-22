"""Live 执行方案跟历史回测页：股票池 + 工厂执行套。"""

import unittest
from unittest.mock import patch

from core.paper.rebalance.live_plan import (
    factory_heads_stamped,
    live_execution_codes,
    live_feature_set,
    stamp_live_execution_heads,
)
from core.paper.rebalance.path_matrix import get_path_matrix_cfg


class LivePlanTests(unittest.TestCase):
    def test_default_pool_is_hs300(self):
        self.assertEqual(live_feature_set({}), "hs300")
        cfg = get_path_matrix_cfg({})
        self.assertEqual(cfg["feature_set"], "hs300")

    def test_saved_pool_is_the_live_universe(self):
        paper = {
            "rules": {
                "execution": {
                    "rebalance_timing": {"rank_lots": {"feature_set": "pool"}},
                }
            }
        }
        self.assertEqual(live_feature_set(paper), "pool")
        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=["600000", "600001"],
        ):
            codes, meta = live_execution_codes(paper, include_held=["000001"])
        self.assertEqual(codes[:2], ["600000", "600001"])
        self.assertIn("000001", codes)
        self.assertEqual(meta["feature_set"], "pool")
        self.assertEqual(meta["n_held_extra"], 1)

    def test_stamp_uses_execution_suite(self):
        items = [
            {"stock_code": "600000", "y_oo": 0.1},
            {"stock_code": "600001", "y_oo": 0.2},
        ]
        table = {
            "heads": ["oo"],
            "suite": "live",
            "runs": {"oo": "v1"},
            "cards": {"oo": {"factory": "T", "run_id": "v1"}},
            "by_day": {
                "2026-11-21": {
                    "600000": {"y_oo": 0.01},
                    "600001": {"y_oo": 0.02},
                }
            },
        }
        with patch(
            "core.paper.rebalance.live_plan._execution_arm_table",
            return_value=table,
        ) as load:
            ok = stamp_live_execution_heads(items, day="2026-11-21", paper={})
        self.assertTrue(ok)
        load.assert_called_once()
        self.assertEqual(items[0]["y_oo"], 0.01)
        self.assertEqual(items[0]["y_oo_source"], "transformer")
        self.assertTrue(factory_heads_stamped(items))

    def test_missing_execution_suite_keeps_existing_scores(self):
        items = [
            {"stock_code": "600000", "y_oo": 0.1},
            {"stock_code": "600001", "y_oo": 0.2},
        ]
        with patch(
            "core.paper.rebalance.live_plan._execution_arm_table",
            return_value={},
        ):
            ok = stamp_live_execution_heads(items, day="2026-11-21", paper={})
        self.assertFalse(ok)
        self.assertEqual(items[0]["y_oo"], 0.1)


if __name__ == "__main__":
    unittest.main()
