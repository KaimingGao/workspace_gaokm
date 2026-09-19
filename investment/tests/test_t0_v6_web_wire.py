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
        self.assertAlmostEqual(d.get("t0_close_band_delta_pct", 3.0), 3.0)
        self.assertEqual(d["t0_slots_max_rounds"], 5)
        self.assertTrue(load_t0_rules({"t0_stop_on_close": False})["t0_stop_on_close"])

    def test_backtest_schema_defaults_cash_and_shares(self):
        req = T0BacktestRequest()
        self.assertEqual(req.initial_shares, 1000)
        self.assertEqual(req.initial_cash, 200_000)

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
        self.assertIn("t0_y_oc_target_scale", fields)
        self.assertIn("t0_y_oc_l", fields)
        self.assertIn("t0_y_oc_u", fields)
        self.assertNotIn("y_tc_enter", fields)
        self.assertNotIn("y_tc_enter_alt", fields)
        self.assertNotIn("y_tc_strong", fields)
        self.assertNotIn("y_t30_strong", fields)
        self.assertNotIn("y_t45_strong", fields)
        self.assertNotIn("y_t60_strong", fields)
        self.assertNotIn("y_t75_strong", fields)
        self.assertNotIn("y_t90_strong", fields)
        self.assertNotIn("y_tau_enter", fields)
        self.assertNotIn("y_tau_enter_alt", fields)
        self.assertNotIn("y_enter_enabled", fields)
        self.assertNotIn("y_hl_enter", fields)
        self.assertNotIn("y_t30_enter", fields)
        self.assertNotIn("y_t30_enter_alt", fields)
        self.assertNotIn("y_τ30_enter_alt", fields)
        self.assertIn("y_tw_enter", fields)
        self.assertIn("y_τw_enter", fields)
        self.assertNotIn("y_tw_strong", fields)
        self.assertNotIn("y_τw_strong", fields)
        self.assertIn("y_tw_vote_margin", fields)
        self.assertIn("y_τw_vote_margin", fields)
        self.assertNotIn("y_t45_enter", fields)
        self.assertNotIn("y_t45_enter_alt", fields)
        self.assertNotIn("y_τ45_enter_alt", fields)
        self.assertNotIn("y_t60_enter", fields)
        self.assertNotIn("y_t60_enter_alt", fields)
        self.assertNotIn("y_τ60_enter_alt", fields)
        self.assertNotIn("y_t75_enter", fields)
        self.assertNotIn("y_t75_enter_alt", fields)
        self.assertNotIn("y_τ75_enter_alt", fields)
        self.assertNotIn("y_t90_enter", fields)
        self.assertNotIn("y_t90_enter_alt", fields)
        self.assertNotIn("y_τ90_enter_alt", fields)
        self.assertNotIn("r_tau_enter", fields)
        self.assertNotIn("y_tau_map", fields)
        self.assertNotIn("min_range_pct", fields)
        self.assertNotIn("y_gap_tier_mode", fields)
        self.assertNotIn("y_trade_strong", fields)
        self.assertNotIn("y_eod_strong", fields)
        self.assertNotIn("y_nowcast_oc_gate", fields)
        self.assertNotIn("y_block_tau_nowcast_sign", fields)
        self.assertNotIn("use_atr", fields)
        fields_bt = T0BacktestRequest.model_fields
        self.assertNotIn("y_tc_strong", fields_bt)
        self.assertNotIn("y_t30_strong", fields_bt)
        self.assertNotIn("y_t45_strong", fields_bt)
        self.assertNotIn("y_t60_strong", fields_bt)
        self.assertNotIn("y_t75_strong", fields_bt)
        self.assertNotIn("y_t90_strong", fields_bt)
        self.assertNotIn("y_tau_enter", fields_bt)
        self.assertNotIn("y_tau_enter_alt", fields_bt)
        self.assertNotIn("y_enter_enabled", fields_bt)
        self.assertNotIn("y_hl_enter", fields_bt)
        self.assertNotIn("y_t30_enter", fields_bt)
        self.assertNotIn("y_t30_enter_alt", fields_bt)
        self.assertNotIn("y_τ30_enter_alt", fields_bt)
        self.assertIn("y_tw_enter", fields_bt)
        self.assertIn("y_τw_enter", fields_bt)
        self.assertNotIn("y_tw_strong", fields_bt)
        self.assertNotIn("y_τw_strong", fields_bt)
        self.assertIn("y_tw_vote_margin", fields_bt)
        self.assertIn("y_τw_vote_margin", fields_bt)
        self.assertNotIn("y_t45_enter", fields_bt)
        self.assertNotIn("y_t45_enter_alt", fields_bt)
        self.assertNotIn("y_τ45_enter_alt", fields_bt)
        self.assertNotIn("y_t60_enter", fields_bt)
        self.assertNotIn("y_t60_enter_alt", fields_bt)
        self.assertNotIn("y_τ60_enter_alt", fields_bt)
        self.assertNotIn("y_t75_enter", fields_bt)
        self.assertNotIn("y_t75_enter_alt", fields_bt)
        self.assertNotIn("y_τ75_enter_alt", fields_bt)
        self.assertNotIn("y_t90_enter", fields_bt)
        self.assertNotIn("y_t90_enter_alt", fields_bt)
        self.assertNotIn("y_τ90_enter_alt", fields_bt)
        self.assertNotIn("r_tau_enter_alt", fields)
        self.assertNotIn("y_tau_leg1_prior_mode", fields)


if __name__ == "__main__":
    unittest.main()
