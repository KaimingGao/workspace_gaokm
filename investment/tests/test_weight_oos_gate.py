"""P2 weight OOS gate + signal_config overlay."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import load_signal_config, signal_config_overlay
from core.signal.weight_oos_gate import evaluate_weight_suggestion_oos
from core.signal.weight_suggest import format_weight_config_diff


class TestSignalConfigOverlay(unittest.TestCase):
    def test_overlay_weights_temporary(self):
        base = load_signal_config()
        mom0 = float((base.get("weights") or {}).get("momentum") or 0)
        with signal_config_overlay({"weights": {"momentum": 0.99}}):
            ov = load_signal_config()
            self.assertAlmostEqual(float(ov["weights"]["momentum"]), 0.99)
        again = load_signal_config()
        self.assertAlmostEqual(float(again["weights"]["momentum"]), mom0)


class TestWeightOosGate(unittest.TestCase):
    def test_gate_pass_when_suggested_oos_better(self):
        def fake_load(codes, lookback=90, fetch_fundamentals=False):
            bars = {
                "a": [{"date": f"2024-01-{i:02d}", "close": 10 + i * 0.01} for i in range(1, 60)],
                "b": [{"date": f"2024-01-{i:02d}", "close": 20 + i * 0.02} for i in range(1, 60)],
                "c": [{"date": f"2024-01-{i:02d}", "close": 30 + i * 0.01} for i in range(1, 60)],
            }
            return bars, [], {}

        def fake_bt(stock_bars, **kwargs):
            # detect overlay via load_signal_config
            cfg = load_signal_config()
            mom = float((cfg.get("weights") or {}).get("momentum") or 0)
            # higher momentum weight → better OOS curve
            if mom >= 0.5:
                curve = [{"date": "d1", "equity": 100}, {"date": "d2", "equity": 105}, {"date": "d3", "equity": 112}, {"date": "d4", "equity": 120}]
            else:
                curve = [{"date": "d1", "equity": 100}, {"date": "d2", "equity": 102}, {"date": "d3", "equity": 101}, {"date": "d4", "equity": 99}]
            return {
                "success": True,
                "metrics": {"total_return_pct": curve[-1]["equity"] - 100},
                "equity_curve": curve,
                "trades": [],
            }

        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            side_effect=fake_load,
        ), patch(
            "core.watching_store.read_watching",
            return_value={"watchlist": ["a", "b", "c"]},
        ), patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            side_effect=fake_bt,
        ):
            out = evaluate_weight_suggestion_oos(
                {"momentum": 0.2, "value": 0.8},
                {"momentum": 0.6, "value": 0.4},
                lookback=60,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["passed"])
        self.assertEqual(out["reason"], "oos_not_worse")

    def test_diff_apply_note_reflects_gate(self):
        sug = {
            "success": True,
            "current_weights": {"momentum": 0.5, "value": 0.5},
            "suggested_weights": {"momentum": 0.6, "value": 0.4},
            "deltas": {},
            "oos_gate": {"ok": True, "passed": False, "skipped": False, "reason": "oos_worse_-3.0pp"},
            "promote_ready": False,
        }
        diff = format_weight_config_diff(sug)
        self.assertIn("未过", diff["apply_note"])
        self.assertFalse(diff["promote_ready"])


if __name__ == "__main__":
    unittest.main()
