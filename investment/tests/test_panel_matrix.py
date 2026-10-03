"""float64 面板 Ridge 与 dict OLS 同口径。"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 40, start: float = 10.0, seed: int = 0):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestCompactBreadth(unittest.TestCase):
    def test_tau_matrix_matches_dict_breadth(self):
        from core.research.panel_matrix import collect_tau_compact
        from core.research.tc_ridge import TAU_Z_FEATURES, _stack_panels, build_tau_panels_from_bars

        stock_bars = [
            {"code": "AAA", "bars": _bars(48, 10, 0)},
            {"code": "BBB", "bars": _bars(48, 12, 1)},
            {"code": "CCC", "bars": _bars(48, 8, 2)},
        ]
        X, names, ys, dates, metas, n_stocks = collect_tau_compact(
            stock_bars,
            list(TAU_Z_FEATURES),
            include_alpha158=False,
            gap_trigger_pct=0.5,
            head="test",
        )
        xs, ys_d, dates_d, metas_d = _stack_panels(
            build_tau_panels_from_bars(
                stock_bars, include_alpha158=False, gap_trigger_pct=0.5
            )
        )
        self.assertGreaterEqual(n_stocks, 2)
        self.assertEqual(len(ys), len(ys_d))
        self.assertEqual(dates, [str(d)[:10] for d in dates_d])
        self.assertEqual(
            [int(m.get("theme_day") or 0) for m in metas],
            [int(m.get("theme_day") or 0) for m in metas_d],
        )
        index = {name: i for i, name in enumerate(names)}
        for key in ("gap_pct", "sector_gap_breadth", "theme_day", "gap_vs_sector"):
            j = index[key]
            for i, row in enumerate(xs):
                raw = row.get(key) if isinstance(row, dict) else None
                got = float(X[i, j])
                if raw is None:
                    self.assertFalse(np.isfinite(got), key)
                else:
                    self.assertAlmostEqual(got, float(raw), places=6, msg=key)


class TestKeepallRidgeMatrix(unittest.TestCase):
    def test_matches_dict_ols_weights_missing_and_predict(self):
        from core.research.factor_ols_fit import fit_factor_ols_from_panel
        from core.research.panel_matrix import (
            fit_keepall_ridge_matrix,
            predict_ridge_matrix,
        )
        from core.research.tc_ridge import _predict_rows

        rng = np.random.default_rng(0)
        n = 40
        names = ["gap_pct", "theme_day", "mom3_pct", "yclose_loc"]
        X = rng.normal(size=(n, len(names)))
        X[0, 0] = np.nan
        X[3, 2] = np.nan
        X[:, 1] = (rng.random(n) > 0.7).astype(np.float64)
        y = (
            0.2 * np.nan_to_num(X[:, 0])
            - 0.1 * X[:, 1]
            + 0.05 * X[:, 3]
            + rng.normal(scale=0.05, size=n)
        )
        w = np.where(X[:, 1] > 0.5, 1.5, 1.0)
        rows = []
        for i in range(n):
            row = {}
            for j, name in enumerate(names):
                row[name] = None if not np.isfinite(X[i, j]) else float(X[i, j])
            rows.append(row)
        ys = [float(v) for v in y]
        weights = [float(v) for v in w]
        dict_fit = fit_factor_ols_from_panel(
            rows,
            ys,
            feature_names=list(names),
            ridge_lambda=1.0,
            standardize=True,
            sample_weights=weights,
            min_std_exempt=list(names),
            collinearity_policy="keep_all",
        )
        mat_fit = fit_keepall_ridge_matrix(
            X,
            ys,
            names,
            ridge_lambda=1.0,
            sample_weights=weights,
            min_std_exempt=list(names),
        )
        self.assertTrue(dict_fit.get("success"), dict_fit.get("error"))
        self.assertTrue(mat_fit.get("success"), mat_fit.get("error"))
        self.assertEqual(dict_fit.get("active_features"), mat_fit.get("active_features"))
        for name in dict_fit["active_features"]:
            self.assertAlmostEqual(
                float(dict_fit["coefficients"][name]),
                float(mat_fit["coefficients"][name]),
                places=5,
            )
        self.assertAlmostEqual(
            float(dict_fit["intercept"]), float(mat_fit["intercept"]), places=5
        )
        dict_pred = _predict_rows(dict_fit, rows[:4])
        mat_pred = predict_ridge_matrix(mat_fit, X[:4], names)
        self.assertEqual(len(dict_pred), len(mat_pred))
        for a, b in zip(dict_pred, mat_pred):
            self.assertAlmostEqual(float(a), float(b), places=5)


if __name__ == "__main__":
    unittest.main()
