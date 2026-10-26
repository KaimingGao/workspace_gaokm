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

    def test_cs_z_tau_skips_open_fallback(self):
        """有 τ 且同钟同伴不足时，不用开盘池 μ/σ，直接弃权。"""
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
            {"stock_code": "A", "features_tau": {"gap_pct": 0.0}},
            {"stock_code": "B", "features_tau": {"gap_pct": 2.0}},
        ]
        loads = []

        def _load(day, codes, clock="open", **_kw):
            loads.append(str(clock))
            return None

        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-01-02": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.t0.score_policy._watching_codes_for_z",
            return_value=["A", "B"],
        ), mock.patch(
            "core.research.day_cube.load_day_cube", side_effect=_load
        ), mock.patch.object(
            sp, "_tau_clock_peer_rows", return_value=[]
        ):
            z = sp._cs_z_horizon_feats(
                {"gap_pct": 2.0},
                head="t30",
                trade_date="2026-01-02",
                stock_code="B",
                tau_hm="10:30",
            )
        self.assertIsNone(z)
        self.assertTrue(loads)
        self.assertNotIn("open", loads)

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

    def test_sparse_raw_ath_becomes_zero_not_raw_level(self):
        """开盘同伴没有 raw_ath_* 时，不得把 235 分钟或价位比 1 当成 z。"""
        from core.t0 import score_policy as sp

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "gap_pct": 0.1,
                    "ret_open_to_tau": 1.0,
                    "raw_ath_CLOSE0": -1.5,
                    "raw_ath_SESSION_REMAIN": -0.01,
                },
                "active_features": [
                    "gap_pct",
                    "ret_open_to_tau",
                    "raw_ath_CLOSE0",
                    "raw_ath_SESSION_REMAIN",
                ],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        peers = [
            {"stock_code": "A", "features_tau": {"gap_pct": 0.0}},
            {"stock_code": "B", "features_tau": {"gap_pct": 2.0}},
        ]
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-01-02": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=False
        ):
            z = sp._cs_z_horizon_feats(
                {
                    "gap_pct": 2.0,
                    "ret_open_to_tau": 0.7,
                    "raw_ath_CLOSE0": 1.01,
                    "raw_ath_SESSION_REMAIN": 235.0,
                },
                head="t30",
                trade_date="2026-01-02",
                stock_code="B",
            )
        self.assertAlmostEqual(float(z["gap_pct"]), 1.0, places=5)
        self.assertAlmostEqual(float(z["ret_open_to_tau"]), 0.7, places=5)
        self.assertAlmostEqual(float(z["raw_ath_CLOSE0"]), 0.0, places=5)
        self.assertAlmostEqual(float(z["raw_ath_SESSION_REMAIN"]), 0.0, places=5)

    def test_tau_lake_peers_zscore_raw_ath(self):
        """Cube miss 时用同钟 L1 同伴给 raw_ath_* 做截面 z。"""
        from core.t0 import score_policy as sp

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "raw_ath_CLOSE0": -1.5,
                    "raw_ath_SESSION_REMAIN": -0.01,
                },
                "active_features": ["raw_ath_CLOSE0", "raw_ath_SESSION_REMAIN"],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        lake = [
            {
                "stock_code": "A",
                "features_tau": {
                    "raw_ath_CLOSE0": 0.98,
                    "raw_ath_SESSION_REMAIN": 235.0,
                },
            },
            {
                "stock_code": "B",
                "features_tau": {
                    "raw_ath_CLOSE0": 1.00,
                    "raw_ath_SESSION_REMAIN": 235.0,
                },
            },
            {
                "stock_code": "C",
                "features_tau": {
                    "raw_ath_CLOSE0": 1.02,
                    "raw_ath_SESSION_REMAIN": 235.0,
                },
            },
        ]
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-10-16": []}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=False
        ), mock.patch.object(
            sp, "_tau_clock_peer_rows", return_value=lake
        ):
            z = sp._cs_z_horizon_feats(
                {
                    "raw_ath_CLOSE0": 1.02,
                    "raw_ath_SESSION_REMAIN": 235.0,
                },
                head="t30",
                trade_date="2026-10-16",
                stock_code="C",
                tau_hm="09:35",
            )
        # 同钟剩余分钟无截面差 → 0；价位比按同伴总体 z，不是原始 1.02
        self.assertAlmostEqual(float(z["raw_ath_SESSION_REMAIN"]), 0.0, places=5)
        self.assertGreater(float(z["raw_ath_CLOSE0"]), 0.5)
        self.assertLess(float(z["raw_ath_CLOSE0"]), 2.0)
        self.assertNotAlmostEqual(float(z["raw_ath_CLOSE0"]), 1.02, places=2)

    def test_partial_cube_zscores_theme_day_when_cover_incomplete(self):
        """立方缺窗头列时，已有列仍套 μ/σ。theme_day 不得以 raw 0/1 进树。"""
        from core.t0 import score_policy as sp

        class _Cube:
            names = ["z_τh_theme_day", "theme_day", "gap_pct"]
            z_mean = [0.2, 0.2, 0.0]
            z_std = [0.4, 0.4, 1.0]

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "z_τh_theme_day": -1.0,
                    "gap_pct": 0.1,
                    "raw_ath_RET_LAST_30": 0.2,
                },
                "active_features": [
                    "z_τh_theme_day",
                    "gap_pct",
                    "raw_ath_RET_LAST_30",
                ],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        peers = [
            {
                "stock_code": "A",
                "features_tau": {"gap_pct": 0.0, "raw_ath_RET_LAST_30": 0.0},
            },
            {
                "stock_code": "B",
                "features_tau": {"gap_pct": 10.0, "raw_ath_RET_LAST_30": 0.0},
            },
            {
                "stock_code": "C",
                "features_tau": {"gap_pct": 2.0, "raw_ath_RET_LAST_30": 2.0},
            },
        ]
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-10-23": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.t0.score_policy._watching_codes_for_z",
            return_value=["A", "B", "C"],
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch.object(
            sp, "_tau_clock_peer_rows", return_value=peers
        ):
            z0 = sp._cs_z_horizon_feats(
                {"theme_day": 0.0, "gap_pct": 2.0, "raw_ath_RET_LAST_30": 2.0},
                head="t30",
                trade_date="2026-10-23",
                stock_code="C",
                tau_hm="09:35",
            )
            z1 = sp._cs_z_horizon_feats(
                {"theme_day": 1.0, "gap_pct": 2.0, "raw_ath_RET_LAST_30": 2.0},
                head="t30",
                trade_date="2026-10-23",
                stock_code="C",
                tau_hm="09:35",
            )
        # (0-0.2)/0.4 = -0.5；(1-0.2)/0.4 = 2.0。不是 raw 0/1。
        self.assertAlmostEqual(float(z0["z_τh_theme_day"]), -0.5, places=5)
        self.assertAlmostEqual(float(z1["z_τh_theme_day"]), 2.0, places=5)
        # gap 走立方 μ=0 σ=1，不是同伴 z
        self.assertAlmostEqual(float(z0["gap_pct"]), 2.0, places=5)
        # 立方没有的 raw_ath 仍用同钟同伴
        self.assertNotAlmostEqual(float(z0["raw_ath_RET_LAST_30"]), 2.0, places=2)

    def test_no_cube_no_peers_does_not_keep_raw_theme_day(self):
        """无立方且无同钟同伴时弃权，不用开盘池把 gap 打成截面 z。"""
        from core.t0 import score_policy as sp

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {"z_τh_theme_day": -1.0, "gap_pct": 0.1},
                "active_features": ["z_τh_theme_day", "gap_pct"],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        peers = [
            {"stock_code": "A", "features_tau": {"gap_pct": 0.0}},
            {"stock_code": "C", "features_tau": {"gap_pct": 2.0}},
        ]
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-10-23": peers}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=False
        ), mock.patch.object(
            sp, "_tau_clock_peer_rows", return_value=[]
        ):
            z = sp._cs_z_horizon_feats(
                {"theme_day": 1.0, "gap_pct": 2.0},
                head="t30",
                trade_date="2026-10-23",
                stock_code="C",
                tau_hm="09:35",
            )
        self.assertIsNone(z)

    def test_cube_only_when_peers_missing(self):
        """没有同伴时，立方已套上的列仍然出分，缺列记 0。"""
        from core.t0 import score_policy as sp

        class _Cube:
            names = ["z_τh_theme_day", "theme_day", "gap_pct"]
            z_mean = [0.2, 0.2, 0.0]
            z_std = [0.4, 0.4, 1.0]

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "z_τh_theme_day": -1.0,
                    "gap_pct": 0.1,
                    "raw_ath_RET_LAST_30": 0.2,
                },
                "active_features": [
                    "z_τh_theme_day",
                    "gap_pct",
                    "raw_ath_RET_LAST_30",
                ],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        with mock.patch.object(sp, "_POOL_FEAT_CACHE", {"2026-10-23": []}), mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.day_cube.enabled", return_value=True
        ), mock.patch(
            "core.t0.score_policy._watching_codes_for_z",
            return_value=["A", "C"],
        ), mock.patch(
            "core.research.day_cube.load_day_cube", return_value=_Cube()
        ), mock.patch.object(
            sp, "_tau_clock_peer_rows", return_value=[]
        ):
            z = sp._cs_z_horizon_feats(
                {"theme_day": 0.0, "gap_pct": 2.0, "raw_ath_RET_LAST_30": 9.0},
                head="t30",
                trade_date="2026-10-23",
                stock_code="C",
                tau_hm="09:35",
            )
        self.assertIsNotNone(z)
        self.assertAlmostEqual(float(z["z_τh_theme_day"]), -0.5, places=5)
        self.assertAlmostEqual(float(z["theme_day"]), -0.5, places=5)
        self.assertAlmostEqual(float(z["raw_ath_RET_LAST_30"]), 0.0, places=5)

    def test_rescore_partial_cube_standardizes_theme_day(self):
        from core.signal.cross_section_rescore import _rescore_one_horizon

        class _Cube:
            names = ["z_τh_theme_day", "theme_day", "gap_pct"]
            z_mean = [0.2, 0.2, 0.0]
            z_std = [0.4, 0.4, 1.0]
            codes = ["A", "C"]

        doc = {
            "backend": "ridge",
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "z_τh_theme_day": -1.0,
                    "gap_pct": 0.1,
                    "raw_ath_RET_LAST_30": 0.2,
                },
                "active_features": [
                    "z_τh_theme_day",
                    "gap_pct",
                    "raw_ath_RET_LAST_30",
                ],
                "feature_names": [
                    "z_τh_theme_day",
                    "gap_pct",
                    "raw_ath_RET_LAST_30",
                ],
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        seen = {}

        def _pred(head, feats, model_doc=None):
            seen["theme"] = feats.get("z_τh_theme_day")
            seen["bare"] = feats.get("theme_day")
            seen["ath"] = feats.get("raw_ath_RET_LAST_30")
            return 0.42

        items = [
            {
                "stock_code": "C",
                "features_tau": {
                    "theme_day": 1.0,
                    "gap_pct": 2.0,
                    "raw_ath_RET_LAST_30": 5.0,
                },
            }
        ]
        with mock.patch(
            "core.research.horizon_ridge.load_ridge_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ), mock.patch(
            "core.research.horizon_ridge.predict_ridge_from_features", side_effect=_pred
        ), mock.patch(
            "core.research.horizon_ridge._doc_backend", return_value="ridge"
        ), mock.patch(
            "core.research.horizon_ridge.explain_ridge_prediction", return_value=None
        ):
            _rescore_one_horizon(items, "t30", day_cube=_Cube())
        self.assertAlmostEqual(float(seen["theme"]), 2.0, places=5)
        self.assertAlmostEqual(float(seen["bare"]), 2.0, places=5)
        self.assertEqual(float(seen["ath"]), 0.0)
        self.assertAlmostEqual(float(items[0]["y_τ30"]), 0.42, places=5)


if __name__ == "__main__":
    unittest.main()
