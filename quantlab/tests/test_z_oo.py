"""z_oo_* canon / 裸名别名。"""

from __future__ import annotations

import unittest


class ZOoAliasTests(unittest.TestCase):
    def test_stamp_and_feature_get_roundtrip(self):
        from core.signal.factors.z_oo import (
            feature_get,
            stamp_z_oo_aliases,
            to_z_oo_key,
            z_oo_fit_features,
        )

        feats = z_oo_fit_features()
        self.assertIn("z_oo_momentum", feats)
        self.assertNotIn("momentum", feats)
        row = {"momentum": 55.0, "value": 40.0}
        stamp_z_oo_aliases(row)
        self.assertEqual(row["z_oo_momentum"], 55.0)
        self.assertEqual(row["z_oo_value"], 40.0)
        self.assertEqual(feature_get(row, "z_oo_momentum"), 55.0)
        self.assertEqual(feature_get({"momentum": 60.0}, "z_oo_momentum"), 60.0)
        self.assertEqual(feature_get({"z_oo_momentum": 70.0}, "momentum"), 70.0)
        self.assertEqual(to_z_oo_key("momentum"), "z_oo_momentum")
        self.assertEqual(to_z_oo_key("raw_aoo_YKMID"), "raw_aoo_YKMID")
        conflict = {"momentum": 1.0, "z_oo_momentum": 9.0}
        stamp_z_oo_aliases(conflict)
        self.assertEqual(conflict["z_oo_momentum"], 1.0)
        self.assertEqual(conflict["momentum"], 1.0)

    def test_raw_aoo_not_in_fit_features(self):
        from core.signal.factors.z_oo import z_oo_fit_features

        for k in z_oo_fit_features():
            self.assertFalse(str(k).startswith("raw_aoo_"), k)
            self.assertNotEqual(k, "alpha_oo")

    def test_predict_accepts_legacy_or_canon(self):
        from core.signal.return_score import ReturnScoreModel

        new_model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"z_oo_momentum": 2.0, "z_oo_value": -1.0},
            feature_zscore=False,
        )
        via_bare = new_model.predict({"momentum": 1.0, "value": 0.0})
        via_canon = new_model.predict({"z_oo_momentum": 1.0, "z_oo_value": 0.0})
        self.assertEqual(via_bare, via_canon)
        self.assertAlmostEqual(float(via_bare), 2.0, places=5)

        old_model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 2.0},
            feature_zscore=False,
        )
        via_canon2 = old_model.predict({"z_oo_momentum": 1.5})
        self.assertAlmostEqual(float(via_canon2), 3.0, places=5)

    def test_registered_ridge_names_are_canon(self):
        from core.research.oo_ridge_compact import _registered_ridge_names

        names = _registered_ridge_names()
        self.assertTrue(names)
        self.assertTrue(all(str(n).startswith("z_oo_") for n in names))
        self.assertNotIn("alpha_oo", names)
        self.assertNotIn("alpha_tc", names)


if __name__ == "__main__":
    unittest.main()
