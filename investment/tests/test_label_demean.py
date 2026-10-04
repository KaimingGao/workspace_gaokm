"""训练标签 label_demean 开关。"""

from __future__ import annotations

import unittest

import numpy as np


class TestLabelDemean(unittest.TestCase):
    def test_resolve_and_annotate(self):
        from core.research.label_demean import (
            annotate_label_demean,
            resolve_label_demean,
        )

        self.assertTrue(resolve_label_demean(label_demean=True))
        self.assertFalse(resolve_label_demean(label_demean=False))
        self.assertTrue(
            resolve_label_demean({"label_demean": True}, default=False)
        )
        self.assertTrue(
            resolve_label_demean({"y_demeaned": True}, default=False)
        )
        self.assertFalse(
            resolve_label_demean({"y_demeaned": False}, default=True)
        )
        d: dict = {}
        annotate_label_demean(d, enabled=True, y_mean=0.0123456)
        self.assertTrue(d["label_demean"])
        self.assertTrue(d["y_demeaned"])
        self.assertAlmostEqual(d["y_label_mean"], 0.012346, places=6)
        annotate_label_demean(d, enabled=False, y_mean=1.0)
        self.assertFalse(d["label_demean"])
        self.assertFalse(d["y_demeaned"])
        self.assertEqual(d["y_label_mean"], 0.0)

    def test_demean_and_restore_intercept(self):
        from core.research.label_demean import (
            demean_labels,
            restore_intercept_after_demean,
        )

        y = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        yd, mu = demean_labels(y)
        self.assertAlmostEqual(mu, 0.2, places=9)
        np.testing.assert_allclose(yd, [-0.1, 0.0, 0.1], atol=1e-12)
        fit = restore_intercept_after_demean({"intercept": -0.05}, mu)
        self.assertAlmostEqual(fit["intercept_demeaned"], -0.05, places=6)
        self.assertAlmostEqual(fit["intercept"], 0.15, places=6)
        self.assertTrue(fit["y_demeaned"])
        self.assertAlmostEqual(fit["y_label_mean"], 0.2, places=6)

    def test_oo_ridge_matrix_respects_flag(self):
        from core.research.oo_ridge_compact import fit_oo_ridge_matrix

        rng = np.random.default_rng(7)
        n = 48
        # 因子量纲约 0–100（_prepare_matrix min_std=5）
        x = np.column_stack(
            [
                50.0 + 12.0 * rng.normal(size=n),
                50.0 + 10.0 * rng.normal(size=n),
            ]
        )
        y = 0.15 + 0.02 * (x[:, 0] - 50.0) - 0.01 * (x[:, 1] - 50.0)
        y = y + rng.normal(scale=0.05, size=n)
        names = ["momentum", "value"]
        _m_off, off = fit_oo_ridge_matrix(
            x, y, names, ridge_lambda=1.0, min_samples=24, label_demean=False
        )
        _m_on, on = fit_oo_ridge_matrix(
            x, y, names, ridge_lambda=1.0, min_samples=24, label_demean=True
        )
        self.assertTrue(off.get("success"), off.get("error"))
        self.assertTrue(on.get("success"), on.get("error"))
        self.assertFalse(off.get("y_demeaned"))
        self.assertTrue(on.get("y_demeaned"))
        self.assertAlmostEqual(float(on.get("y_label_mean") or 0.0), float(y.mean()), places=4)
        # 截距已加回 μ：两路预测水平应接近（斜率空间一致）
        self.assertAlmostEqual(
            float(on.get("intercept") or 0.0),
            float(on.get("intercept_demeaned") or 0.0) + float(on.get("y_label_mean") or 0.0),
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
