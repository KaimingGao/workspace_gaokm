"""选股门槛：默认 ŷ≥+1%；卖出 ŷ<-1%；显式 null 才关闭。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestSelectionFloor(unittest.TestCase):
    def test_default_buy_hold_floors_when_keys_missing(self):
        from core.signal.score_display import (
            resolve_hold_floor,
            selection_min_score,
        )

        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"rank_mode": "predicted_score"}},
        ):
            self.assertEqual(selection_min_score(), 1.0)
            self.assertEqual(resolve_hold_floor(), -1.0)

    def test_explicit_null_disables(self):
        from core.signal.score_display import selection_min_score, resolve_buy_floor

        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"min_predicted_score": None}},
        ):
            self.assertIsNone(selection_min_score())
            self.assertEqual(resolve_buy_floor(), float("-inf"))

    def test_configured_value(self):
        from core.signal.score_display import selection_min_score

        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"min_predicted_score": 0.3}},
        ):
            self.assertEqual(selection_min_score(), 0.3)

    def test_legacy_heuristic_optimize_floor_ignored(self):
        from core.signal.score_display import (
            looks_like_legacy_heuristic_score,
            resolve_buy_floor,
            resolve_optimize_score_floor,
        )

        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"min_predicted_score": 1.0}},
        ):
            self.assertTrue(looks_like_legacy_heuristic_score(55.0))
            self.assertFalse(looks_like_legacy_heuristic_score(1.0))
            self.assertEqual(resolve_optimize_score_floor(None), 1.0)
            self.assertEqual(resolve_optimize_score_floor(55.0), 1.0)
            self.assertEqual(resolve_optimize_score_floor(0.5), 0.5)
            self.assertEqual(resolve_buy_floor(explicit=55.0), 1.0)

    def test_annotate_score_gate_uses_eod_raw_when_item(self):
        from core.signal.score_display import annotate_score_gate

        item = {
            "predicted_score": 1.5,
            "predicted_score_cal": 0.2,
            "score_calibration_enabled": True,
            # applied 恒 False；即便恶意标 True，闸仍应读 raw EOD
            "score_calibration_applied": True,
        }
        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"min_predicted_score": 0.35}},
        ), patch(
            "core.signal.dual_score.eod_gate_score_for_item",
            return_value=1.5,
        ):
            gate = annotate_score_gate(1.5, min_score=0.35, item=item)
            self.assertFalse(gate["below_min_score"])
            self.assertAlmostEqual(gate["gate_score"], 1.5)
            gate_low = annotate_score_gate(0.2, min_score=0.35)
            self.assertTrue(gate_low["below_min_score"])


if __name__ == "__main__":
    unittest.main()
