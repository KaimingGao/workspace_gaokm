"""双轨调仓：predicted 轨资格；分组 OOS 分流已下线。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestRebalanceTracks(unittest.TestCase):
    def test_classify_oos_fail_always_skip(self):
        from core.signal.rebalance_tracks import (
            classify_oos_fail_book_path,
            get_rebalance_tracks_cfg,
            sleeve_policy_allows,
        )

        for pol in ("exclude", "heuristic_sleeve", "predicted_degrade"):
            cfg = get_rebalance_tracks_cfg(
                {"cluster_scoring": {"rebalance_tracks": {"oos_fail_policy": pol}}}
            )
            self.assertEqual(
                classify_oos_fail_book_path(
                    {
                        "return_model_source": "oos_failed_heuristic",
                        "heuristic_score": 90.0,
                    },
                    tracks_cfg=cfg,
                ),
                "skip",
            )
        self.assertFalse(sleeve_policy_allows())

    def test_resolve_oos_status_always_pass(self):
        from core.signal.rebalance_tracks import OOS_PASS, resolve_oos_status

        self.assertEqual(
            resolve_oos_status(
                {
                    "cluster_label": "G_bad",
                    "return_model_source": "oos_failed_heuristic",
                    "oos_blocked": True,
                }
            ),
            OOS_PASS,
        )

    def test_buy_gate_uses_predicted_for_all_codes(self):
        from core.signal.rebalance_tracks import buy_gate_for_item

        cfg = {
            "enabled": True,
            "oos_fail_policy": "exclude",
            "heuristic_buy_floor": 55.0,
            "predicted_buy_floor": 0.6,
            "heuristic_hold_floor": 45.0,
            "heuristic_sleeve_max": 0,
            "heuristic_apply_tau_gate": False,
        }
        # 旧 OOS 失败 heuristic 票：无 ŷ → 缺 predicted，不再因 oos 禁买
        ok, gate, track, reason = buy_gate_for_item(
            {
                "score_track": "heuristic",
                "score_scale": "heuristic_0_100",
                "return_model_source": "oos_failed_heuristic",
                "heuristic_score": 90.0,
                "score": 90.0,
            },
            tracks_cfg=cfg,
            predicted_buy_floor=0.6,
        )
        self.assertFalse(ok)
        self.assertEqual(track, "predicted")
        self.assertEqual(reason, "missing_predicted_eod")
        self.assertIsNone(gate)

        ok2, _, track2, reason2 = buy_gate_for_item(
            {
                "score_track": "predicted",
                "predicted_score": 0.2,
                "predicted_score_eod": 0.2,
            },
            tracks_cfg=cfg,
            predicted_buy_floor=0.6,
        )
        self.assertFalse(ok2)
        self.assertEqual(track2, "predicted")
        self.assertIn("ŷ_oo", reason2 or "")

        ok3, gate3, track3, reason3 = buy_gate_for_item(
            {
                "cluster_label": "G_bad",
                "oos_blocked": True,
                "predicted_score": 1.2,
                "predicted_score_eod": 1.2,
            },
            tracks_cfg=cfg,
            predicted_buy_floor=0.6,
        )
        self.assertTrue(ok3)
        self.assertEqual(track3, "predicted")
        self.assertIsNone(reason3)
        self.assertAlmostEqual(gate3 or 0.0, 1.2)

    def test_hold_uses_predicted_track(self):
        from core.signal.rebalance_tracks import hold_decision_for_item

        cfg = {
            "enabled": True,
            "oos_fail_policy": "exclude",
            "heuristic_buy_floor": 55.0,
            "heuristic_hold_floor": 45.0,
            "predicted_hold_floor": 0.0,
            "heuristic_sleeve_max": 0,
            "heuristic_apply_tau_gate": False,
        }
        # 有全局 ŷ=2.0 → predicted hold（floor=0）保持
        sell2, sc2, floor2, track2 = hold_decision_for_item(
            {
                "return_model_source": "oos_failed_global",
                "cluster_label": "G_bad",
                "heuristic_score": 50.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "oos_blocked": True,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertFalse(sell2)
        self.assertEqual(track2, "predicted")
        self.assertAlmostEqual(floor2, 0.0)
        self.assertAlmostEqual(sc2, 2.0)

        # 全局 ŷ 低于 hold → 卖
        sell_low, sc_low, _, track_low = hold_decision_for_item(
            {
                "return_model_source": "global",
                "predicted_score": -0.5,
                "predicted_score_eod": -0.5,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertTrue(sell_low)
        self.assertEqual(track_low, "predicted")
        self.assertAlmostEqual(sc_low, -0.5)

        # predicted blend 低于 hold → 卖
        sell4, _, _, track4 = hold_decision_for_item(
            {
                "score_track": "predicted",
                "predicted_score_blend": -0.5,
                "predicted_score": 1.0,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertTrue(sell4)
        self.assertEqual(track4, "predicted")


class TestScaleNotNumericRange(unittest.TestCase):
    def test_chi_next_yhat_not_heuristic(self):
        from core.signal.rebalance_tracks import (
            heuristic_score_value,
            table_yhat_score_value,
            hold_decision_for_item,
        )

        item = {
            "score": 21.0,
            "predicted_score": 21.0,
            "predicted_score_eod": 21.0,
            "score_scale": "predicted_yhat",
        }
        self.assertIsNone(heuristic_score_value(item))
        self.assertAlmostEqual(table_yhat_score_value(item), 21.0)
        sell, sc, _, track = hold_decision_for_item(
            item, tracks_cfg={"heuristic_hold_floor": 45.0, "predicted_hold_floor": -1.0}
        )
        self.assertFalse(sell)
        self.assertEqual(track, "predicted")
        self.assertAlmostEqual(sc, 21.0)

    def test_former_oos_global_yhat_uses_predicted_hold(self):
        from core.signal.rebalance_tracks import hold_decision_for_item

        sell, sc, floor, track = hold_decision_for_item(
            {
                "oos_blocked": True,
                "return_model_source": "oos_failed_global",
                "score": 21.0,
                "predicted_score": 21.0,
                "predicted_score_eod": 21.0,
                "score_scale": "predicted_yhat",
            },
            tracks_cfg={"heuristic_hold_floor": 45.0, "predicted_hold_floor": -1.0},
            predicted_hold_floor=-1.0,
        )
        self.assertFalse(sell)
        self.assertEqual(track, "predicted")
        self.assertAlmostEqual(sc, 21.0)
        self.assertAlmostEqual(floor, -1.0)


if __name__ == "__main__":
    unittest.main()
