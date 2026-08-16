"""双轨调仓：predicted / heuristic + OOS 分流（失败组禁买、持仓按 H 留卖）。"""

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

    def test_buy_gate_blocks_oos_failed_even_high_heuristic(self):
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
        self.assertEqual(track, "heuristic")
        self.assertEqual(reason, "oos_failed_no_buy")
        self.assertAlmostEqual(gate or 0.0, 90.0)

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
        self.assertIn("ŷ_EOD", reason2 or "")

    def test_oos_fail_hold_uses_heuristic_threshold(self):
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
        # H=40 ≤ 45 → 卖
        sell, sc, floor, track = hold_decision_for_item(
            {
                "return_model_source": "oos_failed_heuristic",
                "heuristic_score": 40.0,
                "oos_blocked": True,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertTrue(sell)
        self.assertEqual(track, "heuristic")
        self.assertAlmostEqual(floor, 45.0)
        self.assertAlmostEqual(sc, 40.0)

        # H=50 > 45 → 保持
        sell2, sc2, _, track2 = hold_decision_for_item(
            {
                "return_model_source": "oos_failed_global",
                "cluster_label": "G_bad",
                "heuristic_score": 50.0,
                "predicted_score": 2.0,
                "oos_blocked": True,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertFalse(sell2)
        self.assertEqual(track2, "heuristic")
        self.assertAlmostEqual(sc2, 50.0)

        # 无 heuristic → 保守卖出
        sell3, _, _, track3 = hold_decision_for_item(
            {
                "return_model_source": "oos_failed_global",
                "oos_blocked": True,
                "predicted_score": 3.0,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertTrue(sell3)
        self.assertEqual(track3, "heuristic")

        # 非 OOS：仍走 ŷ hold
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

    def test_heuristic_gates_ignore_yhat_score_field(self):
        """表列 score 已是 ŷ% 时，OOS 失败持仓闸仍只读 heuristic_score。"""
        from core.signal.rebalance_tracks import hold_decision_for_item

        cfg = {
            "enabled": True,
            "oos_fail_policy": "exclude",
            "heuristic_hold_floor": 45.0,
            "predicted_hold_floor": 0.0,
        }
        sell, sc, _, track = hold_decision_for_item(
            {
                "oos_blocked": True,
                "return_model_source": "oos_failed_heuristic",
                "score": 0.8,  # ŷ% 表列
                "heuristic_score": 50.0,
            },
            tracks_cfg=cfg,
            predicted_hold_floor=0.0,
        )
        self.assertFalse(sell)
        self.assertEqual(track, "heuristic")
        self.assertAlmostEqual(sc, 50.0)


if __name__ == "__main__":
    unittest.main()
