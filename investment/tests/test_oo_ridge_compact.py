"""ŷ_oo 紧凑矩阵 Ridge 与字典面板 OLS 的系数对齐。"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestOoRidgeCompact(unittest.TestCase):
    def test_matrix_matches_dict_panel(self) -> None:
        from core.research.factor_ols_fit import fit_factor_ols_from_panel
        from core.research.oo_ridge_compact import (
            OoPanelMatrix,
            fit_oo_ridge_matrix,
            predict_oo_matrix,
        )
        from core.signal.factors.alpha158 import ALPHA158_FACTOR_KEY
        from core.signal.factors.meta.health import unsourced_factor_names
        from core.signal.factors.meta.registry import registered_factor_names

        registered = list(registered_factor_names())
        for key in ("momentum", "ma_slope", "value"):
            self.assertIn(key, registered)
        filler = next(
            n
            for n in registered
            if n
            not in ("momentum", "ma_slope", "value", ALPHA158_FACTOR_KEY)
        )
        banned = unsourced_factor_names()
        rng = np.random.default_rng(7)
        n = 120
        rows = []
        ys = []
        dates = []
        for i in range(n):
            momentum = float(40.0 + rng.normal(0.0, 12.0))
            row = {
                "momentum": None if i == 3 else momentum,
                "ma_slope": momentum + float(rng.normal(0.0, 0.05)),
                "value": None if i % 11 == 0 else float(50.0 + rng.normal(0.0, 9.0)),
                filler: float(50.0 + rng.normal(0.0, 0.2)),
                "alpha158": 50.0,
                "raw_alpha158_KMID": float(rng.normal(0.0, 0.15)),
                "raw_other_junk": 1.0,
            }
            if i < 5:
                row.pop("raw_alpha158_KMID")
            y = 0.03 * (momentum - 50.0) + float(rng.normal(0.0, 0.4))
            rows.append(row)
            ys.append(y)
            dates.append(f"2024-01-{(i % 27) + 1:02d}")

        panel = OoPanelMatrix()
        panel.add_stock_rows(rows[:40], ys[:40], dates[:40])
        panel.add_stock_rows(rows[40:], ys[40:], dates[40:])
        x_mat, y_mat, names, _dates = panel.finalize()
        self.assertNotIn("alpha158", names)
        self.assertNotIn("raw_other_junk", names)
        self.assertIn("raw_alpha158_KMID", names)

        _model, report = fit_oo_ridge_matrix(
            x_mat,
            y_mat,
            names,
            horizon_days=1,
            ridge_lambda=1.0,
            min_samples=24,
        )
        stripped = [
            {k: v for k, v in row.items() if str(k) not in banned} for row in rows
        ]
        dict_report = fit_factor_ols_from_panel(
            stripped,
            ys,
            horizon_days=1,
            fundamentals_used=False,
            pit_fundamentals=False,
            mode="watching_pooled",
            ridge_lambda=1.0,
        )
        self.assertTrue(dict_report.get("success"))
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("sample_count"), dict_report.get("sample_count"))
        dict_coef = dict_report["coefficients"]
        mat_coef = report["coefficients"]
        active = [k for k, v in dict_coef.items() if v is not None]
        self.assertTrue(active)
        self.assertNotIn(filler, active)
        for key in active:
            self.assertAlmostEqual(float(mat_coef[key]), float(dict_coef[key]), places=5)
        self.assertAlmostEqual(
            float(report["intercept"]), float(dict_report["intercept"]), places=5
        )
        for key in active:
            self.assertAlmostEqual(
                float(report["zscore_means"][key]),
                float(dict_report["zscore_means"][key]),
                places=4,
            )
            self.assertAlmostEqual(
                float(report["zscore_stds"][key]),
                float(dict_report["zscore_stds"][key]),
                places=4,
            )

        from core.signal.return_score import ReturnScoreModel

        dict_model = ReturnScoreModel.from_ols_report(dict_report)
        self.assertIsNotNone(dict_model)
        probe = [0, 3, 11, 40]
        preds = predict_oo_matrix(_model, x_mat, names, probe)
        for i, pred in zip(probe, preds):
            row = {}
            for j, name in enumerate(names):
                val = float(x_mat[i, j])
                if np.isfinite(val):
                    row[name] = val
            self.assertEqual(dict_model.predict(row), pred)

    def test_column_collinearity_matches_row_dicts(self) -> None:
        from core.research.beta_accuracy import (
            apply_collinearity_on_columns,
            apply_collinearity_policy,
        )
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        a, b = TREND_FAMILY[0], TREND_FAMILY[1]
        xs = []
        ys = []
        for i in range(30):
            v = float(i)
            xs.append({a: v, b: v + 0.01 * i, "value": float(i % 3)})
            ys.append(v * 0.1)
        kept, dropped, _meta = apply_collinearity_policy(
            xs, [a, b, "value"], policy="drop_redundant", ys=ys
        )
        columns = {
            a: [row[a] for row in xs],
            b: [row[b] for row in xs],
        }
        kept2, dropped2, _meta2 = apply_collinearity_on_columns(
            columns, [a, b, "value"], ys, policy="drop_redundant"
        )
        self.assertEqual(kept2, kept)
        self.assertEqual(dropped2, dropped)
        self.assertEqual(len(dropped), 1)

    def test_parallel_blocks_match_serial(self) -> None:
        from core.research.oo_ridge_compact import assemble_oo_panels

        def bars_for(seed: int, n: int = 80):
            rng = np.random.default_rng(seed)
            px = 15.0 + seed
            out = []
            for i in range(n):
                o = px
                c = px * (1.0 + float(rng.normal(0.0, 0.01)))
                out.append(
                    {
                        "date": f"2023-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                        "open": o,
                        "high": max(o, c) * 1.01,
                        "low": min(o, c) * 0.99,
                        "close": c,
                        "volume": 1_000_000.0 + float(i),
                    }
                )
                px = c
            return out

        items = [(f"{i:06d}", bars_for(i)) for i in range(4)]
        x1, y1, names1, d1 = assemble_oo_panels(items, horizon_days=1, workers=1)
        x2, y2, names2, d2 = assemble_oo_panels(items, horizon_days=1, workers=2)
        self.assertEqual(names1, names2)
        self.assertEqual(d1, d2)
        self.assertEqual(y1.shape, y2.shape)
        self.assertTrue(np.allclose(y1, y2, equal_nan=True))
        self.assertTrue(np.allclose(x1, x2, equal_nan=True))

    def test_resolve_workers_caps_and_keeps_small_jobs_serial(self) -> None:
        import os
        from unittest.mock import patch

        from core.research.oo_ridge_compact import _resolve_workers

        with patch.dict(os.environ, {"OO_RIDGE_WORKERS": "2"}):
            self.assertEqual(_resolve_workers(300, None), 2)
            self.assertEqual(_resolve_workers(3, None), 1)
        self.assertEqual(_resolve_workers(300, 8), 3)
        self.assertEqual(_resolve_workers(1, 2), 1)

    def test_stack_aligns_extra_column_and_skips_empty(self) -> None:
        from core.research.oo_ridge_compact import OoPanelMatrix, _stack_blocks

        base = OoPanelMatrix().names
        extra = base + ["raw_alpha158_EXTRA_TEST"]
        n = 2
        x_base = np.ones((n, len(base)), dtype=np.float64)
        x_extra = np.concatenate(
            [np.ones((n, len(base))), np.full((n, 1), 7.0)], axis=1
        )
        empty = np.zeros((0, len(base)), dtype=np.float64)
        x, y, names, dates = _stack_blocks(
            [
                (empty, np.zeros((0,)), base, []),
                (x_base, np.array([1.0, 2.0]), base, ["2024-01-02", "2024-01-03"]),
                (x_extra, np.array([3.0, 4.0]), extra, ["2024-01-04", "2024-01-05"]),
            ]
        )
        self.assertEqual(names[-1], "raw_alpha158_EXTRA_TEST")
        self.assertEqual(dates, ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        self.assertEqual(y.tolist(), [1.0, 2.0, 3.0, 4.0])
        self.assertTrue(np.isnan(x[0, -1]))
        self.assertEqual(float(x[2, -1]), 7.0)

    def test_row_idx_matches_sliced_matrix(self) -> None:
        from core.research.oo_ridge_compact import fit_oo_ridge_matrix

        rng = np.random.default_rng(0)
        n = 80
        names = ["momentum", "value"]
        x = np.column_stack(
            [rng.normal(50, 12, n), rng.normal(50, 10, n)]
        )
        y = 0.02 * (x[:, 0] - 50.0) + rng.normal(0, 0.3, n)
        idx = list(range(0, n, 2))
        _m1, via_idx = fit_oo_ridge_matrix(
            x, y, names, row_idx=idx, min_samples=24, ridge_lambda=1.0, horizon_days=1
        )
        _m2, via_slice = fit_oo_ridge_matrix(
            x[idx], y[idx], names, min_samples=24, ridge_lambda=1.0, horizon_days=1
        )
        self.assertTrue(via_idx.get("success"))
        self.assertEqual(via_idx["coefficients"], via_slice["coefficients"])
        self.assertEqual(via_idx["intercept"], via_slice["intercept"])
        self.assertEqual(via_idx["sample_count"], via_slice["sample_count"])

    def test_stock_job_error_names_the_code(self) -> None:
        from core.research.oo_ridge_compact import _oo_stock_job

        with self.assertRaises(RuntimeError) as ctx:
            _oo_stock_job(("600000", [], "bad-horizon"))
        self.assertIn("600000", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
