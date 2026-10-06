"""特征 feature_zscore 开关。"""

from __future__ import annotations

import unittest


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

    def test_prepare_lgb_features_zscore(self):
        import numpy as np

        from core.research.factor_ols_fit import _zscore_complete_panel
        from core.research.tc_tree import prepare_lgb_features

        xs = [
            {"a": 1.0, "b": 10.0},
            {"a": 3.0, "b": 14.0},
            {"a": 5.0, "b": 12.0},
            {"a": 7.0, "b": 16.0},
        ]
        xs_z, mu, sd = _zscore_complete_panel(xs, ["a", "b"])
        x_tr, x_te, means, patch = prepare_lgb_features(
            xs, xs[:2], ["a", "b"], feature_zscore=True
        )
        self.assertTrue(patch.get("feature_zscore"))
        self.assertAlmostEqual(float(patch["zscore_means"]["a"]), float(mu["a"]), places=6)
        self.assertAlmostEqual(float(patch["zscore_stds"]["b"]), float(sd["b"]), places=6)
        for i, row in enumerate(xs_z):
            self.assertAlmostEqual(float(x_tr[i, 0]), float(row["a"]), places=6)
            self.assertAlmostEqual(float(x_tr[i, 1]), float(row["b"]), places=6)
        np.testing.assert_allclose(means, [mu["a"], mu["b"]], rtol=1e-6)
        self.assertEqual(x_te.shape, (2, 2))


if __name__ == "__main__":
    unittest.main()
