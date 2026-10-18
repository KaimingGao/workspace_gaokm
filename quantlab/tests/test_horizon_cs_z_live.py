"""live / 回测 ŷ_τ*：截面模型须吃日截面 z，而非 raw。"""

from __future__ import annotations

import unittest
from unittest import mock


class HorizonCsZLiveTests(unittest.TestCase):
    def test_predict_horizon_hat_uses_peer_cs_z(self):
        from core.t0 import score_policy as sp

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {"gap_pct": 1.0},
                "active_features": ["gap_pct"],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        peers = [
            {
                "stock_code": "A",
                "features_tau": {"gap_pct": 0.0},
            },
            {
                "stock_code": "B",
                "features_tau": {"gap_pct": 2.0},
            },
        ]
        seen = {}

        def _predict(feats, model_doc=None):
            seen["gap"] = feats.get("gap_pct")
            return 0.55

        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-01-02": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.horizon_tree.current_horizon_prob_backend",
            return_value="ridge",
        ):
            y_hat, model, src = sp._predict_horizon_hat(
                "t30",
                {"gap_pct": 2.0},
                load_ridge=lambda: doc,
                predict_ridge=_predict,
                predict_tree=_predict,
                trade_date="2026-01-02",
                stock_code="B",
            )
        self.assertAlmostEqual(float(y_hat), 0.55)
        self.assertEqual(src, "ridge")
        # 同伴 gap=[0,2]，B=2 → z≈+1，不是 raw 2
        self.assertAlmostEqual(float(seen["gap"]), 1.0, places=5)

    def test_rescore_horizons_writes_y_hat(self):
        from core.signal.cross_section_rescore import _rescore_one_horizon

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {"gap_pct": 0.5},
                "active_features": ["gap_pct"],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        items = [
            {"stock_code": "A", "features_tau": {"gap_pct": 0.0}},
            {"stock_code": "B", "features_tau": {"gap_pct": 2.0}},
        ]
        with mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.horizon_ridge.predict_ridge_from_features",
            return_value=0.61,
        ):
            _rescore_one_horizon(items, "t30")
        self.assertAlmostEqual(float(items[0]["y_τ30"]), 0.61, places=5)
        self.assertAlmostEqual(float(items[1]["y_t30"]), 0.61, places=5)


if __name__ == "__main__":
    unittest.main()
