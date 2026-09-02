"""v5 选腿：Web schema / patch / 表单键接线。"""

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


class TestT0V5WebWire(unittest.TestCase):
    def test_backtest_schema_accepts_v5_keys(self):
        req = T0BacktestRequest(
            t0_confirm_dev_pct=0.8,
            t0_confirm_mom_bars=2,
            t0_confirm_vol_mult=0.0,
            t0_env_min_range_pct=1.0,
            t0_env_min_path_abs=0.15,
            t0_env_one_sided_tau_abs=2.0,
            t0_env_one_sided_path_abs=2.0,
            t0_slots_max_rounds=4,
            t0_slots_enabled=True,
            y_tau_require_for_leg1=True,
        )
        dumped = req.model_dump(exclude_none=True)
        for k in (
            "t0_confirm_dev_pct",
            "t0_confirm_mom_bars",
            "t0_confirm_vol_mult",
            "t0_env_min_range_pct",
            "t0_env_min_path_abs",
            "t0_env_one_sided_tau_abs",
            "t0_env_one_sided_path_abs",
            "t0_slots_max_rounds",
        ):
            self.assertIn(k, dumped)
        self.assertNotIn("t0_leg_confirm_mode", dumped)
        self.assertNotIn("t0_env_gate_enabled", dumped)
        self.assertNotIn("t0_slots_roll_unused", dumped)
        self.assertNotIn("y_prefix_segment_enabled", dumped)

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
        self.assertAlmostEqual(d["t0_confirm_dev_pct"], 0.3)
        self.assertEqual(d["t0_slots_max_rounds"], 4)

    def test_patch_accepts_confirm_thresholds(self):
        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "t0_confirm_dev_pct": 1.0,
                    "t0_confirm_mom_bars": 3,
                    "t0_slots_max_rounds": 3,
                }
            }
        )
        self.assertTrue(ok, errs)
        self.assertEqual(errs, [])
        t0 = norm["t0"]
        self.assertAlmostEqual(t0["t0_confirm_dev_pct"], 1.0)
        self.assertEqual(t0["t0_confirm_mom_bars"], 3)
        self.assertEqual(t0["t0_slots_max_rounds"], 3)

    def test_patch_schema_has_no_legacy_switches(self):
        fields = PaperExecutionPatchRequest.model_fields
        self.assertNotIn("t0_leg_confirm_mode", fields)
        self.assertNotIn("t0_env_gate_enabled", fields)
        self.assertNotIn("t0_slots_roll_unused", fields)
        self.assertNotIn("y_prefix_segment_enabled", fields)
        self.assertNotIn("y_prefix_segment_enabled_buy_then_sell", fields)
        self.assertNotIn("y_prefix_segment_enabled_sell_then_buy", fields)
        self.assertIn("t0_confirm_mom_bars", fields)


if __name__ == "__main__":
    unittest.main()
