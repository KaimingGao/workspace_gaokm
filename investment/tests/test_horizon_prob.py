"""τ 后窗口概率头：logistic Ridge、p_agree、闸迁移。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class HorizonProbHelpersTests(unittest.TestCase):
    def test_p_agree_folds_direction(self):
        from core.research.horizon_prob import horizon_band_agree, p_agree, p_up_vote

        self.assertAlmostEqual(p_agree("buy_then_sell", 0.7), 0.7)
        self.assertAlmostEqual(p_agree("sell_then_buy", 0.7), 0.3)
        self.assertTrue(horizon_band_agree("buy_then_sell", 0.7))
        self.assertFalse(horizon_band_agree("sell_then_buy", 0.7))
        self.assertTrue(horizon_band_agree("sell_then_buy", 0.3))
        self.assertIsNone(p_agree(None, 0.7))
        self.assertEqual(p_up_vote(0.7, midpoint=0.5), 1)
        self.assertEqual(p_up_vote(0.3, midpoint=0.5), -1)
        self.assertIsNone(p_up_vote(0.5, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.52, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.48, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.51, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.49, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.521, midpoint=0.5))
        self.assertIsNone(p_up_vote(0.479, midpoint=0.5))
        self.assertEqual(p_up_vote(0.521, margin_pp=2, midpoint=0.5), 1)
        self.assertEqual(p_up_vote(0.479, margin_pp=2, midpoint=0.5), -1)
        self.assertEqual(p_up_vote(0.51, margin_pp=0, midpoint=0.5), 1)
        self.assertEqual(p_up_vote(0.49, margin_pp=0, midpoint=0.5), -1)
        self.assertIsNone(p_up_vote(0.54, margin_pp=5, midpoint=0.5))
        self.assertEqual(p_up_vote(0.56, margin_pp=5, midpoint=0.5), 1)
        # 默认中位点 47%
        self.assertEqual(p_up_vote(0.53), 1)
        self.assertEqual(p_up_vote(0.40), -1)
        self.assertIsNone(p_up_vote(0.47))
        self.assertIsNone(p_up_vote(0.50))
        self.assertEqual(p_up_vote(0.53, midpoint=47), 1)

    def test_logistic_separates_on_synthetic(self):
        from core.research.horizon_prob import (
            fit_logistic_ridge_from_panel,
            predict_p_up_rows,
            roc_auc,
        )

        xs = []
        ys = []
        for i in range(40):
            z = -1.5 + 3.0 * i / 39.0
            xs.append({"gap_pct": z, "mom3_pct": z * 0.4})
            ys.append(1.0 if z > 0 else 0.0)
        fit = fit_logistic_ridge_from_panel(
            xs,
            ys,
            feature_names=["gap_pct", "mom3_pct"],
            ridge_lambda=1.0,
            min_std_exempt=["gap_pct", "mom3_pct"],
        )
        self.assertTrue(fit.get("success"), fit)
        self.assertEqual(fit.get("head_kind"), "prob")
        preds = predict_p_up_rows(fit, xs)
        self.assertTrue(all(p is not None and 0.0 < p < 1.0 for p in preds))
        auc = roc_auc(preds, ys)
        self.assertIsNotNone(auc)
        self.assertGreater(float(auc), 0.8)

    def test_stamp_horizon_explain_adds_p_up(self):
        from core.research.horizon_prob import stamp_horizon_explain, sigmoid

        expl = stamp_horizon_explain(
            {"intercept": -0.12, "total": -0.248, "terms": []},
            head="t30",
            model_doc={"model_role": "live"},
        )
        self.assertEqual(expl.get("head"), "t30")
        self.assertEqual(expl.get("head_kind"), "prob")
        self.assertEqual(expl.get("model_role"), "live")
        self.assertAlmostEqual(float(expl.get("logit")), -0.248, places=6)
        self.assertAlmostEqual(float(expl.get("p_up")), sigmoid(-0.248), places=6)
        self.assertAlmostEqual(float(expl.get("p_up")), 0.4383, places=3)

    def test_vote_margin_clamps_and_ignores_greek_alias(self):
        from core.t0.config import load_t0_rules

        self.assertAlmostEqual(float(load_t0_rules({})["y_tw_vote_margin"]), 5.0)
        self.assertAlmostEqual(float(load_t0_rules({"y_tw_vote_margin": 25})["y_tw_vote_margin"]), 20.0)
        ignored = load_t0_rules({"y_τw_vote_margin": 3.5})
        self.assertAlmostEqual(float(ignored["y_tw_vote_margin"]), 5.0)


if __name__ == "__main__":
    unittest.main()
