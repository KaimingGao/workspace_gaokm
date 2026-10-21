"""SUMP+SUMN≡1 共线防护：特征剔除、raw 误用拒预测、批塌缩。"""

from __future__ import annotations

import unittest

from core.signal.factors.sum_identity import (
    coef_names_for_raw_identity,
    cs_batch_preds_look_collapsed,
    drop_redundant_sum_share_names,
    filter_predict_active_names,
    finite_coef,
    is_redundant_sum_share_name,
    omit_redundant_from_coef_map,
    prep_meta_excluded,
    row_has_raw_sum_share_identity,
)


class TestSumIdentityNames(unittest.TestCase):
    def test_detect_psum_osumn(self):
        self.assertTrue(is_redundant_sum_share_name("PSUMP5"))
        self.assertTrue(is_redundant_sum_share_name("raw_atc_PSUMN20"))
        self.assertTrue(is_redundant_sum_share_name("raw_aoo_OSUMP10"))
        self.assertTrue(is_redundant_sum_share_name("OVSUMP5"))
        self.assertFalse(is_redundant_sum_share_name("PSUMD5"))
        self.assertFalse(is_redundant_sum_share_name("raw_atc_PVSUMD30"))
        self.assertFalse(is_redundant_sum_share_name("raw_atc_YKLEN"))

    def test_drop_keeps_sumd(self):
        names = [
            "raw_atc_PSUMP5",
            "raw_atc_PSUMN5",
            "raw_atc_PSUMD5",
            "raw_atc_YKLEN",
        ]
        kept, dropped = drop_redundant_sum_share_names(names)
        self.assertEqual(kept, ["raw_atc_PSUMD5", "raw_atc_YKLEN"])
        self.assertEqual(set(dropped), {"raw_atc_PSUMP5", "raw_atc_PSUMN5"})

    def test_filter_predict_drops_shares(self):
        names = ["raw_atc_PSUMP5", "raw_atc_PSUMD5", "raw_atc_YKLEN"]
        coefs = {
            "raw_atc_PSUMP5": 5.0,
            "raw_atc_PSUMD5": 0.2,
            "raw_atc_YKLEN": 1.0,
        }
        self.assertEqual(
            filter_predict_active_names(names, coefs),
            ["raw_atc_PSUMD5", "raw_atc_YKLEN"],
        )

    def test_finite_coef_and_omit_map(self):
        self.assertIsNone(finite_coef(None))
        self.assertIsNone(finite_coef(float("nan")))
        self.assertIsNone(finite_coef("x"))
        self.assertEqual(finite_coef(1.5), 1.5)
        coefs = omit_redundant_from_coef_map(
            {"raw_atc_PSUMD5": 0.2, "raw_atc_YKLEN": 1.0},
            ["raw_atc_PSUMP5", "raw_atc_PSUMN5", "raw_atc_SPARSE"],
        )
        self.assertNotIn("raw_atc_PSUMP5", coefs)
        self.assertNotIn("raw_atc_PSUMN5", coefs)
        self.assertIsNone(coefs.get("raw_atc_SPARSE"))
        self.assertEqual(coefs["raw_atc_PSUMD5"], 0.2)
        self.assertEqual(
            prep_meta_excluded(
                {
                    "dropped_sparse": ["a"],
                    "dropped_constant": ["b"],
                    "dropped_for_coverage": [],
                    "dropped_sum_identity": ["c"],
                }
            ),
            ["a", "b", "c"],
        )

    def test_filter_predict_skips_nonfinite(self):
        names = ["raw_atc_PSUMD5", "raw_atc_YKLEN", "raw_atc_BAD"]
        coefs = {
            "raw_atc_PSUMD5": 0.2,
            "raw_atc_YKLEN": float("nan"),
            "raw_atc_BAD": None,
        }
        self.assertEqual(
            filter_predict_active_names(names, coefs),
            ["raw_atc_PSUMD5"],
        )


class TestRawIdentityGuard(unittest.TestCase):
    def test_raw_share_pair_detected(self):
        row = {
            "raw_atc_PSUMP5": 0.45,
            "raw_atc_PSUMN5": 0.55,
            "raw_atc_YKLEN": 0.02,
        }
        self.assertTrue(row_has_raw_sum_share_identity(row))

    def test_raw_p_plus_d_detected(self):
        # 仅有 P+D：raw 下 D=2P−1
        row = {
            "raw_atc_PSUMP5": 0.6,
            "raw_atc_PSUMD5": 0.2,
        }
        self.assertTrue(row_has_raw_sum_share_identity(row))

    def test_cs_z_pair_not_detected(self):
        # 截面 Z 后近似相反、和≈0；且 Z_D≈2 Z_P ≠ 2P−1
        row = {
            "raw_atc_PSUMP5": 0.48,
            "raw_atc_PSUMN5": -0.48,
            "raw_atc_PSUMD5": 0.96,
        }
        self.assertFalse(row_has_raw_sum_share_identity(row))

    def test_leftover_raw_shares_ignored_when_not_in_model(self):
        # 面板仍带 SUMP/SUMN；模型只入了 SUMD / YKLEN。不能因 P+N≡1 拒预测。
        row = {
            "raw_atc_PSUMP5": 0.45,
            "raw_atc_PSUMN5": 0.55,
            "raw_atc_PSUMD5": 1.2,
            "raw_atc_YKLEN": 0.8,
        }
        coefs = {"raw_atc_PSUMD5": 0.2, "raw_atc_YKLEN": 1.0}
        names = coef_names_for_raw_identity(coefs)
        self.assertEqual(names, ["raw_atc_PSUMD5", "raw_atc_YKLEN"])
        self.assertFalse(row_has_raw_sum_share_identity(row, relevant_names=names))
        # β=None 的剔除残留同样不算入模
        coefs_none = {
            "raw_atc_PSUMP5": None,
            "raw_atc_PSUMN5": None,
            "raw_atc_YKLEN": 1.0,
        }
        self.assertEqual(
            coef_names_for_raw_identity(coefs_none),
            ["raw_atc_YKLEN"],
        )
        self.assertFalse(
            row_has_raw_sum_share_identity(
                row, relevant_names=coef_names_for_raw_identity(coefs_none)
            )
        )


class TestCollapseGuard(unittest.TestCase):
    def test_collapsed_far_from_intercept(self):
        preds = [6.3 + 0.01 * (i % 3) for i in range(12)]
        self.assertTrue(cs_batch_preds_look_collapsed(preds, intercept=0.08))

    def test_healthy_cross_section(self):
        preds = [0.08 + 0.4 * ((i % 5) - 2) for i in range(20)]
        self.assertFalse(cs_batch_preds_look_collapsed(preds, intercept=0.08))


class TestPredictRefusesRawSumIdentity(unittest.TestCase):
    def test_tau_predict_rows_returns_none(self):
        from core.research.tc_ridge_compact import _predict_rows

        fit = {
            "intercept": 0.08,
            "zscore_scope": "cross_section",
            "feature_zscore": True,
            "coefficients": {
                "raw_atc_PSUMP5": 5.5,
                "raw_atc_PSUMN5": 5.5,
                "raw_atc_YKLEN": 1.0,
            },
            "active_features": [
                "raw_atc_PSUMP5",
                "raw_atc_PSUMN5",
                "raw_atc_YKLEN",
            ],
        }
        raw_row = {
            "raw_atc_PSUMP5": 0.5,
            "raw_atc_PSUMN5": 0.5,
            "raw_atc_YKLEN": 0.01,
        }
        out = _predict_rows(fit, [raw_row])
        self.assertEqual(out, [None])

        # 正确截面 z：SUMP/SUMN 被丢弃，只剩 YKLEN
        z_row = {
            "raw_atc_PSUMP5": 0.2,
            "raw_atc_PSUMN5": -0.2,
            "raw_atc_YKLEN": 1.0,
        }
        out_z = _predict_rows(fit, [z_row])
        self.assertIsNotNone(out_z[0])
        self.assertAlmostEqual(float(out_z[0]), 0.08 + 1.0 * 1.0, places=5)

    def test_tau_predict_keeps_score_when_shares_not_in_model(self):
        from core.research.tc_ridge_compact import _predict_rows

        fit = {
            "intercept": 0.08,
            "zscore_scope": "cross_section",
            "feature_zscore": True,
            "coefficients": {
                "raw_atc_PSUMP5": None,
                "raw_atc_PSUMN5": None,
                "raw_atc_PSUMD5": 0.2,
                "raw_atc_YKLEN": 1.0,
            },
            "active_features": ["raw_atc_PSUMD5", "raw_atc_YKLEN"],
        }
        row = {
            "raw_atc_PSUMP5": 0.45,
            "raw_atc_PSUMN5": 0.55,
            "raw_atc_PSUMD5": 1.2,
            "raw_atc_YKLEN": 0.5,
        }
        out = _predict_rows(fit, [row])
        self.assertIsNotNone(out[0])
        self.assertAlmostEqual(float(out[0]), 0.08 + 0.2 * 1.2 + 1.0 * 0.5, places=5)

    def test_tau_predict_rows_raises_on_collapsed_batch(self):
        from core.research.tc_ridge_compact import _predict_rows

        fit = {
            "intercept": 0.08,
            "zscore_scope": "cross_section",
            "feature_zscore": True,
            # 无 SUMP 键，但用常数大系数模拟「漏 Z」塌缩
            "coefficients": {"raw_atc_CONSTBIAS": 6.0},
            "active_features": ["raw_atc_CONSTBIAS"],
        }
        rows = [{"raw_atc_CONSTBIAS": 1.0 + 0.001 * (i % 2)} for i in range(12)]
        with self.assertRaises(RuntimeError):
            _predict_rows(fit, rows)


if __name__ == "__main__":
    unittest.main()
