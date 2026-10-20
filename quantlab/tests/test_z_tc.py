"""z_τc_* canon / 裸名别名。"""

from __future__ import annotations

import unittest


class ZTcAliasTests(unittest.TestCase):
    def test_stamp_and_feature_get_roundtrip(self):
        from core.signal.factors.z_tc import (
            Z_TC_FIT_FEATURES,
            feature_get,
            stamp_z_tc_aliases,
            to_z_tc_key,
        )

        self.assertIn("z_τc_gap_pct", Z_TC_FIT_FEATURES)
        row = {"gap_pct": 1.25, "theme_day": 1.0}
        stamp_z_tc_aliases(row)
        self.assertEqual(row["z_τc_gap_pct"], 1.25)
        self.assertEqual(row["z_τc_theme_day"], 1.0)
        self.assertEqual(feature_get(row, "z_τc_gap_pct"), 1.25)
        self.assertEqual(feature_get({"gap_pct": 0.5}, "z_τc_gap_pct"), 0.5)
        self.assertEqual(feature_get({"z_τc_gap_pct": 0.7}, "gap_pct"), 0.7)
        self.assertEqual(to_z_tc_key("gap_pct"), "z_τc_gap_pct")
        # 裸名优先覆盖陈旧 canon
        conflict = {"gap_pct": 1.0, "z_τc_gap_pct": 9.0}
        stamp_z_tc_aliases(conflict)
        self.assertEqual(conflict["z_τc_gap_pct"], 1.0)
        self.assertEqual(conflict["gap_pct"], 1.0)

    def test_predict_rows_accepts_legacy_or_canon(self):
        from core.research.tc_ridge import _predict_rows

        fit = {
            "coefficients": {"z_τc_gap_pct": 2.0, "z_τc_theme_day": -1.0},
            "intercept": 0.0,
            "active_features": ["z_τc_gap_pct", "z_τc_theme_day"],
            "zscore_means": {},
            "zscore_stds": {},
        }
        legacy = _predict_rows(fit, [{"gap_pct": 1.0, "theme_day": 0.0}])
        canon = _predict_rows(fit, [{"z_τc_gap_pct": 1.0, "z_τc_theme_day": 0.0}])
        self.assertEqual(legacy, canon)
        self.assertAlmostEqual(float(legacy[0]), 2.0, places=5)

        old_fit = {
            "coefficients": {"gap_pct": 2.0},
            "intercept": 0.0,
            "active_features": ["gap_pct"],
            "zscore_means": {},
            "zscore_stds": {},
        }
        via_canon = _predict_rows(old_fit, [{"z_τc_gap_pct": 1.5}])
        self.assertAlmostEqual(float(via_canon[0]), 3.0, places=5)

if __name__ == "__main__":
    unittest.main()
