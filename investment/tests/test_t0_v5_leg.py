"""v5 选腿：环境闸 / 复合确认 / 半贪心滚仓。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.t0.config import load_t0_rules
from core.t0.minute_path import (
    prefix_composite_entry_ok,
    prefix_env_gate_ok,
    prefix_leg1_confirm_ok,
)
from core.t0.rules import _t0_qty_lots
from core.t0.slots import allocate_remaining_slot_slice, slot_budget_t0_ratio


def _bars_dip_then_up(n=6, open_px=100.0):
    """前半下探，后半收阳（满足正T偏离+动量）。"""
    out = []
    for i in range(n):
        if i < n // 2:
            o, c = open_px - i * 0.2, open_px - i * 0.2 - 0.15
            hi, lo = o + 0.05, c - 0.9  # 深探
        else:
            o, c = open_px - 1.0 + (i - n // 2) * 0.3, open_px - 0.7 + (i - n // 2) * 0.35
            hi, lo = c + 0.1, o - 0.05
        out.append(
            {
                "datetime": f"2026-09-02 {9 + (i * 5) // 60:02d}:{(35 + i * 5) % 60:02d}:00",
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
                "volume": 1000 + i * 10,
            }
        )
    return out


class TestT0V5Leg(unittest.TestCase):
    def test_defaults_enable_v5_scheme(self):
        d = load_t0_rules()
        self.assertNotIn("y_prefix_segment_enabled", d)
        self.assertNotIn("y_prefix_segment_enabled_buy_then_sell", d)
        self.assertNotIn("y_prefix_segment_enabled_sell_then_buy", d)
        self.assertNotIn("t0_leg_confirm_mode", d)
        self.assertNotIn("t0_env_gate_enabled", d)
        self.assertNotIn("t0_slots_roll_unused", d)
        self.assertEqual(d["t0_slots_max_rounds"], 4)
        self.assertEqual(d["t0_confirm_mom_bars"], 2)
        self.assertAlmostEqual(d["t0_confirm_dev_pct"], 0.3)
        self.assertAlmostEqual(d["t0_env_min_range_pct"], 0.5)

    def test_segment_keys_dropped(self):
        d = load_t0_rules(
            {
                "y_prefix_segment_enabled": False,
                "y_prefix_segment_enabled_buy_then_sell": False,
                "y_prefix_segment_enabled_sell_then_buy": False,
            }
        )
        self.assertNotIn("y_prefix_segment_enabled", d)
        self.assertNotIn("y_prefix_segment_enabled_buy_then_sell", d)
        self.assertNotIn("y_prefix_segment_enabled_sell_then_buy", d)

    def test_env_threshold_zero_preserved(self):
        d = load_t0_rules(
            {
                "t0_env_min_range_pct": 0.0,
                "t0_env_min_path_abs": 0.0,
                "t0_env_one_sided_tau_abs": 0.0,
                "t0_env_one_sided_path_abs": 0.0,
            }
        )
        self.assertEqual(d["t0_env_min_range_pct"], 0.0)
        self.assertEqual(d["t0_env_min_path_abs"], 0.0)
        self.assertEqual(d["t0_env_one_sided_tau_abs"], 0.0)
        self.assertEqual(d["t0_env_one_sided_path_abs"], 0.0)

    def test_max_rounds_zero_preserved(self):
        d = load_t0_rules({"t0_slots_max_rounds": 0})
        self.assertEqual(d["t0_slots_max_rounds"], 0)

    def test_env_gate_blocks_small_range(self):
        g = prefix_env_gate_ok(
            range_pct=0.2,
            y_path=1.0,
            y_tau=0.5,
            cfg={"t0_env_min_range_pct": 1.0},
        )
        self.assertFalse(g["ok"])
        self.assertIn("振幅", g["reason"])

    def test_env_gate_blocks_one_sided(self):
        g = prefix_env_gate_ok(
            range_pct=3.0,
            y_path=2.5,
            y_tau=2.5,
            cfg={
                "t0_env_min_range_pct": 0.0,
                "t0_env_min_path_abs": 0.0,
                "t0_env_one_sided_tau_abs": 2.0,
                "t0_env_one_sided_path_abs": 2.0,
            },
        )
        self.assertFalse(g["ok"])
        self.assertIn("单边", g["reason"])

    def test_composite_buy_then_sell_ok(self):
        bars = _bars_dip_then_up(6, 100.0)
        cfg = {
            "y_path_abandon_bars": 6,
            "t0_confirm_dev_pct": 0.8,
            "t0_confirm_mom_bars": 2,
            "t0_confirm_vol_mult": 0.0,
        }
        seg = prefix_composite_entry_ok(
            bars, direction="buy_then_sell", cfg=cfg, ref=100.0
        )
        self.assertTrue(seg["ok"], seg)

    def test_composite_fails_without_dip(self):
        bars = []
        px = 100.0
        for i in range(6):
            bars.append(
                {
                    "open": px,
                    "high": px + 0.2,
                    "low": px - 0.05,
                    "close": px + 0.1,
                    "volume": 1000,
                }
            )
            px += 0.1
        seg = prefix_composite_entry_ok(
            bars,
            direction="buy_then_sell",
            cfg={
                "y_path_abandon_bars": 6,
                "t0_confirm_dev_pct": 0.8,
                "t0_confirm_mom_bars": 2,
            },
            ref=100.0,
        )
        self.assertFalse(seg["ok"], seg)

    def test_leg1_confirm_always_both(self):
        bars = _bars_dip_then_up(6, 100.0)
        cfg = {
            "y_path_abandon_bars": 6,
            "y_prefix_upbar_ratio_buy_then_sell": 0.5,
            "t0_confirm_dev_pct": 0.8,
            "t0_confirm_mom_bars": 2,
        }
        ok = prefix_leg1_confirm_ok(
            bars, direction="buy_then_sell", cfg=cfg, ref=100.0
        )
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["mode"], "both")
        self.assertEqual(len(ok["parts"]), 2)

    def test_allocate_remaining_rolls_budget(self):
        slots = [
            {"id": "s1", "ratio": 0.15},
            {"id": "s2", "ratio": 0.15},
            {"id": "s3", "ratio": 0.15},
            {"id": "s4", "ratio": 0.15},
        ]
        q0 = allocate_remaining_slot_slice(600, slots, 0, 100)
        q1 = allocate_remaining_slot_slice(600, slots, 1, 100)
        self.assertEqual(q0, 100.0)
        self.assertEqual(q1, 200.0)
        self.assertGreater(q1, q0)

    def test_max_rounds_zero_skips_all_slots(self):
        from core.t0.slots import simulate_t0_day_slots

        date = "2026-03-02"
        bar = {"date": date, "open": 100, "high": 105, "low": 97, "close": 101, "volume": 1000}
        h, m = 9, 35
        mins = []
        for i in range(24):
            mins.append(
                {
                    "datetime": f"{date} {h:02d}:{m:02d}:00",
                    "date": date,
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                }
            )
            m += 5
            if m >= 60:
                h += 1
                m -= 60
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg={
                "t0_slots_enabled": True,
                "t0_slots_max_rounds": 0,
                "t0_confirm_dev_pct": 0.0,
                "t0_confirm_mom_bars": 1,
                "t0_env_min_range_pct": 0.0,
                "t0_env_min_path_abs": 0.0,
                "y_prefix_upbar_ratio_buy_then_sell": 0.0,
                "direction": "buy_then_sell",
                "lot_size": 100,
            },
            cash=200000,
            stock_code="600519",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=None,
        )
        self.assertFalse(out.get("trades"), out)

    def test_roll_budget_drives_qty_not_slot_ratio(self):
        """空轮滚仓后 slice>slot.ratio×shares 时，成交量应跟预算走。"""
        slots = [
            {"id": "s1", "ratio": 0.15},
            {"id": "s2", "ratio": 0.15},
            {"id": "s3", "ratio": 0.15},
            {"id": "s4", "ratio": 0.15},
        ]
        shares, sellable, lot = 1000.0, 600.0, 100
        slice_q = allocate_remaining_slot_slice(sellable, slots, 1, lot)
        self.assertEqual(slice_q, 200.0)
        capped = _t0_qty_lots(shares, 0.15, lot, slice_q)
        eff = slot_budget_t0_ratio(shares, slice_q, 0.15)
        rolled = _t0_qty_lots(shares, eff, lot, slice_q)
        self.assertLess(capped, slice_q)
        self.assertEqual(rolled, slice_q)
        self.assertAlmostEqual(eff, 0.2)


if __name__ == "__main__":
    unittest.main()
