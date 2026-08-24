"""做 T 成本与纸面调仓对齐。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestT0Costs(unittest.TestCase):
    def test_simple_cn_pnl_matches_cash_delta(self):
        from core.t0.minute_path import _first_touch_long

        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 09:45:00", "open": 10.5, "high": 10.5, "low": 9.8, "close": 10.0},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 9.8, "close": 10.0}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_long(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            sell_trig=5.0,
            buy_trig=5.0,
            lot=100,
            fill_mode="trigger",
            cfg={"t0_ratio": 0.4, "must_cover_same_day": True, "lot_size": 100},
            cost_model="simple_cn",
            cost_params=__import__("core.paper.costs", fromlist=["cost_params"]).cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
        )
        self.assertTrue(out.get("success"))
        self.assertEqual(len(out.get("trades") or []), 2)
        self.assertGreater(float(out.get("fees_total") or 0), 0)
        self.assertEqual(out.get("cost_model"), "simple_cn")
        self.assertAlmostEqual(float(out["pnl"]), float(out["cash_delta"]), places=2)
        for t in out["trades"]:
            self.assertIn("fees", t)
            self.assertIn("net_cash_delta", t)

    def test_zero_model_no_fees(self):
        from core.t0.costs import resolve_t0_cost_context

        model, params = resolve_t0_cost_context(paper={"cost_model": "zero"})
        self.assertEqual(model, "zero")
        gross = 1000.0
        from core.t0.costs import append_t0_leg

        trades = []
        append_t0_leg(
            trades,
            cost_model=model,
            cost_params=params,
            side="t0_sell",
            stock_code="600519",
            shares=100,
            price=10.0,
            trigger=10.0,
            at="t",
            leg_kind="trigger",
            note="test",
        )
        self.assertEqual(trades[0]["fees"], 0.0)
        self.assertEqual(trades[0]["net_cash_delta"], gross)


if __name__ == "__main__":
    unittest.main()
