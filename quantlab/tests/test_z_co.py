"""z_co_* canon / 裸名别名。"""

from __future__ import annotations

import unittest


class ZCoAliasTests(unittest.TestCase):
    def test_stamp_and_feature_get_roundtrip(self):
        from core.signal.factors.z_co import (
            Z_CO_FIT_FEATURES,
            feature_get,
            stamp_z_co_aliases,
            to_z_co_key,
        )

        self.assertIn("z_co_gap_pct", Z_CO_FIT_FEATURES)
        self.assertIn("z_co_ret_oc", Z_CO_FIT_FEATURES)
        row = {"gap_pct": 1.25, "ret_oc": -0.5, "yest_gap": 0.8}
        stamp_z_co_aliases(row)
        self.assertEqual(row["z_co_gap_pct"], 1.25)
        self.assertEqual(row["z_co_ret_oc"], -0.5)
        self.assertEqual(row["z_co_yest_gap"], 0.8)
        self.assertEqual(feature_get(row, "z_co_gap_pct"), 1.25)
        self.assertEqual(feature_get({"gap_pct": 0.5}, "z_co_gap_pct"), 0.5)
        self.assertEqual(feature_get({"z_co_gap_pct": 0.7}, "gap_pct"), 0.7)
        self.assertEqual(to_z_co_key("gap_pct"), "z_co_gap_pct")

    def test_predict_accepts_legacy_or_canon(self):
        from core.research.co_ridge import predict_co_from_features

        fit = {
            "return_model": {
                "coefficients": {"z_co_gap_pct": 2.0, "z_co_theme_day": -1.0},
                "intercept": 0.0,
                "active_features": ["z_co_gap_pct", "z_co_theme_day"],
                "zscore_means": {},
                "zscore_stds": {},
            }
        }
        legacy = predict_co_from_features(
            {"gap_pct": 1.0, "theme_day": 0.0}, model_doc=fit
        )
        canon = predict_co_from_features(
            {"z_co_gap_pct": 1.0, "z_co_theme_day": 0.0}, model_doc=fit
        )
        self.assertAlmostEqual(float(legacy), float(canon), places=5)
        self.assertAlmostEqual(float(legacy), 2.0, places=5)

        old_fit = {
            "return_model": {
                "coefficients": {"gap_pct": 2.0},
                "intercept": 0.0,
                "active_features": ["gap_pct"],
                "zscore_means": {},
                "zscore_stds": {},
            }
        }
        via_canon = predict_co_from_features(
            {"z_co_gap_pct": 1.5}, model_doc=old_fit
        )
        self.assertAlmostEqual(float(via_canon), 3.0, places=5)

    def test_co_z_features_are_canon(self):
        from core.research.co_panel import CO_Z_FEATURES, CO_Z_LEGACY

        self.assertEqual(len(CO_Z_FEATURES), len(CO_Z_LEGACY))
        self.assertTrue(all(k.startswith("z_co_") for k in CO_Z_FEATURES))
        self.assertFalse(any(k.startswith("z_co_") for k in CO_Z_LEGACY))


if __name__ == "__main__":
    unittest.main()
