"""Arm·R：日内名次、岭回归求解，以及模型中心临时权重。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core.research.yhat_factory_weights import ENV_RUN_ID, save_ridge_pending
from core.research.yhat_ridge import (
    average_ranks,
    dense_rank_targets,
    fit_ridge,
    predict_ridge,
)
from core.research.yhat_tf_desk import factory_scope


class TestRidgeMath(unittest.TestCase):
    def test_average_ranks_break_ties(self):
        ranks = average_ranks(np.array([1.0, 3.0, 3.0, 2.0]))
        self.assertEqual(list(ranks), [1.0, 3.5, 3.5, 2.0])

    def test_dense_rank_skips_short_days(self):
        y = np.array([1.0, 2.0, 3.0, 9.0])
        dates = ["2024-01-02", "2024-01-02", "2024-01-02", "2024-01-03"]
        out, keep = dense_rank_targets(y, dates, [0, 1, 2, 3], min_names=3)
        self.assertEqual(keep, [0, 1, 2])
        self.assertEqual(list(out[:3]), [1.0, 2.0, 3.0])
        self.assertEqual(float(out[3]), 9.0)

    def test_fit_recovers_line(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=(40, 2))
        y = 0.5 + 1.5 * x[:, 0] - 0.25 * x[:, 1]
        solved = fit_ridge(x, y, 0.0)
        self.assertIsNotNone(solved)
        intercept, coef = solved
        pred = predict_ridge(x, intercept, coef)
        self.assertLess(float(np.max(np.abs(pred - y))), 1e-6)

    def test_predict_skips_missing_cells(self):
        x = np.array([[1.0, np.nan], [np.nan, np.nan]])
        pred = predict_ridge(x, 0.5, [2.0, 3.0])
        self.assertAlmostEqual(float(pred[0]), 2.5)
        self.assertTrue(np.isnan(pred[1]))

    def test_predict_complete_row_rejects_partial(self):
        x = np.array([[1.0, np.nan], [1.0, 2.0]])
        pred = predict_ridge(x, 0.5, [2.0, 3.0], require_complete=True)
        self.assertTrue(np.isnan(pred[0]))
        self.assertAlmostEqual(float(pred[1]), 0.5 + 2.0 + 6.0)


def _keep_l2(x, y, dates, clock="open", **_kwargs):
    days = [str(d)[:10] for d in dates]
    return (
        np.asarray(x, dtype=np.float64),
        np.asarray(y, dtype=np.float64),
        days,
        np.ones(len(days), dtype=bool),
    )


class TestRidgeHoldout(unittest.TestCase):
    def test_regression_arm_saves_coef(self):
        from scripts.run_yhat_transformer_holdout import _run_arm_r

        n = 36
        rng = np.random.default_rng(1)
        x = rng.normal(size=(n, 2))
        y = 0.3 + 1.2 * x[:, 0] - 0.5 * x[:, 1]
        fac = SimpleNamespace(
            X=x,
            y=y,
            feature_names=["a", "b"],
            dates=["2024-01-02"] * 24 + ["2024-01-03"] * 12,
            codes=[f"c{i:03d}" for i in range(n)],
            n=n,
        )

        def _identity(X, names, dates, clock="open", **_kwargs):
            return np.array(X, dtype=np.float64, copy=True)

        with tempfile.TemporaryDirectory() as td:
            env = os.environ.get(ENV_RUN_ID)
            os.environ[ENV_RUN_ID] = "ridge-run"
            try:
                with mock.patch(
                    "core.research.materialize.cube_z.align_fit_rows_to_l2",
                    side_effect=_keep_l2,
                ), mock.patch(
                    "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
                    side_effect=_identity,
                ), mock.patch(
                    "core.research.yhat_tf_desk.YHAT_R_DIR", td
                ), mock.patch(
                    "core.research.yhat_tf_desk.YHAT_R_LAST_PATH",
                    os.path.join(td, "last_status.json"),
                ):
                    out = _run_arm_r(
                        fac,
                        list(range(24)),
                        list(range(24, n)),
                        head_id="oo",
                        rank=False,
                        ridge_alpha=0.0,
                    )
                    self.assertTrue(out.get("success"), out.get("error"))
                    self.assertEqual(out.get("arm"), "R_ridge")
                    self.assertEqual(out.get("n_eval"), 12)
                    pending = os.path.join(td, "oo.weights.pending", "ridge.json")
                    with open(pending, encoding="utf-8") as f:
                        blob = json.load(f)
                    pred = predict_ridge(
                        fac.X[24:], blob["intercept"], blob["coef"]
                    )
                    corr = float(np.corrcoef(pred, y[24:])[0, 1])
                    self.assertGreater(corr, 0.99)
            finally:
                if env is None:
                    os.environ.pop(ENV_RUN_ID, None)
                else:
                    os.environ[ENV_RUN_ID] = env
        self.assertEqual(len(blob["coef"]), 2)
        self.assertEqual(blob["feature_names"], ["a", "b"])

    def test_cs_z_skips_rows_outside_train_and_holdout(self):
        """特征垫根留在面板里，但不进截面标准化。"""
        from core.research.materialize.consumer import MaterializeRequired
        from scripts.run_yhat_transformer_holdout import _run_arm_r

        early = "2022-07-01"
        train_day = "2024-01-02"
        test_day = "2024-06-03"
        n_early, n_train, n_test = 30, 30, 12
        n = n_early + n_train + n_test
        rng = np.random.default_rng(2)
        x = rng.normal(size=(n, 2))
        y = 0.2 + x[:, 0]
        dates = [early] * n_early + [train_day] * n_train + [test_day] * n_test
        fac = SimpleNamespace(
            X=x,
            y=y,
            feature_names=["a", "b"],
            dates=dates,
            codes=[f"c{i:03d}" for i in range(n)],
            n=n,
        )
        seen: list = []

        def _z(X, names, dates_in, clock="open", **_kwargs):
            days = [str(d)[:10] for d in dates_in]
            seen.extend(days)
            if early in days:
                raise MaterializeRequired({"error": "cube miss 2022-07-01|open"})
            return np.array(X, dtype=np.float64, copy=True)

        with mock.patch(
            "core.research.materialize.cube_z.align_fit_rows_to_l2",
            side_effect=_keep_l2,
        ), mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=_z,
        ):
            out = _run_arm_r(
                fac,
                list(range(n_early, n_early + n_train)),
                list(range(n_early + n_train, n)),
                head_id="oo",
                rank=False,
                ridge_alpha=0.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out.get("n_train"), n_train)
        self.assertEqual(out.get("n_eval"), n_test)
        self.assertNotIn(early, seen)
        self.assertIn(train_day, seen)
        self.assertIn(test_day, seen)

    def test_gap_on_or_after_cube_start_still_fails(self):
        """起点当天及之后的缺日仍整单失败，不随窗头一起丢掉。"""
        from core.research.materialize.consumer import MaterializeRequired
        from scripts.run_yhat_transformer_holdout import _run_arm_r

        n_train, n_test = 30, 12
        n = n_train + n_test
        rng = np.random.default_rng(4)
        fac = SimpleNamespace(
            X=rng.normal(size=(n, 2)),
            y=rng.normal(size=n),
            feature_names=["a", "b"],
            dates=["2024-01-02"] * n_train + ["2024-06-03"] * n_test,
            codes=[f"c{i:03d}" for i in range(n)],
            n=n,
        )

        def _z(X, names, dates, clock="open", **_kwargs):
            raise MaterializeRequired({"error": "cube miss 2024-01-02|open"})

        with mock.patch(
            "core.research.materialize.cube_z.align_fit_rows_to_l2",
            side_effect=_keep_l2,
        ), mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=_z,
        ):
            out = _run_arm_r(
                fac,
                list(range(n_train)),
                list(range(n_train, n)),
                head_id="oo",
            )
        self.assertFalse(out.get("success"))
        self.assertIn("MaterializeRequired", str(out.get("error") or ""))
        self.assertIn("2024-01-02", str(out.get("error") or ""))

    def test_regression_drops_incomplete_rows(self):
        """缺测行不进训练；Holdout 整行都缺则不进指标。"""
        from scripts.run_yhat_transformer_holdout import _run_arm_r

        n_train, n_test = 30, 12
        n = n_train + n_test
        rng = np.random.default_rng(3)
        x = rng.normal(size=(n, 2))
        x[:6, 1] = np.nan
        x[-1, :] = np.nan
        y = 0.2 + np.where(np.isfinite(x[:, 0]), x[:, 0], 0.0)
        fac = SimpleNamespace(
            X=x,
            y=y,
            feature_names=["a", "b"],
            dates=["2024-01-02"] * n_train + ["2024-06-03"] * n_test,
            codes=[f"c{i:03d}" for i in range(n)],
            n=n,
        )

        def _identity(X, names, dates, clock="open", **_kwargs):
            return np.array(X, dtype=np.float64, copy=True)

        with mock.patch(
            "core.research.materialize.cube_z.align_fit_rows_to_l2",
            side_effect=_keep_l2,
        ), mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=_identity,
        ):
            out = _run_arm_r(
                fac,
                list(range(n_train)),
                list(range(n_train, n)),
                head_id="oo",
                rank=False,
                ridge_alpha=0.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out.get("n_train"), n_train - 6)
        self.assertEqual(out.get("n_eval"), n_test - 1)
        self.assertEqual(out.get("n_features"), 2)
        self.assertIn("缺测跳过", str(out.get("note") or ""))

    def test_tc_matches_main_keepall_and_drops_partial_row(self):
        """ŷ_τc：主题日加权，Holdout 缺任一入模列则整行不进指标。"""
        from scripts.run_yhat_transformer_holdout import _run_arm_r

        n_train, n_test = 30, 12
        n = n_train + n_test
        rng = np.random.default_rng(5)
        gap = rng.normal(size=n)
        theme = np.zeros(n, dtype=np.float64)
        theme[:8] = 1.0
        x = np.column_stack([gap, theme])
        x[-1, 0] = np.nan
        y = 0.2 + np.where(np.isfinite(gap), gap, 0.0)
        fac = SimpleNamespace(
            X=x,
            y=y,
            feature_names=["z_τc_gap_pct", "z_τc_theme_day"],
            dates=["2024-01-02"] * n_train + ["2024-06-03"] * n_test,
            codes=[f"c{i:03d}" for i in range(n)],
            n=n,
        )

        def _identity(X, names, dates, clock="open", **_kwargs):
            return np.array(X, dtype=np.float64, copy=True)

        with mock.patch(
            "core.research.materialize.cube_z.align_fit_rows_to_l2",
            side_effect=_keep_l2,
        ), mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=_identity,
        ):
            out = _run_arm_r(
                fac,
                list(range(n_train)),
                list(range(n_train, n)),
                head_id="tc",
                rank=False,
                ridge_alpha=1.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out.get("n_train"), n_train)
        self.assertEqual(out.get("n_eval"), n_test - 1)
        self.assertEqual(out.get("n_features"), 2)
        self.assertIn("主题日×1.5", str(out.get("note") or ""))
        self.assertIn("缺列整行不算", str(out.get("note") or ""))

    def test_pending_requires_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.pop(ENV_RUN_ID, None)
            try:
                with factory_scope("R"), mock.patch(
                    "core.research.yhat_tf_desk.YHAT_R_DIR", td
                ):
                    saved = save_ridge_pending(
                        {
                            "alpha": 1.0,
                            "intercept": 0.0,
                            "coef": [1.0],
                            "feature_names": ["a"],
                        },
                        {"head": "oo"},
                    )
            finally:
                if env is not None:
                    os.environ[ENV_RUN_ID] = env
        self.assertFalse(saved)
