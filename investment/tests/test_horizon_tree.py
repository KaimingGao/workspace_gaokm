"""horizon_tree：落盘 / 预测 / backend 切换。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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

    def test_t0_rules_carry_backend(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"horizon_prob_backend": "tree"})
        self.assertEqual(cfg.get("horizon_prob_backend"), "tree")
        cfg2 = load_t0_rules({})
        self.assertEqual(cfg2.get("horizon_prob_backend"), "ridge")

    def test_predict_horizon_hat_prefers_tree(self):
        from core.research.horizon_tree import horizon_prob_backend_context
        from core.t0.score_policy import _predict_horizon_hat

        def load_ridge():
            return {"return_model": {"intercept": 0.0, "coefficients": {}}}

        def predict_ridge(feats, model_doc=None):
            return 0.41

        def load_tree():
            return {"return_model": {"feature_names": ["gap_pct"]}}

        def predict_tree(feats, model_doc=None):
            return 0.62

        with horizon_prob_backend_context("tree"):
            y, _doc, src = _predict_horizon_hat(
                "t30",
                {"gap_pct": 1.0},
                load_ridge=load_ridge,
                predict_ridge=predict_ridge,
                load_tree=load_tree,
                predict_tree=predict_tree,
            )
        self.assertAlmostEqual(float(y), 0.62)
        self.assertEqual(src, "tree")

        y2, _doc2, src2 = _predict_horizon_hat(
            "t30",
            {"gap_pct": 1.0},
            load_ridge=load_ridge,
            predict_ridge=predict_ridge,
            load_tree=load_tree,
            predict_tree=predict_tree,
        )
        self.assertAlmostEqual(float(y2), 0.41)
        self.assertEqual(src2, "ridge")


if __name__ == "__main__":
    unittest.main()
