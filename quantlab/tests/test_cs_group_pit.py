"""截面 CS 分组 PIT：date|τ 不被截成仅日期。"""

from __future__ import annotations

import unittest

import numpy as np


class CsGroupKeyTests(unittest.TestCase):
    def test_normalize_preserves_clock(self):
        from core.research.feature_standardize import (
            cs_group_key,
            normalize_cs_group_key,
        )

        self.assertEqual(normalize_cs_group_key("2026-01-02|10:30"), "2026-01-02|10:30")
        self.assertEqual(normalize_cs_group_key("2026-01-02"), "2026-01-02")
        self.assertEqual(cs_group_key("2026-01-02", "09:35"), "2026-01-02|09:35")
        self.assertEqual(cs_group_key("2026-01-02", "open"), "2026-01-02")

    def test_matrix_z_does_not_mix_clocks(self):
        from core.research.feature_standardize import cross_section_zscore_matrix

        # 同日两钟各 2 票：若混组则四行同一 μ；分钟后每组 μ 不同
        x = np.array(
            [
                [0.0],
                [2.0],
                [10.0],
                [12.0],
            ],
            dtype=np.float64,
        )
        groups = [
            "2026-01-02|09:35",
            "2026-01-02|09:35",
            "2026-01-02|10:30",
            "2026-01-02|10:30",
        ]
        z = cross_section_zscore_matrix(x, groups)
        # 09:35 组 mean=1 → z = -1, +1
        self.assertAlmostEqual(float(z[0, 0]), -1.0, places=5)
        self.assertAlmostEqual(float(z[1, 0]), 1.0, places=5)
        # 10:30 组 mean=11 → z = -1, +1
        self.assertAlmostEqual(float(z[2, 0]), -1.0, places=5)
        self.assertAlmostEqual(float(z[3, 0]), 1.0, places=5)

    def test_cs_groups_from_metas(self):
        from core.research.horizon_compact import cs_groups_from_dates_metas

        g = cs_groups_from_dates_metas(
            ["2026-01-02", "2026-01-02"],
            [{"tau": "09:35"}, {"tau": "10:30"}],
        )
        self.assertEqual(g, ["2026-01-02|09:35", "2026-01-02|10:30"])

    def test_cs_groups_accepts_clock_alias(self):
        from core.research.horizon_compact import cs_groups_from_dates_metas

        g = cs_groups_from_dates_metas(
            ["2026-01-02", "2026-01-02"],
            [{"clock": "09:35"}, {"decision_hm": "10:30"}],
        )
        self.assertEqual(g, ["2026-01-02|09:35", "2026-01-02|10:30"])

    def test_summarize_cs_groups_pit_vs_day(self):
        from core.research.horizon_compact import summarize_cs_groups

        pit = summarize_cs_groups(
            ["2026-01-02|09:35", "2026-01-02|10:30", "2026-01-03|09:35"]
        )
        self.assertEqual(pit["mode"], "pit")
        self.assertEqual(pit["n_unique"], 3)
        self.assertEqual(pit["n_day_only"], 0)

        day = summarize_cs_groups(["2026-01-02", "2026-01-02", "2026-01-03"])
        self.assertEqual(day["mode"], "day")
        self.assertEqual(day["n_unique"], 2)

        mixed = summarize_cs_groups(["2026-01-02|09:35", "2026-01-02"])
        self.assertEqual(mixed["mode"], "mixed")

    def test_horizon_fit_groups_stay_pit_not_day_pooled(self):
        """回归锁：同日多钟必须 date|τ，禁止截成仅日期（按日混钟不可 live）。"""
        from core.research.horizon_compact import (
            cs_groups_from_dates_metas,
            summarize_cs_groups,
        )

        dates = ["2026-01-02"] * 4
        metas = [
            {"tau": "09:35"},
            {"tau": "09:35"},
            {"tau": "10:30"},
            {"tau": "10:30"},
        ]
        groups = cs_groups_from_dates_metas(dates, metas)
        audit = summarize_cs_groups(groups)
        self.assertEqual(audit["mode"], "pit")
        self.assertEqual(audit["n_unique"], 2)
        self.assertTrue(all("|" in g for g in groups))
        # 若误用 dates[:10] 会变成 1 个唯一组
        self.assertNotEqual(len(set(d[:10] for d in dates)), audit["n_unique"])

    def test_day_or_mixed_groups_block_fit(self):
        from core.research.horizon_compact import cs_pit_block_reason

        self.assertIsNone(
            cs_pit_block_reason(
                ["2026-01-02|09:35", "2026-01-02|10:30"],
                ["2026-01-03|09:35"],
            )
        )
        day = cs_pit_block_reason(["2026-01-02", "2026-01-03"], ["2026-01-04|09:35"])
        self.assertIn("训=day", day or "")
        mixed = cs_pit_block_reason(
            ["2026-01-02|09:35", "2026-01-02"],
            ["2026-01-03|10:30"],
        )
        self.assertIn("训=mixed", mixed or "")


class PrefixLightPathTests(unittest.TestCase):
    def test_light_skips_resolve_when_open_snap(self):
        from unittest import mock

        from core.t0 import score_policy as sp

        open_snap = {
            "stock_code": "000001",
            "y_tau": 0.1,
            "predicted_score": 0.1,
            "features_tau": {"gap_pct": 1.0},
        }
        day = {"date": "2026-01-02", "open": 10.0, "close": 10.1}
        mins = [
            {
                "datetime": "2026-01-02 09:35:00",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
            }
        ]
        def _inject(item, **_kwargs):
            feats = dict(item.get("features_tau") or {})
            feats["ret_open_to_tau"] = 0.2
            item["features_tau"] = feats

        sp._PREFIX_RESCORE_CACHE.clear()
        with mock.patch.object(
            sp, "resolve_scores_for_code", side_effect=AssertionError("should skip")
        ), mock.patch.object(sp, "_attach_y_path_to_item"), mock.patch.object(
            sp, "_inject_minute_pack_from_prefix", side_effect=_inject
        ), mock.patch.object(
            sp, "_refresh_tau_oc_from_feats"
        ), mock.patch.object(
            sp, "scores_have_any", return_value=True
        ):
            out = sp.rescore_scores_at_fixed_prefix(
                stock_code="000001",
                minute_prefix=mins,
                day_bar=day,
                open_snap=open_snap,
                include_tau_horizons=True,
            )
        self.assertEqual(out.get("_score_source"), "prefix_light")
        self.assertNotIn("_minute_data_missing", out)

    def test_missing_minute_pack_clears_open_horizon_hats(self):
        from unittest import mock

        from core.t0 import score_policy as sp

        open_snap = {
            "stock_code": "000001",
            "y_tau": 0.1,
            "predicted_score": 0.1,
            "y_τ30": 0.8,
            "y_t30": 0.8,
            "predicted_score_t30": 0.8,
            "features_tau": {"gap_pct": 1.0},
        }
        day = {"date": "2026-01-02", "open": 10.0, "close": 10.1}
        mins = [
            {
                "datetime": "2026-01-02 09:35:00",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
            }
        ]
        sp._PREFIX_RESCORE_CACHE.clear()
        with mock.patch.object(sp, "_attach_y_path_to_item"), mock.patch.object(
            sp, "_inject_minute_pack_from_prefix"
        ), mock.patch.object(sp, "_refresh_tau_oc_from_feats"), mock.patch.object(
            sp, "scores_have_any", return_value=True
        ):
            out = sp.rescore_scores_at_fixed_prefix(
                stock_code="000001",
                minute_prefix=mins,
                day_bar=day,
                open_snap=open_snap,
                include_tau_horizons=True,
            )
        self.assertTrue(out.get("_minute_data_missing"))
        self.assertEqual(out.get("_score_source"), "prefix_minute_missing")
        self.assertIsNone(out.get("y_τ30"))
        self.assertIsNone(out.get("y_t30"))
        self.assertIsNone(out.get("predicted_score_t30"))

if __name__ == "__main__":
    unittest.main()
