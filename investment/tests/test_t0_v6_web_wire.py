"""v6 收盘带宽：Web schema / patch / 表单键接线。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.execution import validate_execution_patch
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

    def test_patch_schema_keeps_live_t0_fields(self):
        live = (
            "t0_close_band_delta_pct",
            "t0_y_τc_target_scale",
            "t0_lock_win_arm_bars",
            "y_tw_enter",
            "y_tw_midpoint",
            "y_tw_vote_margin",
            "y_τc_enter",
            "y_τc_strong",
            "y_τc_enter_amount",
            "y_τc_strong_amount",
            "residual_w_τc",
            "residual_w_oc",
        )
        for fields in (
            PaperExecutionPatchRequest.model_fields,
            T0BacktestRequest.model_fields,
        ):
            for name in live:
                self.assertIn(name, fields)


if __name__ == "__main__":
    unittest.main()
