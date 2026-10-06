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

    def test_lookback_impute_aligns_horizon_sample_count(self):
        """早盘缺近窗 / ret_last_5m 时仍留行，与预测 z=0 一致。"""
        from core.research.horizon_prob import (
            fit_logistic_ridge_from_panel,
            predict_p_up_rows,
        )

        xs = []
        for i in range(40):
            row = {"gap_pct": -2.0 + 4.0 * i / 39.0}
            if i >= 1:
                row["ret_last_5m"] = -1.0 + 0.05 * i
            if i >= 13:
                row["ret_last_30m"] = -8.0 + 16.0 * (i - 13) / 26.0
                row["vol_last_30m_vs_avg"] = 0.4 + 0.05 * (i - 13)
            if i >= 36:
                row["ret_last_90m"] = -6.0 + 4.0 * (i - 36)
                row["vol_last_90m_vs_avg"] = 0.5 + 0.2 * (i - 36)
            xs.append(row)
        ys = [1.0 if row["gap_pct"] > 0 else 0.0 for row in xs]
        exempt = [
            "gap_pct",
            "ret_last_5m",
            "ret_last_30m",
            "vol_last_30m_vs_avg",
            "ret_last_90m",
            "vol_last_90m_vs_avg",
        ]
        feat90 = [
            "gap_pct",
            "ret_last_5m",
            "ret_last_90m",
            "vol_last_90m_vs_avg",
        ]
        fit30 = fit_logistic_ridge_from_panel(
            xs,
            ys,
            feature_names=["gap_pct", "ret_last_5m", "ret_last_30m", "vol_last_30m_vs_avg"],
            ridge_lambda=1.0,
            min_std_exempt=exempt,
            collinearity_policy="keep_all",
        )
        fit90 = fit_logistic_ridge_from_panel(
            xs,
            ys,
            feature_names=feat90,
            ridge_lambda=1.0,
            min_std_exempt=exempt,
            collinearity_policy="keep_all",
        )
        self.assertTrue(fit30.get("success"), fit30)
        self.assertTrue(fit90.get("success"), fit90)
        self.assertEqual(fit30.get("sample_count"), 40)
        self.assertEqual(fit90.get("sample_count"), 40)
        self.assertIn("ret_last_30m", fit30.get("active_features") or [])
        self.assertIn("ret_last_90m", fit90.get("active_features") or [])
        filled = (fit90.get("prep_meta") or {}).get("impute_n_filled") or {}
        self.assertEqual(filled.get("ret_last_90m"), 36)
        self.assertEqual(filled.get("ret_last_5m"), 1)
        self.assertIsNone(xs[0].get("ret_last_90m"))
        self.assertIsNone(xs[0].get("ret_last_5m"))
        preds = predict_p_up_rows(fit90, xs)
        self.assertEqual(len(preds), 40)
        self.assertTrue(all(p is not None for p in preds))

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
