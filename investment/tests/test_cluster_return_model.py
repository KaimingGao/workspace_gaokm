"""分组 OLS β → 分组收益分。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.return_score import ReturnScoreModel, apply_predicted_scores_by_model
from quant.research.factor_ols_clusters import (
    _public_cluster_ols,
    _return_model_from_ols,
)
from quant.research.cluster_pool_artifact import build_pool_artifact


class TestClusterReturnModel(unittest.TestCase):
    def test_public_ols_keeps_zstats_and_return_model(self):
        raw = {
            "success": True,
            "coefficients": {"momentum": 0.5, "value": -0.2},
            "intercept": 0.1,
            "r_squared": 0.3,
            "sample_count": 40,
            "active_features": ["momentum", "value"],
            "standardized": True,
            "zscore_means": {"momentum": 50.0, "value": 40.0},
            "zscore_stds": {"momentum": 10.0, "value": 5.0},
            "horizon_days": 3,
            "solver": "qr",
            "ridge_lambda": 0.0,
        }
        pub = _public_cluster_ols(raw, mode="cluster_pooled")
        self.assertTrue(pub["success"])
        self.assertIn("zscore_means", pub)
        rm = _return_model_from_ols(pub)
        self.assertIsNotNone(rm)
        model = ReturnScoreModel.from_dict(rm)
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.z_means["momentum"], 50.0)
        # z=1 → 0.1 + 0.5*1 = 0.6
        y = model.predict({"momentum": 60.0, "value": 40.0})
        self.assertAlmostEqual(y, 0.6, places=5)

    def test_from_ols_reads_zscore_aliases(self):
        model = ReturnScoreModel.from_ols_report(
            {
                "success": True,
                "intercept": 0.0,
                "coefficients": {"momentum": 1.0},
                "standardized": True,
                "zscore_means": {"momentum": 50.0},
                "zscore_stds": {"momentum": 10.0},
            }
        )
        self.assertIsNotNone(model)
        self.assertAlmostEqual(model.predict({"momentum": 60.0}), 1.0, places=5)

    def test_artifact_carries_return_model(self):
        rm = {
            "intercept": 0.0,
            "coefficients": {"momentum": 0.2},
            "z_means": {},
            "z_stds": {},
            "standardized": False,
        }
        report = {
            "success": True,
            "lookback": 60,
            "horizon_days": 3,
            "stock_count": 2,
            "clusters": [
                {
                    "cluster_id": 0,
                    "label": "G1",
                    "members": ["a", "b"],
                    "singleton": False,
                    "return_model": rm,
                    "weight_suggest": {
                        "success": True,
                        "suggested_weights": {"momentum": 0.6, "value": 0.4},
                    },
                }
            ],
        }
        art = build_pool_artifact(report)
        self.assertTrue(art["success"])
        self.assertEqual(art["clusters"][0]["return_model"]["coefficients"]["momentum"], 0.2)
        self.assertEqual(art["code_map"]["a"]["return_model"]["coefficients"]["momentum"], 0.2)

    def test_apply_by_code_models(self):
        m_hi = ReturnScoreModel(
            intercept=0.0, coefficients={"momentum": 1.0}, standardized=False
        )
        m_lo = ReturnScoreModel(
            intercept=0.0, coefficients={"momentum": 0.1}, standardized=False
        )
        entries = [
            {"stock_code": "hi", "score": 40.0, "sub_scores": {"momentum": 80.0}},
            {"stock_code": "lo", "score": 90.0, "sub_scores": {"momentum": 80.0}},
        ]
        out = apply_predicted_scores_by_model(
            entries, {"hi": m_hi, "lo": m_lo}, write_rank_score=False
        )
        self.assertAlmostEqual(out[0]["predicted_score"], 80.0)
        self.assertAlmostEqual(out[1]["predicted_score"], 8.0)
        self.assertEqual(out[0]["score_formula_terms"]["terms"][0]["key"], "momentum")
        self.assertAlmostEqual(float(out[0]["score_formula_terms"]["terms"][0]["contrib"]), 80.0)
        self.assertEqual(out[1]["score_formula_terms"]["terms"][0]["key"], "momentum")
        self.assertAlmostEqual(float(out[1]["score_formula_terms"]["terms"][0]["contrib"]), 8.0)


if __name__ == "__main__":
    unittest.main()
