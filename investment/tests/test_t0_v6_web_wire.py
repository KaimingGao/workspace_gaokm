"""v6 收盘带宽：Web schema / patch / 表单键接线。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.execution import validate_execution_patch
from core.t0.config import load_t0_rules
from web.schemas.paper import PaperExecutionPatchRequest, T0BacktestRequest


class TestT0V6WebWire(unittest.TestCase):
    def test_backtest_schema_accepts_v6_close_band_keys(self):
        req = T0BacktestRequest(
            t0_close_band_delta_pct=0.6,
            t0_round_ratio=0.2,
            t0_max_position_pct=1.0,
            t0_slots_max_rounds=5,
            t0_slots_enabled=True,
            y_tau_require_for_leg1=False,
        )
        dumped = req.model_dump(exclude_none=True)
        for k in (
            "t0_close_band_delta_pct",
            "t0_round_ratio",
            "t0_max_position_pct",
            "t0_slots_max_rounds",
        ):
            self.assertIn(k, dumped)
        self.assertNotIn("t0_leg_confirm_mode", dumped)
        self.assertNotIn("t0_env_gate_enabled", dumped)
        self.assertNotIn("t0_slots_roll_unused", dumped)
        self.assertNotIn("y_prefix_segment_enabled", dumped)
        self.assertNotIn("t0_confirm_dev_pct", dumped)
        self.assertNotIn("y_path_abandon_bars", dumped)

    def test_defaults_drop_legacy_mode_switches(self):
        d = load_t0_rules(
            {
                "t0_leg_confirm_mode": "bar_ratio",
                "t0_env_gate_enabled": False,
                "t0_slots_roll_unused": False,
            }
        )
        self.assertNotIn("t0_leg_confirm_mode", d)
        self.assertNotIn("t0_env_gate_enabled", d)
        self.assertNotIn("t0_slots_roll_unused", d)
        self.assertAlmostEqual(d.get("t0_close_band_delta_pct", 0.2), 0.2)
        self.assertEqual(d["t0_slots_max_rounds"], 5)

    def test_patch_accepts_close_band_thresholds(self):
        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "t0_close_band_delta_pct": 0.7,
                    "t0_round_ratio": 0.25,
                    "t0_slots_max_rounds": 3,
                }
            }
        )
        self.assertTrue(ok, errs)
        self.assertEqual(errs, [])
        t0 = norm["t0"]
        self.assertAlmostEqual(t0["t0_close_band_delta_pct"], 0.7)
        self.assertAlmostEqual(t0["t0_round_ratio"], 0.25)
        self.assertEqual(t0["t0_slots_max_rounds"], 3)

    def test_patch_schema_has_no_legacy_switches(self):
        fields = PaperExecutionPatchRequest.model_fields
        self.assertNotIn("t0_leg_confirm_mode", fields)
        self.assertNotIn("t0_env_gate_enabled", fields)
        self.assertNotIn("t0_slots_roll_unused", fields)
        self.assertNotIn("y_prefix_segment_enabled", fields)
        self.assertNotIn("t0_confirm_dev_pct", fields)
        self.assertNotIn("y_path_abandon_bars", fields)
        self.assertIn("t0_close_band_delta_pct", fields)


if __name__ == "__main__":
    unittest.main()
