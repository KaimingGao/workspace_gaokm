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
            y_tw_enter=1.0,
            t0_round_ratio=0.2,
            t0_max_position_pct=1.0,
            t0_slots_max_rounds=5,
            t0_slots_enabled=True,
        )
        dumped = req.model_dump(exclude_none=True)
        for k in (
            "y_tw_enter",
            "t0_round_ratio",
            "t0_max_position_pct",
            "t0_slots_max_rounds",
        ):
            self.assertIn(k, dumped)
        self.assertNotIn("t0_close_band_delta_pct", dumped)
        self.assertNotIn("t0_y_oc_target_scale", dumped)
        self.assertNotIn("y_hl_strong", dumped)
        self.assertNotIn("t0_leg_confirm_mode", dumped)
        self.assertNotIn("t0_env_gate_enabled", dumped)
        self.assertNotIn("t0_slots_roll_unused", dumped)
        self.assertNotIn("y_prefix_segment_enabled", dumped)
        self.assertNotIn("t0_confirm_dev_pct", dumped)
        self.assertNotIn("y_path_abandon_bars", dumped)

    def test_patch_request_flatten_skips_dropped_path_required(self):
        import inspect

        from web.routers import paper as paper_router

        req = PaperExecutionPatchRequest(y_tw_enter=2.0)
        self.assertFalse(hasattr(req, "y_path_required"))
        self.assertFalse(hasattr(req, "y_hl_required"))
        src = inspect.getsource(paper_router.paper_execution_save)
        self.assertNotIn("req.y_path_required", src)
        self.assertNotIn("req.y_hl_required", src)

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
        self.assertAlmostEqual(float(d["t0_close_band_delta_pct"]), 0.5)
        self.assertAlmostEqual(float(d["t0_y_τc_target_scale"]), 2.0)
        self.assertNotIn("t0_y_oc_l", d)
        self.assertNotIn("t0_y_oc_u", d)
        self.assertAlmostEqual(float(d["y_tw_midpoint"]), 47.0)
        self.assertNotIn("y_hl_strong", d)
        self.assertEqual(d["t0_slots_max_rounds"], 5)
        self.assertTrue(load_t0_rules({"t0_stop_on_close": False})["t0_stop_on_close"])

    def test_backtest_schema_defaults_cash_and_shares(self):
        req = T0BacktestRequest()
        self.assertEqual(req.initial_shares, 1000)
        self.assertEqual(req.initial_cash, 200_000)

    def test_virtual_shares_scale_with_max_rounds(self):
        from core.t0.config import t0_backtest_virtual_shares
        from web.routers.quant_backtest import _t0_backtest_kwargs

        self.assertEqual(t0_backtest_virtual_shares({}, 1000), 1000)
        self.assertEqual(
            t0_backtest_virtual_shares(
                {
                    "y_τc_enter_amount": 4000,
                    "y_τc_strong_amount": 4000,
                    "t0_slots_max_rounds": 5,
                },
                price=10.0,
            ),
            2000,
        )
        self.assertEqual(
            t0_backtest_virtual_shares(
                {
                    "y_τc_enter_amount": 2000,
                    "y_τc_strong_amount": 4000,
                    "t0_slots_max_rounds": 3,
                },
                price=10.0,
            ),
            1200,
        )
        kw = _t0_backtest_kwargs(
            T0BacktestRequest(
                initial_shares=400,
                y_τc_enter_amount=4000,
                y_τc_strong_amount=4000,
                t0_slots_max_rounds=5,
            )
        )
        self.assertEqual(kw["initial_shares"], 400)
        self.assertEqual(kw["rules"]["y_τc_enter_amount"], 4000)
        self.assertNotIn("y_oc_enter_amount", kw["rules"])

    def test_patch_accepts_close_band_thresholds(self):
        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "y_tw_enter": 1.5,
                    "t0_round_ratio": 0.25,
                    "t0_slots_max_rounds": 3,
                }
            }
        )
        self.assertTrue(ok, errs)
        self.assertEqual(errs, [])
        t0 = norm["t0"]
        self.assertAlmostEqual(t0["y_tw_enter"], 1.5)
        self.assertAlmostEqual(t0["t0_round_ratio"], 0.25)
        self.assertEqual(t0["t0_slots_max_rounds"], 3)
        self.assertNotIn("t0_leg1_close_extreme", t0)
        self.assertNotIn("t0_close_band_delta_pct", t0)

    def test_patch_schema_has_no_legacy_switches(self):
        fields = PaperExecutionPatchRequest.model_fields
        self.assertNotIn("t0_leg_confirm_mode", fields)
        self.assertNotIn("t0_env_gate_enabled", fields)
        self.assertNotIn("t0_slots_roll_unused", fields)
        self.assertNotIn("y_prefix_segment_enabled", fields)
        self.assertNotIn("t0_confirm_dev_pct", fields)
        self.assertNotIn("y_path_abandon_bars", fields)
        self.assertIn("t0_close_band_delta_pct", fields)
        self.assertIn("t0_y_τc_target_scale", fields)
        self.assertNotIn("t0_y_oc_target_scale", fields)
        self.assertNotIn("t0_y_oc_l", fields)
        self.assertNotIn("t0_y_oc_u", fields)
        self.assertIn("t0_lock_win_arm_bars", fields)
        self.assertIn("y_tw_midpoint", fields)
        self.assertNotIn("y_hl_strong", fields)
        self.assertNotIn("y_hl_required", fields)
        self.assertNotIn("y_path_required", fields)
        self.assertNotIn("y_path_strong", fields)
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
        self.assertIn("y_τc_enter", fields)
        self.assertIn("y_τc_strong", fields)
        self.assertIn("y_τc_enter_amount", fields)
        self.assertIn("y_τc_strong_amount", fields)
        self.assertNotIn("y_oc_enter", fields)
        self.assertNotIn("y_oc_strong", fields)
        self.assertNotIn("y_oc_enter_amount", fields)
        self.assertNotIn("y_oc_strong_amount", fields)
        self.assertNotIn("y_tw_strong", fields)
        self.assertNotIn("y_tw_enter_shares", fields)
        self.assertNotIn("y_tw_strong_shares", fields)
        self.assertNotIn("t0_leg1_close_extreme", fields)
        self.assertNotIn("y_tw_enter_buy_then_sell", fields)
        self.assertNotIn("y_tw_enter_sell_then_buy", fields)
        self.assertIn("y_tw_vote_margin", fields)
        self.assertIn("y_τw_vote_margin", fields)
        self.assertNotIn("t0_bar_oc_gate", fields)
        self.assertNotIn("t0_ytw_prefix_confirm", fields)
        self.assertNotIn("t0_ytw_prefix_lookback", fields)
        self.assertNotIn("t0_ytw_prefix_min_hit_pct", fields)
        self.assertIn("y_tw_midpoint", fields)
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
        self.assertIn("y_τc_enter", fields_bt)
        self.assertIn("y_τc_strong", fields_bt)
        self.assertNotIn("y_oc_enter", fields_bt)
        self.assertNotIn("y_oc_strong", fields_bt)
        self.assertNotIn("y_tw_strong", fields_bt)
        self.assertNotIn("t0_leg1_close_extreme", fields_bt)
        self.assertNotIn("y_tw_enter_buy_then_sell", fields_bt)
        self.assertNotIn("y_tw_enter_sell_then_buy", fields_bt)
        self.assertIn("y_tw_vote_margin", fields_bt)
        self.assertIn("y_τw_vote_margin", fields_bt)
        self.assertNotIn("t0_bar_oc_gate", fields_bt)
        self.assertNotIn("t0_ytw_prefix_confirm", fields_bt)
        self.assertNotIn("t0_ytw_prefix_lookback", fields_bt)
        self.assertNotIn("t0_ytw_prefix_min_hit_pct", fields_bt)
        self.assertIn("y_tw_midpoint", fields_bt)
        self.assertIn("t0_close_band_delta_pct", fields_bt)
        self.assertIn("t0_y_τc_target_scale", fields_bt)
        self.assertNotIn("t0_y_oc_target_scale", fields_bt)
        self.assertIn("t0_lock_win_arm_bars", fields_bt)
        self.assertNotIn("t0_y_oc_l", fields_bt)
        self.assertNotIn("t0_y_oc_u", fields_bt)
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
