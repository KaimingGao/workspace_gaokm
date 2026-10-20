"""horizon_tree：落盘 / 预测 / backend 切换。"""

from __future__ import annotations

import unittest


class TestHorizonTree(unittest.TestCase):
    def test_backend_context_and_normalize(self):
        from core.research.horizon_tree import (
            current_horizon_prob_backend,
            horizon_prob_backend_context,
            normalize_horizon_prob_backend,
        )

        self.assertEqual(normalize_horizon_prob_backend("TREE"), "tree")
        self.assertEqual(normalize_horizon_prob_backend("ridge"), "ridge")
        self.assertEqual(current_horizon_prob_backend(), "ridge")
        with horizon_prob_backend_context("tree"):
            self.assertEqual(current_horizon_prob_backend(), "tree")
        self.assertEqual(current_horizon_prob_backend(), "ridge")

    def test_pack_rejects_retired_backends(self):
        from core.research.horizon_tree import pack_tree_return_model

        for backend in ("xgboost", "numpy_gbm", "numpy"):
            with self.assertRaises(ValueError):
                pack_tree_return_model(
                    head="x",
                    backend=backend,
                    feature_names=["a"],
                    impute_means=[0.0],
                    model_obj=None,
                    schema="x",
                )

    def test_t0_rules_carry_backend(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"horizon_prob_backend": "tree"})
        self.assertEqual(cfg.get("horizon_prob_backend"), "tree")
        cfg2 = load_t0_rules({})
        self.assertEqual(cfg2.get("horizon_prob_backend"), "ridge")

    def test_predict_horizon_hat_force_tree_uses_backends_tree(self):
        """强制 Tree：读启用套 backends.tree，不再走独立 tree 包。"""
        from core.research.horizon_tree import horizon_prob_backend_context
        from core.t0.score_policy import _predict_horizon_hat

        def load_ridge():
            return {
                "backend": "ridge",
                "return_model": {"coefficients": {"a": 1.0}},
                "backends": {
                    "ridge": {"return_model": {"coefficients": {"a": 1.0}}},
                    "tree": {
                        "return_model": {"feature_names": ["gap_pct"], "booster": {}}
                    },
                },
            }

        def predict_ridge(feats, model_doc=None):
            return 0.41

        def predict_tree(feats, model_doc=None):
            return 0.62

        with horizon_prob_backend_context("tree"):
            y, doc, src = _predict_horizon_hat(
                "t30",
                {"gap_pct": 1.0},
                load_ridge=load_ridge,
                predict_ridge=predict_ridge,
                predict_tree=predict_tree,
            )
        self.assertAlmostEqual(float(y), 0.62)
        self.assertEqual(src, "tree")
        self.assertEqual((doc or {}).get("backend"), "tree")

        y2, _doc2, src2 = _predict_horizon_hat(
            "t30",
            {"gap_pct": 1.0},
            load_ridge=load_ridge,
            predict_ridge=predict_ridge,
            predict_tree=predict_tree,
        )
        self.assertAlmostEqual(float(y2), 0.41)
        self.assertEqual(src2, "ridge")

    def test_predict_horizon_hat_reads_pack_backend(self):
        """启用槽 backend=tree 时，无需 ContextVar 也走 tree。"""
        from core.t0.score_policy import _predict_horizon_hat

        def load_ridge():
            return {
                "backend": "tree",
                "return_model": {"feature_names": ["gap_pct"], "booster": {}},
            }

        def predict_ridge(feats, model_doc=None):
            # 真实路径里 predict_ridge_from_features 会按 backend 分发
            return 0.71

        def predict_tree(feats, model_doc=None):
            return 0.11

        y, doc, src = _predict_horizon_hat(
            "t30",
            {"gap_pct": 1.0},
            load_ridge=load_ridge,
            predict_ridge=predict_ridge,
            predict_tree=predict_tree,
        )
        self.assertAlmostEqual(float(y), 0.71)
        self.assertEqual(src, "tree")
        self.assertEqual((doc or {}).get("backend"), "tree")

    def test_stamp_horizon_formula_uses_tree_tip(self):
        from core.t0.score_policy import _stamp_horizon_formula_terms

        item: dict = {}
        _stamp_horizon_formula_terms(
            item,
            {"gap_pct": 1.0},
            src="tree",
            model={"return_model": {"feature_names": ["gap_pct"]}},
            y_hat=0.62,
            keys=("formula_terms_t30", "score_formula_terms_t30"),
            explain_ridge=lambda *a, **k: {"model_role": "ridge", "terms": [{"key": "ridge_only"}]},
        )
        self.assertEqual(item["formula_terms_t30"].get("model_role"), "tree")
        self.assertAlmostEqual(float(item["formula_terms_t30"]["total"]), 0.62, places=6)
        self.assertNotEqual(
            (item["formula_terms_t30"].get("terms") or [{}])[0].get("key"),
            "ridge_only",
        )

    def test_refresh_tau_oc_always_ridge(self):
        from unittest.mock import patch

        from core.research.horizon_tree import horizon_prob_backend_context
        from core.t0.score_policy import _refresh_tau_oc_from_feats

        item = {
            "as_of_tau": "2026-09-18T09:40:00+08:00",
            "features_tau": {"ret_open_to_tau": 2.17, "gap_pct": 0.5},
            "formula_terms_tau": {
                "model_role": "ridge",
                "terms": [{"key": "ridge_gap", "contrib": 0.1}],
            },
        }
        ridge_expl = {
            "model_role": "ridge",
            "intercept": 0.0,
            "total": 0.55,
            "terms": [{"key": "gap_pct", "contrib": 0.4}],
        }
        with horizon_prob_backend_context("tree"), patch(
            "core.research.tc_ridge.predict_tau_from_features",
            return_value=0.55,
        ), patch(
            "core.research.tc_ridge.explain_tau_prediction",
            return_value=ridge_expl,
        ):
            _refresh_tau_oc_from_feats(item, hm="09:40", trade_date="2026-09-18")
        self.assertEqual(item.get("y_τc_source"), "ridge")
        self.assertNotIn("y_τc_tree", item)
        self.assertEqual(item["formula_terms_tau"].get("model_role"), "ridge")

    def test_collapsed_stump_is_not_a_return_forecast(self):
        from core.research.horizon_lgb import lgb_best_iteration_collapsed
        from core.research.horizon_tree import (
            predict_tree_return,
            tree_return_model_collapsed,
        )

        self.assertTrue(lgb_best_iteration_collapsed(1))
        self.assertFalse(lgb_best_iteration_collapsed(40))
        rm = {
            "feature_names": ["a"],
            "backend": "lightgbm",
            "hyperparams": {"best_iteration": 1},
        }
        self.assertTrue(tree_return_model_collapsed({"return_model": rm}))
        self.assertIsNone(predict_tree_return({"a": 1.0}, rm))

    def test_stamp_tree_fitted_at_on_report_and_model(self):
        from core.research.horizon_tree import stamp_tree_fitted_at

        skipped = stamp_tree_fitted_at({"success": False})
        self.assertIsNone(skipped.get("fitted_at"))

        report = {
            "success": True,
            "tree_return_model": {"feature_names": ["a"], "backend": "lightgbm"},
        }
        stamp_tree_fitted_at(report)
        self.assertTrue(report.get("fitted_at"))
        self.assertEqual(
            report["tree_return_model"].get("fitted_at"), report["fitted_at"]
        )
        first = report["fitted_at"]
        stamp_tree_fitted_at(report)
        self.assertEqual(report["fitted_at"], first)

if __name__ == "__main__":
    unittest.main()
