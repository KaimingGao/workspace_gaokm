"""ŷ_τc LightGBM：包识别、权重对齐、CS-z 软弃权。"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from core.research.tc_lgb import (
    SCHEMA_TC_LGB,
    is_tc_lightgbm_blob,
    predict_tc_lgb_matrix,
)


class TestTcLightgbmBlob(unittest.TestCase):
    def test_detect_booster(self):
        blob = {
            "feature_names": ["z_τc_gap_pct", "raw_atc_GAP"],
            "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
            "schema": SCHEMA_TC_LGB,
        }
        self.assertTrue(is_tc_lightgbm_blob(blob))

    def test_booster_wins_over_legacy_ridge_schema(self):
        """遗留 schema=tau_ridge_* 但已有 booster → 仍认 LightGBM。"""
        blob = {
            "feature_names": ["z_τc_gap_pct"],
            "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
            "schema": "tau_ridge_v15",
        }
        self.assertTrue(is_tc_lightgbm_blob(blob))

    def test_reject_ridge_coefs_only(self):
        self.assertFalse(
            is_tc_lightgbm_blob(
                {
                    "coefficients": {"z_τc_gap_pct": 0.1},
                    "intercept": 0.0,
                    "schema": "tau_ridge_v15",
                }
            )
        )


class TestTcLgbFitWeights(unittest.TestCase):
    def test_l2_keep_mask_slices_theme_weights(self):
        """L2 丢行后 theme 权重须跟 keep_mask 切片，不能退回全 1。"""
        from core.research.tc_lgb import fit_tc_lgb_matrix

        n = 20
        x = np.random.randn(n, 2).astype(np.float64)
        y = np.random.randn(n).astype(np.float64)
        names = ["f0", "f1"]
        dates = [f"2024-01-{i+1:02d}" for i in range(n)]
        # 偶数行权重 2，奇数 1 → 丢奇数后应仍见 2（保留 10 ≥ need=8）
        weights = [2.0 if i % 2 == 0 else 1.0 for i in range(n)]
        keep = np.array([i % 2 == 0 for i in range(n)], dtype=bool)

        captured = {}

        def _fake_align(x_c, y_c, dates_c, clock="open"):
            return (
                np.asarray(x_c)[keep],
                np.asarray(y_c)[keep],
                [dates_c[i] for i in range(len(dates_c)) if keep[i]],
                keep,
            )

        def _fake_cs(x_c, active, dates_c, clock="open", **_kw):
            captured["inplace"] = bool(_kw.get("inplace"))
            return np.asarray(x_c, dtype=np.float64)

        def _fake_fit(x_z, y_z, w, **kwargs):
            captured["w"] = np.asarray(w, dtype=np.float64).copy()
            # 最小假 booster 接口：下游 pack 需要可序列化对象
            class _B:
                def save_model(self, path):
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("tree\n")

                def feature_importance(self, importance_type="gain"):
                    return np.ones(x_z.shape[1], dtype=np.float64)

            return _B(), np.ones(x_z.shape[1], dtype=np.float64)

        with mock.patch(
            "core.research.materialize.cube_z.align_fit_rows_to_l2",
            side_effect=_fake_align,
        ), mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=_fake_cs,
        ), mock.patch(
            "core.research.horizon_lgb._fit_lightgbm",
            side_effect=_fake_fit,
        ), mock.patch(
            "core.research.horizon_lgb._gain_from_booster",
            return_value=np.ones(2, dtype=np.float64),
        ):
            blob, rep = fit_tc_lgb_matrix(
                x,
                y,
                names,
                row_dates=dates,
                sample_weights=weights,
                cross_section_zscore=True,
                min_samples=4,
            )
        self.assertTrue(rep.get("success"), rep)
        self.assertIsNotNone(blob)
        w = captured.get("w")
        self.assertIsNotNone(w)
        self.assertEqual(int(w.shape[0]), int(keep.sum()))
        self.assertTrue(np.allclose(w, 2.0), w)
        self.assertTrue(captured.get("inplace"))


class TestTcLgbPredictSoftMiss(unittest.TestCase):
    def test_materialize_required_returns_none_preds(self):
        from core.research.materialize.consumer import MaterializeRequired

        blob = {
            "feature_names": ["f0"],
            "booster": {"format": "lightgbm_string", "payload_b64": "YQ=="},
            "schema": SCHEMA_TC_LGB,
            "zscore_scope": "cross_section",
        }
        x = np.ones((3, 1), dtype=np.float64)
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=MaterializeRequired({"error": "no cube"}),
        ):
            out = predict_tc_lgb_matrix(
                blob, x, ["f0"], [0, 1, 2], row_dates=dates, clock="open"
            )
        self.assertEqual(out, [None, None, None])


class TestTcRefuseRidgeScore(unittest.TestCase):
    def test_predict_explain_refuse_ridge_coefs(self):
        from core.research.tc_model import explain_tau_prediction, predict_tau_from_features

        doc = {
            "return_model": {
                "coefficients": {"gap_pct": 0.2},
                "intercept": 0.1,
                "schema": "tau_ridge_v15",
            }
        }
        self.assertIsNone(
            predict_tau_from_features({"gap_pct": 1.0}, model_doc=doc)
        )
        self.assertIsNone(
            explain_tau_prediction({"gap_pct": 1.0}, model_doc=doc)
        )


if __name__ == "__main__":
    unittest.main()
