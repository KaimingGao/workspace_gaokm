"""调仓回测 ŷ头 ridge/tree：上下文、回归预测、缺模型回退。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestRebalanceScoreBackend(unittest.TestCase):
    def test_normalize_and_context_default_ridge(self):
        from core.research.return_tree import (
            current_rebalance_score_backend,
            normalize_rebalance_score_backend,
            rebalance_score_backend_context,
        )

        self.assertEqual(normalize_rebalance_score_backend(None), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("TREE"), "tree")
        self.assertEqual(normalize_rebalance_score_backend("lightgbm"), "tree")
        self.assertEqual(normalize_rebalance_score_backend("lambdarank"), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("oo_rank"), "ridge")
        self.assertEqual(current_rebalance_score_backend(), "ridge")
        with rebalance_score_backend_context("tree"):
            self.assertEqual(current_rebalance_score_backend(), "tree")
        self.assertEqual(current_rebalance_score_backend(), "ridge")

    def test_predict_return_head_falls_back_without_tree_model(self):
        from core.research.return_tree import (
            predict_return_head,
            rebalance_score_backend_context,
        )

        def _load_empty():
            return None

        def _tree(_feats, model_doc=None):
            raise AssertionError("tree predict should not run")

        def _ridge(feats, model_doc=None):
            return 1.5

        with rebalance_score_backend_context("tree"):
            y, src = predict_return_head(
                {"a": 1.0},
                load_tree=_load_empty,
                predict_tree=_tree,
                predict_ridge=_ridge,
            )
        self.assertEqual(y, 1.5)
        self.assertEqual(src, "ridge")

    def test_predict_return_head_prefers_tree(self):
        from core.research.return_tree import (
            predict_return_head,
            rebalance_score_backend_context,
        )

        def _load():
            return {"return_model": {"feature_names": ["a"]}}

        def _tree(_feats, model_doc=None):
            return 0.4

        def _ridge(_feats, model_doc=None):
            raise AssertionError("ridge should not run")

        with rebalance_score_backend_context("tree"):
            y, src = predict_return_head(
                {"a": 1.0},
                load_tree=_load,
                predict_tree=_tree,
                predict_ridge=_ridge,
            )
        self.assertEqual(y, 0.4)
        self.assertEqual(src, "tree")

    def test_predict_return_head_follows_horizon_tree_backend(self):
        from core.research.horizon_tree import horizon_prob_backend_context
        from core.research.return_tree import predict_return_head

        def _load():
            return {"return_model": {"feature_names": ["a"]}}

        def _tree(_feats, model_doc=None):
            return 0.7

        def _ridge(_feats, model_doc=None):
            raise AssertionError("ridge should not run under horizon tree")

        with horizon_prob_backend_context("tree"):
            y, src = predict_return_head(
                {"a": 1.0},
                load_tree=_load,
                predict_tree=_tree,
                predict_ridge=_ridge,
            )
        self.assertEqual(y, 0.7)
        self.assertEqual(src, "tree")

    def test_overlay_oo_tree_on_item_under_horizon_backend(self):
        from core.research.horizon_tree import horizon_prob_backend_context
        from core.research.return_tree import overlay_oo_tree_on_item

        item = {"y_oo": 0.11, "predicted_score": 0.11, "stock_code": "600000"}
        with horizon_prob_backend_context("tree"), patch(
            "core.research.oo_tree.load_oo_tree_model",
            return_value={"return_model": {"feature_names": ["gap_pct"]}},
        ), patch(
            "core.research.oo_tree.oo_tree_features_from_window",
            return_value={"gap_pct": 1.2},
        ), patch(
            "core.research.oo_tree.predict_oo_tree_from_features",
            return_value=0.77,
        ), patch(
            "core.research.horizon_tree.tree_tip_formula",
            return_value={
                "model_role": "tree",
                "total": 0.77,
                "terms": [{"key": "raw_alpha158_KMID", "contrib": 0.4}],
            },
        ):
            overlay_oo_tree_on_item(item, window=[{"close": 10}], quote={"open": 10.1})
        self.assertAlmostEqual(float(item["y_oo"]), 0.77)
        self.assertEqual(item.get("y_oo_source"), "tree")
        self.assertEqual(
            (item.get("score_formula_terms") or {}).get("model_role"), "tree"
        )
        keys = {
            t.get("key")
            for t in (item.get("score_formula_terms") or {}).get("terms") or []
            if isinstance(t, dict)
        }
        self.assertIn("raw_alpha158_KMID", keys)

    def test_regression_pack_roundtrip(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.horizon_tree import pack_tree_return_model, predict_tree_return
        from core.research.tc_tree import _fit_lightgbm

        rng = np.random.default_rng(0)
        x = rng.normal(size=(40, 2))
        y = 0.3 * x[:, 0] - 0.2 * x[:, 1]
        w = np.ones(40)
        model, _gain = _fit_lightgbm(
            x,
            y,
            w,
            n_estimators=8,
            max_depth=2,
            learning_rate=0.2,
            subsample=1.0,
        )
        packed = pack_tree_return_model(
            head="oo",
            backend="lightgbm",
            feature_names=["a", "b"],
            impute_means=[0.0, 0.0],
            model_obj=model,
            schema="test",
            kind="return",
            y_label="open[T+1]/open[T]-1",
        )
        self.assertEqual(packed.get("head_kind"), "return")
        pred = predict_tree_return({"a": 1.0, "b": -1.0}, packed)
        self.assertIsNotNone(pred)
        self.assertTrue(abs(float(pred)) < 5.0)

        from core.research.horizon_tree import explain_tree_return

        feats = {"a": 1.0, "b": -1.0}
        expl = explain_tree_return(feats, packed)
        self.assertEqual(expl.get("model_role"), "tree")
        self.assertAlmostEqual(float(expl["total"]), float(pred), places=4)
        summed = float(expl["intercept"]) + sum(float(t["contrib"]) for t in expl["terms"])
        self.assertAlmostEqual(summed, float(pred), places=3)

    def test_tree_main_heads_usable_requires_oo_and_tc(self):
        from core.research.return_tree import tree_main_heads_usable

        with patch(
            "core.research.return_tree.return_tree_model_state",
            return_value={
                "oo": "collapsed_fallback_ridge",
                "tc": "loaded",
                "co": "loaded",
            },
        ):
            self.assertFalse(tree_main_heads_usable())
        with patch(
            "core.research.return_tree.return_tree_model_state",
            return_value={"oo": "loaded", "tc": "loaded", "co": "loaded"},
        ):
            self.assertTrue(tree_main_heads_usable())


if __name__ == "__main__":
    unittest.main()
