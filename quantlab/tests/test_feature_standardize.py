"""特征 feature_zscore / 日截面 z-score。"""

from __future__ import annotations

import unittest

import numpy as np


class TestFeatureZscore(unittest.TestCase):
    def test_resolve_and_annotate(self):
        from core.research.feature_standardize import (
            annotate_feature_zscore,
            resolve_feature_zscore,
        )

        self.assertTrue(resolve_feature_zscore(feature_zscore=True))
        self.assertFalse(resolve_feature_zscore(feature_zscore=False))
        self.assertTrue(
            resolve_feature_zscore({"hyperparams": {"feature_zscore": True}}, default=False)
        )
        self.assertFalse(
            resolve_feature_zscore({"hyperparams": {"feature_zscore": False}}, default=True)
        )
        d: dict = {}
        annotate_feature_zscore(d, True)
        self.assertEqual(d, {"feature_zscore": True})

    def test_days_do_not_share_mean(self):
        from core.research.feature_standardize import cross_section_zscore_matrix

        # 第一天两只股票 0 和 2（均值 1）；第二天两只都是 100。
        x = np.array([[0.0], [2.0], [100.0], [100.0]], dtype=np.float64)
        z = cross_section_zscore_matrix(x, ["d1", "d1", "d2", "d2"])
        self.assertAlmostEqual(float(z[0, 0]), -1.0, places=6)
        self.assertAlmostEqual(float(z[1, 0]), 1.0, places=6)
        self.assertAlmostEqual(float(z[2, 0]), 0.0, places=6)
        self.assertAlmostEqual(float(z[3, 0]), 0.0, places=6)

    def test_dicts_leave_missing(self):
        from core.research.feature_standardize import cross_section_zscore_dicts

        rows = cross_section_zscore_dicts(
            [{"a": 0.0}, {"a": 2.0, "b": 1.0}],
            ["a", "b"],
        )
        self.assertNotIn("b", rows[0])
        self.assertAlmostEqual(rows[0]["a"], -1.0, places=6)
        self.assertAlmostEqual(rows[1]["a"], 1.0, places=6)

    def test_dicts_mirror_z_alias_after_cs(self):
        """截面 z 后裸名与 canon 同值，避免 stamp 裸名优先冲掉 z。"""
        from core.research.feature_standardize import cross_section_zscore_dicts
        from core.signal.factors.z_co import stamp_z_co_aliases

        raw = [
            {"gap_pct": 1.0, "z_co_gap_pct": 1.0},
            {"gap_pct": 3.0, "z_co_gap_pct": 3.0},
            {"gap_pct": 5.0, "z_co_gap_pct": 5.0},
        ]
        z_rows = cross_section_zscore_dicts(raw, ["z_co_gap_pct"])
        z0 = float(z_rows[0]["z_co_gap_pct"])
        self.assertAlmostEqual(float(z_rows[0]["gap_pct"]), z0, places=6)
        stamp_z_co_aliases(z_rows[0])
        self.assertAlmostEqual(float(z_rows[0]["z_co_gap_pct"]), z0, places=6)

    def test_dates_for_complete_rows(self):
        from core.research.feature_standardize import dates_for_complete_rows

        self.assertEqual(
            dates_for_complete_rows(["2024-01-01", "2024-01-02", "2024-01-03"], [0, 2]),
            ["2024-01-01", "2024-01-03"],
        )
        self.assertIsNone(dates_for_complete_rows(["a"], [0, 1]))

    def test_prepare_lgb_features_cross_section(self):
        from core.research.feature_standardize import (
            cross_section_zscore_matrix,
            is_cross_section_zscore,
            stamp_cross_section_zscore,
        )
        from core.research.horizon_lgb import prepare_lgb_features

        xs = [
            {"a": 0.0, "b": 10.0},
            {"a": 2.0, "b": 14.0},
            {"a": 100.0, "b": 12.0},
            {"a": 100.0, "b": 16.0},
        ]
        dates = ["d1", "d1", "d2", "d2"]
        x_tr, x_te, means, patch = prepare_lgb_features(
            xs,
            xs[:2],
            ["a", "b"],
            feature_zscore=True,
            train_dates=dates,
            test_dates=dates[:2],
        )
        self.assertTrue(patch.get("feature_zscore"))
        self.assertEqual(patch.get("zscore_scope"), "cross_section")
        self.assertTrue(is_cross_section_zscore(patch))
        expect = cross_section_zscore_matrix(
            np.asarray([[0.0, 10.0], [2.0, 14.0], [100.0, 12.0], [100.0, 16.0]]),
            dates,
        )
        expect = np.where(np.isfinite(expect), expect, 0.0)
        np.testing.assert_allclose(x_tr, expect, rtol=1e-6)
        np.testing.assert_allclose(means, [0.0, 0.0])
        self.assertEqual(x_te.shape, (2, 2))
        stamped = stamp_cross_section_zscore({})
        self.assertEqual(stamped.get("zscore_means"), {})

class TestZscoreUniverse(unittest.TestCase):
    def test_normalize_aliases(self):
        from core.research.feature_standardize import (
            ZSCORE_UNIVERSE_POOL,
            ZSCORE_UNIVERSE_TIER_A,
            normalize_zscore_universe,
        )

        self.assertEqual(normalize_zscore_universe(None), ZSCORE_UNIVERSE_POOL)
        self.assertEqual(normalize_zscore_universe("day_panel"), ZSCORE_UNIVERSE_POOL)
        self.assertEqual(normalize_zscore_universe("watching"), ZSCORE_UNIVERSE_POOL)
        self.assertEqual(normalize_zscore_universe("tier_a"), ZSCORE_UNIVERSE_TIER_A)
        self.assertEqual(normalize_zscore_universe("A"), ZSCORE_UNIVERSE_TIER_A)

    def test_stamp_and_detect(self):
        from core.research.feature_standardize import (
            is_tier_a_zscore_universe,
            stamp_zscore_universe,
        )

        doc: dict = {}
        stamp_zscore_universe(doc, "tier_a", tier_codes=["600519", "000001"])
        self.assertEqual(doc["zscore_universe"], "tier_a")
        self.assertEqual(doc["zscore_universe_tiers"], ["A"])
        self.assertEqual(doc["tier_codes"], ["000001", "600519"])
        self.assertTrue(is_tier_a_zscore_universe(doc))
        self.assertTrue(is_tier_a_zscore_universe({"return_model": doc}))
        stamp_zscore_universe(doc, "pool")
        self.assertEqual(doc["zscore_universe"], "pool")
        self.assertNotIn("zscore_universe_tiers", doc)
        self.assertFalse(is_tier_a_zscore_universe(doc))

if __name__ == "__main__":
    unittest.main()
