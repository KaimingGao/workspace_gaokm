"""market_context_merge / ipo_metrics 单元测试。"""

from __future__ import annotations

import unittest

from core.market.context_merge import merge_macro_snapshots, freshness_report
from adapters.announcement.ipo_metrics import compute_drain_ratios


class TestMarketContextMerge(unittest.TestCase):
    def test_merge_macro_keeps_prior_series(self):
        prior = {
            "series": {"qqq": {"close": 400, "change_1d_pct": -1.0}},
            "overseas_tech_1d_pct": -1.0,
        }
        fresh = {"series": {}, "errors": ["sox:empty"], "liquidity_stress_score": 1.0}
        merged = merge_macro_snapshots(fresh, prior)
        self.assertEqual(merged["series"]["qqq"]["close"], 400)
        self.assertTrue(merged.get("merged_from_prior"))

    def test_merge_macro_prunes_stale_errors(self):
        prior = {
            "series": {"a50": {"close": 14700, "change_1d_pct": 0.3}},
            "errors": ["a50:empty", "cnh:timeout"],
        }
        fresh = {
            "series": {"cnh": {"close": 678.5, "change_1d_pct": -0.08}},
            "errors": ["cnh:empty"],
        }
        merged = merge_macro_snapshots(fresh, prior)
        self.assertEqual(merged["series"]["a50"]["close"], 14700)
        self.assertEqual(merged["series"]["cnh"]["close"], 678.5)
        self.assertEqual(merged["errors"], [])

        ctx = {"macro": {"fetched_at": "2020-01-01T08:00:00"}}
        rep = freshness_report(ctx, stale_hours=24.0)
        self.assertTrue(rep.get("needs_ingest"))

    def test_drain_ratio(self):
        events = [{"market_cap_est": 200e9, "name": "Mega IPO"}]
        amounts = {"机器人": 50e9}
        out = compute_drain_ratios(events, amounts)
        self.assertGreater(out["liquidity_drain_ratio"], 3.0)
        self.assertTrue(out["extreme_ipo_day"])


if __name__ == "__main__":
    unittest.main()
