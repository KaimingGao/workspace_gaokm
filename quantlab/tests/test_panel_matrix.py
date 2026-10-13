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
    def test_build_tau_panels_drops_minute_bars(self):
        """分钟路径 collect 后丢掉 minute_bars，避免满池 5m 常驻。"""
        from core.research.tc_ridge import build_tau_panels_from_bars

        day = "2024-02-15"
        bars = _bars(40, 10, 0)
        bars[-1]["date"] = day
        mins = []
        for hm in range(935, 1105, 5):
            h, m = divmod(hm, 100)
            if m >= 60:
                continue
            t = f"{h:02d}:{m:02d}:00"
            mins.append(
                {
                    "datetime": f"{day} {t}",
                    "date": day,
                    "time": t,
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": 100,
                }
            )
        item = {"code": "AAA", "bars": bars, "minute_bars": mins}
        build_tau_panels_from_bars(
            [item],
            tau_hm="10:30",
            tau_grid=["10:30"],
            min_history=12,
            include_alpha_tc=False,
        )
        self.assertNotIn("minute_bars", item)

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
            include_alpha_tc=False,
            gap_trigger_pct=0.5,
            head="test",
        )
        xs, ys_d, dates_d, metas_d = _stack_panels(
            build_tau_panels_from_bars(
                stock_bars,
                include_alpha_tc=False,
                gap_trigger_pct=0.5,
            )
        )
        self.assertGreaterEqual(n_stocks, 2)
        self.assertEqual(len(ys), len(ys_d))
        self.assertEqual(dates, [str(d)[:10] for d in dates_d])
        self.assertEqual(
            [int(m.get("theme_day") or 0) for m in metas],
            [int(m.get("theme_day") or 0) for m in metas_d],
        )
        from core.signal.factors.z_tc import to_z_tc_key

        index = {name: i for i, name in enumerate(names)}
        for key in ("gap_pct", "sector_gap_breadth", "theme_day", "gap_vs_sector"):
            canon = to_z_tc_key(key)
            j = index[canon]
            for i, row in enumerate(xs):
                raw = row.get(key) if isinstance(row, dict) else None
                got = float(X[i, j])
                if raw is None:
                    self.assertFalse(np.isfinite(got), canon)
                else:
                    self.assertAlmostEqual(got, float(raw), places=6, msg=canon)


class TestCoCompactBoundedFold(unittest.TestCase):
    def test_workers_match_serial_multiset(self):
        """并行有界折入与串行同样本集合（行序可因完成顺序不同）。"""
        from core.research.co_panel import CO_Z_FEATURES
        from core.research.panel_matrix import collect_co_compact

        def _pool():
            return [
                {"code": "AAA", "bars": _bars(80, 10, 0)},
                {"code": "BBB", "bars": _bars(80, 12, 1)},
                {"code": "CCC", "bars": _bars(80, 8, 2)},
                {"code": "DDD", "bars": _bars(80, 11, 3)},
                {"code": "EEE", "bars": _bars(80, 9, 4)},
            ]

        def _keys(ys, dates, metas):
            out = []
            for y, d, m in zip(ys, dates, metas):
                code = str((m or {}).get("stock_code") or "")
                out.append((code, str(d)[:10], round(float(y), 6)))
            return sorted(out)

        pool1 = _pool()
        X1, _n1, ys1, dates1, metas1, n1 = collect_co_compact(
            pool1,
            list(CO_Z_FEATURES),
            include_alpha_oo=True,
            gap_trigger_pct=0.5,
            workers=1,
        )
        # 8GB 路径：算完即丢 bars
        self.assertTrue(all("bars" not in it for it in pool1))
        pool2 = _pool()
        X2, _n2, ys2, dates2, metas2, n2 = collect_co_compact(
            pool2,
            list(CO_Z_FEATURES),
            include_alpha_oo=True,
            gap_trigger_pct=0.5,
            workers=2,
        )
        self.assertEqual(n1, n2)
        self.assertEqual(len(ys1), len(ys2))
        self.assertEqual(int(X1.shape[0]), len(ys1))
        self.assertEqual(int(X2.shape[0]), len(ys2))
        self.assertEqual(_keys(ys1, dates1, metas1), _keys(ys2, dates2, metas2))
        self.assertGreater(int(X1.shape[1]), len(CO_Z_FEATURES))

    def test_resolve_co_workers_caps_8gb(self):
        from unittest.mock import patch

        from core.research.panel_matrix import _resolve_co_workers

        with patch.dict(os.environ, {"CO_RIDGE_WORKERS": "2"}, clear=False):
            self.assertEqual(_resolve_co_workers(800, None), 2)
        # 硬顶 2：再高也会被压住
        self.assertEqual(_resolve_co_workers(800, 8), 2)
        self.assertEqual(_resolve_co_workers(2, None), 1)


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
        names = ["gap_pct", "theme_day", "feat_m", "yclose_loc"]
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
            feature_zscore=True,
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
