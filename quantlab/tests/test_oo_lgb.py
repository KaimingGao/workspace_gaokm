"""ŷ_oo LightGBM：包识别与晋级门。"""

from __future__ import annotations

import unittest

from core.research.oo_lgb import (
    SCHEMA_OO_LGB,
    OoLightgbmScorer,
    is_oo_lightgbm_blob,
    oo_model_feature_names,
)


class TestOoLightgbmBlob(unittest.TestCase):
    def test_detect_booster(self):
        blob = {
            "feature_names": ["z_oo_momentum", "raw_aoo_GAP"],
            "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
            "schema": SCHEMA_OO_LGB,
            "zscore_scope": "cross_section",
        }
        self.assertTrue(is_oo_lightgbm_blob(blob))
        scorer = OoLightgbmScorer.from_dict(blob)
        self.assertIsNotNone(scorer)
        self.assertEqual(scorer.solver, "lightgbm")
        self.assertEqual(scorer.zscore_scope, "cross_section")
        self.assertIn("z_oo_momentum", scorer.feature_names)

    def test_reject_ridge(self):
        self.assertFalse(
            is_oo_lightgbm_blob(
                {"coefficients": {"z_oo_momentum": 0.1}, "intercept": 0.0}
            )
        )

    def test_feature_names_prefer_lgb_over_empty_coefs(self):
        scorer = OoLightgbmScorer(
            {
                "feature_names": ["z_oo_momentum", "raw_aoo_GAP"],
                "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
                "schema": SCHEMA_OO_LGB,
            }
        )
        self.assertEqual(scorer.coefficients, {})
        self.assertEqual(
            oo_model_feature_names(scorer),
            ["z_oo_momentum", "raw_aoo_GAP"],
        )

    def test_feature_names_legacy_ridge_coefs(self):
        class _R:
            coefficients = {"momentum": 0.2}
            feature_names = []

        self.assertEqual(oo_model_feature_names(_R()), ["momentum"])


class TestPromoteRequiresLgb(unittest.TestCase):
    def test_usable_and_hydrate_refuse_ridge_blob(self):
        """草稿探测与 hydrate 一致：旧 Ridge 当缺，只认 LightGBM。"""
        from core.research.oo_model import _hydrate_oo_scorer, _usable_oo_model_blob

        ridge = {"coefficients": {"a": 1.0}, "intercept": 0.0}
        self.assertFalse(_usable_oo_model_blob(ridge))
        self.assertIsNone(_hydrate_oo_scorer(ridge, suite="live"))
        self.assertTrue(
            _usable_oo_model_blob(
                {
                    "feature_names": ["a"],
                    "booster": {
                        "format": "lightgbm_string",
                        "payload_b64": "YQ==",
                    },
                }
            )
        )


class TestOoLgbCsZInfer(unittest.TestCase):
    def test_tree_cs_impute_is_zero_not_raw_mean(self):
        from core.research.horizon_tree import _tree_impute_means

        means = _tree_impute_means(
            {
                "feature_names": ["a"],
                "impute_means": {"a": 99.0},
                "zscore_scope": "cross_section",
            }
        )
        self.assertEqual(means, {})

    def test_matrix_cs_without_dates_refuses(self):
        import numpy as np

        from core.research.oo_lgb import SCHEMA_OO_LGB, predict_oo_lgb_matrix

        blob = {
            "feature_names": ["f1"],
            "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
            "schema": SCHEMA_OO_LGB,
            "zscore_scope": "cross_section",
        }
        out = predict_oo_lgb_matrix(
            blob, np.ones((2, 1)), ["f1"], [0, 1], row_dates=None
        )
        self.assertEqual(out, [None, None])

    def test_fit_watching_default_horizon_is_one(self):
        import inspect

        from core.research import oo_model

        sig = inspect.signature(oo_model.fit_watching_return_model)
        self.assertEqual(sig.parameters["horizon_days"].default, 1)

    def test_fit_stores_impute_stds_for_factor_table(self):
        """因子表 μ/σ：拟合写 impute_stds；zscore_* 仍空（截面推理）。"""
        import inspect
        from pathlib import Path

        oo_src = Path(inspect.getfile(__import__("core.research.oo_lgb", fromlist=["*"]))).read_text(
            encoding="utf-8"
        )
        tc_src = Path(
            inspect.getfile(__import__("core.research.tc_lgb", fromlist=["*"]))
        ).read_text(encoding="utf-8")
        self.assertIn('blob["impute_stds"]', oo_src)
        self.assertIn('blob["impute_stds"]', tc_src)
        self.assertIn("nanstd", oo_src)
        self.assertIn("nanstd", tc_src)


if __name__ == "__main__":
    unittest.main()
