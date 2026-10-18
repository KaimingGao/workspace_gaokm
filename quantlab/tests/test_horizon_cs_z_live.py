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

    def test_cube_covers_rejects_slim_gap_only(self):
        from core.t0 import score_policy as sp

        class _Slim:
            names = ["gap_pct"]

        class _Full:
            names = ["gap_pct", "ret_open_to_tau", "loc_hl", "range_pct", "yclose_loc"]

        names = ["gap_pct", "ret_open_to_tau", "loc_hl", "range_pct"]
        self.assertFalse(sp._cube_covers_horizon_names(_Slim(), names))
        self.assertTrue(sp._cube_covers_horizon_names(_Full(), names))

    def test_cs_z_keeps_sparse_ret_open_not_sqrt2(self):
        """开盘同伴 ret_open=0 占位不得把各钟差异压成 √2 / 0。"""
        from core.t0 import score_policy as sp

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {"gap_pct": 0.1, "ret_open_to_tau": 1.0},
                "active_features": ["gap_pct", "ret_open_to_tau"],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        # 同伴开盘行：分钟键为 0 占位（无 minute pack 语义上也应剥掉）
        peers = [
            {
                "stock_code": "A",
                "features_tau": {"gap_pct": 0.0, "ret_open_to_tau": 0.0},
            },
            {
                "stock_code": "B",
                "features_tau": {"gap_pct": 2.0, "ret_open_to_tau": 0.0},
            },
        ]
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-01-02": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=False
        ):
            z_lo = sp._cs_z_horizon_feats(
                {"gap_pct": 2.0, "ret_open_to_tau": 0.7},
                head="t30",
                trade_date="2026-01-02",
                stock_code="B",
            )
            z_hi = sp._cs_z_horizon_feats(
                {"gap_pct": 2.0, "ret_open_to_tau": 2.1},
                head="t30",
                trade_date="2026-01-02",
                stock_code="B",
            )
        # gap 有同伴真实分布 → 截面 z；ret_open 仅本票有值 → 保留 stamped，不塌成 √2
        self.assertAlmostEqual(float(z_lo["gap_pct"]), 1.0, places=5)
        self.assertAlmostEqual(float(z_hi["gap_pct"]), 1.0, places=5)
        self.assertAlmostEqual(float(z_lo["ret_open_to_tau"]), 0.7, places=5)
        self.assertAlmostEqual(float(z_hi["ret_open_to_tau"]), 2.1, places=5)
        self.assertNotAlmostEqual(float(z_lo["ret_open_to_tau"]), float(z_hi["ret_open_to_tau"]))


if __name__ == "__main__":
    unittest.main()
