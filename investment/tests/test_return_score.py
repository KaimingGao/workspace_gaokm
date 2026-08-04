"""收益导向打分 / rank_mode 对照。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.return_score import (
    ReturnScoreModel,
    apply_predicted_scores,
    clamp_rank_mode,
    rank_by_predicted_score,
)
from core.signal.cross_section_batch import score_and_rank_watching


class TestReturnScoreModel(unittest.TestCase):
    def test_predict_linear(self):
        model = ReturnScoreModel(
            intercept=1.0,
            coefficients={"momentum": 2.0, "value": -1.0},
            z_means={"momentum": 50.0, "value": 50.0},
            z_stds={"momentum": 10.0, "value": 10.0},
            standardized=True,
        )
        # z_mom=1, z_val=0 → 1 + 2*1 = 3
        y = model.predict({"momentum": 60.0, "value": 50.0})
        self.assertAlmostEqual(y, 3.0, places=5)
        formula = model.format_formula({"momentum": 60.0, "value": 50.0})
        self.assertIn("ŷ =", formula)
        self.assertIn("β=", formula)
        self.assertIn("3.000%", formula)
        expl = model.explain_prediction({"momentum": 60.0, "value": 50.0})
        self.assertIsNotNone(expl)
        self.assertAlmostEqual(expl["total"], 3.0, places=5)
        self.assertEqual(expl["terms"][0]["key"], "momentum")
        self.assertAlmostEqual(expl["terms"][0]["contrib"], 2.0, places=5)

    def test_rank_by_predicted_score(self):
        items = [
            {"stock_code": "a", "predicted_score": 0.5},
            {"stock_code": "b", "predicted_score": 1.2},
            {"stock_code": "c", "predicted_score": -0.1},
        ]
        picks = rank_by_predicted_score(items, min_predicted_score=0.0)
        self.assertEqual([c for c, _ in picks], ["b", "a"])

    def test_score_and_rank_predicted_mode(self):
        model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 1.0},
            standardized=False,
        )
        entries = [
            {
                "stock_code": "low",
                "score": 90.0,
                "sub_scores": {"momentum": 10.0},
            },
            {
                "stock_code": "high",
                "score": 40.0,
                "sub_scores": {"momentum": 80.0},
            },
        ]
        picks, meta = score_and_rank_watching(
            entries,
            min_score=55.0,
            neutralize=False,
            rank_mode="predicted_score",
            return_model=model,
        )
        self.assertEqual(meta["rank_mode"], "predicted_score")
        self.assertEqual(picks[0][0], "high")
        self.assertGreater(picks[0][1], picks[1][1])

    def test_from_ols_skips_none_coefs(self):
        model = ReturnScoreModel.from_ols_report(
            {
                "success": True,
                "intercept": 0.0,
                "coefficients": {"momentum": 0.2, "value": None, "quality": 0.1},
                "z_means": {"momentum": None, "quality": 50.0},
                "z_stds": {"quality": 10.0},
                "standardized": True,
                "horizon_days": 3,
                "sample_count": 30,
            }
        )
        self.assertIsNotNone(model)
        self.assertIn("momentum", model.coefficients)
        self.assertNotIn("value", model.coefficients)
        self.assertNotIn("momentum", model.z_means)

    def test_clamp_and_apply(self):
        self.assertEqual(clamp_rank_mode("yhat"), "predicted_score")
        self.assertEqual(clamp_rank_mode(None), "predicted_score")
        self.assertEqual(clamp_rank_mode("heuristic"), "predicted_score")
        model = ReturnScoreModel(intercept=0.0, coefficients={"momentum": 1.0}, standardized=False)
        out = apply_predicted_scores(
            [{"stock_code": "x", "score": 55, "sub_scores": {"momentum": 2.0}}],
            model,
            write_rank_score=True,
        )
        self.assertNotIn("heuristic_score", out[0])
        self.assertAlmostEqual(out[0]["predicted_score"], 2.0)
        self.assertAlmostEqual(out[0]["score"], 2.0)


if __name__ == "__main__":
    unittest.main()
