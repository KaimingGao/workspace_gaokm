"""专业核心三轨 DC / FM / RK 最小验收。"""

from __future__ import annotations

import unittest


class TestProCoreDC(unittest.TestCase):
    def test_assess_pit_depth_thresholds(self):
        from core.pro_core import ANN_MISSING_HARD_MAX, assess_pit_depth

        soft = assess_pit_depth(
            fundamentals_history={
                "ann_missing_code_ratio": 0.4,
                "real_multi_coverage": 0.6,
                "ann_missing_top": [{"stock_code": "600000"}],
            }
        )
        self.assertTrue(soft["ok"])
        self.assertFalse(soft["soft_ok"])
        self.assertEqual(soft["honest_label"], "pit_soft_debt")
        self.assertIn("600000", soft["ann_missing_top_codes"])

        hard = assess_pit_depth(
            fundamentals_history={
                "ann_missing_code_ratio": ANN_MISSING_HARD_MAX + 0.1,
                "real_multi_coverage": 0.6,
            }
        )
        self.assertFalse(hard["ok"])
        self.assertEqual(hard["honest_label"], "pit_hard_debt")

    def test_filter_halted_bars(self):
        from core.market_calendar import filter_halted_bars

        bars = [
            {"date": "2024-01-02", "volume": 100, "close": 10},
            {"date": "2024-01-03", "volume": 0, "close": 10},
            {"date": "2024-01-04", "volume": 50, "status": "停牌", "close": 10},
            {"date": "2024-01-05", "vol": 80, "close": 11},
        ]
        kept, audit = filter_halted_bars(bars)
        self.assertEqual(len(kept), 2)
        self.assertEqual(audit["dropped_n"], 2)
        self.assertGreaterEqual(audit["drop_reasons"].get("zero_volume", 0), 1)

    def test_adjust_policy_consistent(self):
        from core.pro_core import assert_adjust_policy_consistent

        ok = assert_adjust_policy_consistent(requested="qfq", observed="qfq")
        self.assertTrue(ok["ok"])
        bad = assert_adjust_policy_consistent(requested="qfq", observed="raw")
        self.assertFalse(bad["ok"])


class TestProCoreFM(unittest.TestCase):
    def test_guard_weights_blocks_proxy(self):
        from core.signal.factor_health import guard_weights_for_promote

        g = guard_weights_for_promote({"money_flow": 0.15})
        self.assertTrue(g.get("blocked"))
        g2 = guard_weights_for_promote({"money_flow": 0.15}, force=True)
        self.assertFalse(g2.get("blocked"))
        self.assertTrue(g2.get("forced"))

    def test_list_factors_meta(self):
        from core.signal.factor_registry import list_factors

        rows = list_factors(include_meta=True)
        mf = next((r for r in rows if r["name"] == "money_flow"), None)
        self.assertIsNotNone(mf)
        self.assertFalse(mf.get("sourced"))
        self.assertEqual(mf.get("status"), "proxy")


class TestProCoreRK(unittest.TestCase):
    def test_regime_position_scale(self):
        from core.pro_core import regime_position_scale

        r = regime_position_scale(regime={"regime": "weak", "adjustments": {}})
        self.assertLess(r["scale"], 1.0)
        r2 = regime_position_scale(
            regime={"adjustments": {"position_scale": 0.5}}
        )
        self.assertAlmostEqual(r2["scale"], 0.5)

    def test_style_soft_caps(self):
        from core.pro_core import style_soft_caps_from_exposure

        caps = style_soft_caps_from_exposure(
            {"styles": {"growth": 55.0, "value": 10.0}}, max_style_pct=40.0
        )
        self.assertFalse(caps["ok"])
        self.assertEqual(caps["over_limit"][0]["style"], "growth")

    def test_optimize_limits_include_regime_scale(self):
        from core.portfolio_optimize import optimize_weights

        out = optimize_weights(
            [
                {"stock_code": "600000", "score": 0.05, "sector": "银行"},
                {"stock_code": "600001", "score": 0.04, "sector": "银行"},
            ],
            max_position_pct=10.0,
            max_sector_pct=20.0,
            max_positions=5,
            min_score=-1.0,
            apply_market_vol=False,
            apply_regime_scale=False,
        )
        self.assertTrue(out.get("ok"))
        self.assertIn("regime_scale", out)
        self.assertIn("combined_scale", out)
        self.assertIn("regime_scale", out.get("limits") or {})


class TestProCoreIngestNudge(unittest.TestCase):
    def test_ingest_nudge_payload(self):
        from core.pro_core import ingest_nudge_payload

        p = ingest_nudge_payload(
            sample_status={
                "fundamentals_history": {
                    "ann_missing_code_ratio": 0.1,
                    "real_multi_coverage": 0.8,
                    "ann_missing_top": [{"code": "000001"}, {"code": "000002"}],
                }
            }
        )
        self.assertTrue(p["ok"])
        self.assertEqual(p["count"], 2)


if __name__ == "__main__":
    unittest.main()
