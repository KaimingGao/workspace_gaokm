"""调仓回测 ŷ头：恒 ridge（影子树头已下线）。"""

from __future__ import annotations

import unittest

import numpy as np


class TestRebalanceScoreBackend(unittest.TestCase):
    def test_normalize_always_ridge(self):
        from core.research.return_tree import (
            current_rebalance_score_backend,
            normalize_rebalance_score_backend,
            rebalance_score_backend_context,
        )

        self.assertEqual(normalize_rebalance_score_backend(None), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("TREE"), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("lightgbm"), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("lambdarank"), "ridge")
        self.assertEqual(normalize_rebalance_score_backend("oo_rank"), "ridge")
        self.assertEqual(current_rebalance_score_backend(), "ridge")
        with rebalance_score_backend_context("tree"):
            self.assertEqual(current_rebalance_score_backend(), "ridge")
        self.assertEqual(current_rebalance_score_backend(), "ridge")

    def test_regression_pack_roundtrip(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.horizon_lgb import _fit_lightgbm
        from core.research.horizon_tree import pack_tree_return_model, predict_tree_return

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

    def test_finish_return_tree_persist(self):
        from core.research.return_tree import finish_return_tree_persist

        report = {"tree_return_model": {"booster": {"x": 1}}}
        out = finish_return_tree_persist(
            report, {"success": True, "path": "/tmp/m.json"}
        )
        self.assertTrue(out["persisted"]["success"])
        self.assertEqual(out["model_path"], "/tmp/m.json")
        self.assertNotIn("tree_return_model", out)
        self.assertTrue(out["backtest_hook"])
        self.assertFalse(out["live_hook"])

if __name__ == "__main__":
    unittest.main()
