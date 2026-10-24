"""τ 后窗口概率头：logistic Ridge、p_agree、闸迁移。"""

from __future__ import annotations

import unittest


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
        # 默认中位点 50%
        self.assertIsNone(p_up_vote(0.53))
        self.assertEqual(p_up_vote(0.56), 1)
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
            xs.append({"gap_pct": z, "feat_m": z * 0.4})
            ys.append(1.0 if z > 0 else 0.0)
        fit = fit_logistic_ridge_from_panel(
            xs,
            ys,
            feature_names=["gap_pct", "feat_m"],
            ridge_lambda=1.0,
        )
        self.assertTrue(fit.get("success"), fit)
        self.assertEqual(fit.get("head_kind"), "prob")
        preds = predict_p_up_rows(fit, xs)
        self.assertTrue(all(p is not None and 0.0 < p < 1.0 for p in preds))
        auc = roc_auc(preds, ys)
        self.assertIsNotNone(auc)
        self.assertGreater(float(auc), 0.8)

    def test_logistic_matrix_path_separates(self):
        import numpy as np

        from core.research.horizon_prob import (
            fit_logistic_ridge_from_matrix,
            predict_p_up_matrix,
            roc_auc,
        )

        z = np.linspace(-1.5, 1.5, 40)
        X = np.column_stack([z, z * 0.4])
        ys = [1.0 if v > 0 else 0.0 for v in z]
        fit = fit_logistic_ridge_from_matrix(
            X,
            ys,
            ["gap_pct", "feat_m"],
            ridge_lambda=1.0,
            cross_section_zscore=False,
        )
        self.assertTrue(fit.get("success"), fit)
        preds = predict_p_up_matrix(fit, X, ["gap_pct", "feat_m"])
        self.assertTrue(all(p is not None and 0.0 < p < 1.0 for p in preds))
        auc = roc_auc(preds, ys)
        self.assertIsNotNone(auc)
        self.assertGreater(float(auc), 0.8)

    def test_matrix_slice_tolerates_oob_and_bad_x(self):
        import numpy as np

        from core.research.horizon_prob import (
            _slice_feat_matrix,
            fit_logistic_ridge_from_matrix,
            predict_p_up_matrix,
        )

        X = np.arange(12, dtype=np.float64).reshape(4, 3)
        out, used = _slice_feat_matrix(
            X, ["a", "b", "c"], ["a", "missing"], [0, 99, 2]
        )
        self.assertEqual(used, ["a"])
        self.assertEqual(out.shape, (3, 1))
        self.assertTrue(np.isfinite(out[0, 0]))
        self.assertFalse(np.isfinite(out[1, 0]))  # OOB → NaN
        empty, used2 = _slice_feat_matrix(None, ["a"], ["a"], [0])
        self.assertEqual(empty.shape[1], 0)
        self.assertEqual(used2, [])
        # 过少样本 → 空模而非抛错
        fit = fit_logistic_ridge_from_matrix(
            X[:3],
            [1.0, 0.0, 1.0],
            ["a", "b", "c"],
            cross_section_zscore=False,
        )
        self.assertTrue(fit.get("success"))
        self.assertIn("过少", str(fit.get("note") or ""))
        preds = predict_p_up_matrix(fit, X, ["a", "b", "c"])
        self.assertEqual(len(preds), 4)

    def test_resolve_lgb_num_threads_clamps(self):
        from core.research.horizon_lgb import resolve_lgb_num_threads

        self.assertEqual(resolve_lgb_num_threads(4), 4)
        self.assertEqual(resolve_lgb_num_threads(0), 1)
        self.assertEqual(resolve_lgb_num_threads(-3), 1)
        self.assertEqual(resolve_lgb_num_threads(100), 32)
        self.assertGreaterEqual(resolve_lgb_num_threads(None), 1)

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
            collinearity_policy="keep_all",
        )
        fit90 = fit_logistic_ridge_from_panel(
            xs,
            ys,
            feature_names=feat90,
            ridge_lambda=1.0,
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
        from core.research.horizon_prob import sigmoid, stamp_horizon_explain

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
