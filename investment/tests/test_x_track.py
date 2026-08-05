"""X 轨：live 财务 PIT / index / 因子健康 / 质量政策。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestXTrackLiveFeatures(unittest.TestCase):
    def test_resolve_live_fundamentals_as_of_matches_resolve(self):
        from core.fundamentals_pit import resolve_fundamentals_for_score
        from core.signal.live_features import resolve_live_fundamentals

        hist = [
            {
                "as_of": "2024-03-31",
                "available_as_of": "2024-04-20",
                "ann_date": "2024-04-20",
                "metrics": {"pe": 10.0, "pb": 1.2, "roe": 0.12},
            },
            {
                "as_of": "2024-06-30",
                "available_as_of": "2024-08-01",
                "ann_date": "2024-08-01",
                "metrics": {"pe": 12.0, "pb": 1.5, "roe": 0.15},
            },
        ]
        cfg = {"fundamentals": {"pit_mode": "as_of", "missing_as_of_policy": "zero_weight"}}
        with patch(
            "core.fundamentals_pit.load_fundamentals_panel",
            return_value={"history": hist, "history_count": 2},
        ):
            decision = "2024-05-01"
            live = resolve_live_fundamentals(
                "600000",
                as_of=decision,
                config=cfg,
            )
            research = resolve_fundamentals_for_score(
                "600000",
                as_of=decision,
                fund_cfg=cfg["fundamentals"],
                live_fallback=False,
            )
        self.assertEqual(live.get("metrics"), research.get("metrics"))
        self.assertTrue((live.get("fundamentals_pit") or {}).get("fundamentals_pit"))
        self.assertEqual((live.get("metrics") or {}).get("pe"), 10.0)

    def test_ann_missing_flag_on_select(self):
        from core.fundamentals_pit import select_point_as_of

        hist = [
            {
                "as_of": "2024-03-31",
                "ann_missing": True,
                "metrics": {"pe": 8.0},
            }
        ]
        # without available/ann, _available_date falls back to as_of
        point, meta = select_point_as_of(hist, "2024-06-01")
        self.assertIsNotNone(point)
        self.assertTrue(meta.get("ann_missing"))

    def test_factor_health_blocks_money_flow_weight(self):
        from core.signal.factor_health import (
            assess_factor_health,
            guard_weights_for_promote,
        )

        h = assess_factor_health(config={"weights": {"money_flow": 0.2, "momentum": 0.1}})
        self.assertFalse(h.get("ok"))
        self.assertTrue(h.get("promote_blocked"))
        g = guard_weights_for_promote({"money_flow": 0.2})
        self.assertFalse(g.get("ok"))
        g2 = guard_weights_for_promote({"money_flow": 0.2}, force=True)
        self.assertTrue(g2.get("ok"))
        self.assertTrue(g2.get("forced"))

    def test_quality_policy_snapshot(self):
        from core.signal.live_features import build_quality_policy_snapshot

        qp = build_quality_policy_snapshot(config={"fundamentals": {"pit_mode": "as_of"}})
        self.assertTrue(qp["live"]["quality_gate"])
        self.assertFalse(qp["backtest"]["quality_gate"])
        self.assertEqual(qp["live"]["fundamentals_pit_mode"], "as_of")

    def test_fundamentals_depth_cn(self):
        from core.signal.live_features import infer_fundamentals_depth

        with patch(
            "core.ports.market.resolve_market_code",
            return_value="CN",
        ):
            d = infer_fundamentals_depth("600519")
        self.assertEqual(d.get("fundamentals_depth"), "cn_full")
        self.assertFalse(d.get("sentiment_in_yhat"))

    def test_fit_gap_includes_quality_policy(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(realization={"status": "unavailable", "reason": "test"})
        codes = {h.get("code") for h in out.get("hints") or []}
        self.assertIn("quality_policy", codes)
        self.assertIsNotNone(out.get("quality_policy"))

    def test_panel_respect_regime_narrows_factors(self):
        from core.research.panel import _resolve_factor_names

        with patch(
            "core.signal.regime.assess_regime",
            return_value={"adjustments": {"enabled_factors": ["momentum", "value"]}},
        ):
            names = _resolve_factor_names(
                respect_regime=True,
                index_bars=[{"close": 1}, {"close": 1.01}],
                config={"regime": {"enabled": True}},
            )
        self.assertEqual(set(names), {"momentum", "value"})


if __name__ == "__main__":
    unittest.main()
