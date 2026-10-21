"""面板工具测试：指纹 + Ridge/OLS 矩阵。合并自 panel_fingerprint + panel_matrix。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


# --- from test_panel_fingerprint.py ---
def _bars__panel_fingerprint(n: int = 80, start: float = 10.0, seed: int = 0):
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

class TestPanelFingerprint(unittest.TestCase):
    def test_bars_fingerprint_changes_on_new_day(self) -> None:
        from core.research.panel_fingerprint import bars_fingerprint

        a = _bars__panel_fingerprint(5)
        b = list(a)
        b.append(
            {
                "date": "2024-02-10",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 100,
            }
        )
        self.assertNotEqual(bars_fingerprint(a), bars_fingerprint(b))
        self.assertEqual(bars_fingerprint(a), bars_fingerprint(list(a)))

    def test_mid_bar_change_invalidates_fingerprint(self) -> None:
        from core.research.panel_fingerprint import PANEL_CACHE_SCHEMA, bars_fingerprint

        a = _bars__panel_fingerprint(10)
        b = [dict(x) for x in a]
        b[3] = dict(b[3])
        b[3]["close"] = float(b[3]["close"]) + 9.0
        self.assertNotEqual(bars_fingerprint(a), bars_fingerprint(b))
        self.assertEqual(PANEL_CACHE_SCHEMA, 3)

    def test_head_keys_stable(self) -> None:
        from core.research.panel_fingerprint import (
            co_head_key,
            make_fingerprint,
            oo_head_key,
            tc_head_key,
        )

        self.assertEqual(
            oo_head_key(horizon_days=1, ridge_core_only=False),
            oo_head_key(horizon_days=1, ridge_core_only=False),
        )
        self.assertNotEqual(
            oo_head_key(horizon_days=1, ridge_core_only=False),
            oo_head_key(horizon_days=1, ridge_core_only=True),
        )
        self.assertIn("tc", tc_head_key(tau_hm="open", min_history=12, include_alpha_tc=False, use_minute=False))
        self.assertIn("co", co_head_key(min_history=12, include_alpha_oo=True))
        a = make_fingerprint(bars_fp="z", horizon=1, ridge_core_only=0)
        b = make_fingerprint(bars_fp="z", ridge_core_only=0, horizon=1)
        self.assertEqual(a, b)

class TestAssembleUsesL1(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmpdir.name, "l1")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_assemble_oo_panels_cache_hit(self) -> None:
        from core.research.feature_lake import load_stock_panel
        from core.research.lake_bridge import FAMILY_OO
        from core.research.materialize.consumer import materialize_writer
        from core.research.oo_ridge_compact import assemble_oo_panels
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

        items = [
            ("AAA", _bars__panel_fingerprint(80, 10.0, 0)),
            ("BBB", _bars__panel_fingerprint(80, 12.0, 1)),
        ]
        with materialize_writer():
            x1, y1, names1, d1 = assemble_oo_panels(items, horizon_days=1, workers=1)
            self.assertGreater(int(y1.shape[0]), 0)
            fp_a = make_fingerprint(
                bars_fp=bars_fingerprint(items[0][1]),
                horizon=1,
                ridge_core_only=0,
            )
            self.assertIsNotNone(load_stock_panel(FAMILY_OO, "AAA", fingerprint=fp_a))

            x2, y2, names2, d2 = assemble_oo_panels(items, horizon_days=1, workers=1)
            np.testing.assert_allclose(x1, x2)
            np.testing.assert_allclose(y1, y2)
            self.assertEqual(names1, names2)
            self.assertEqual(d1, d2)

            mutated = [dict(b) for b in items[0][1]]
            mutated[-1] = dict(mutated[-1])
            mutated[-1]["close"] = float(mutated[-1]["close"]) * 1.05
            items2 = [("AAA", mutated), ("BBB", items[1][1])]
            x3, y3, _n3, _d3 = assemble_oo_panels(items2, horizon_days=1, workers=1)
            self.assertEqual(y3.shape[0], y1.shape[0])
            self.assertFalse(np.allclose(x1, x3))

    def test_tau_and_rank_l1_families(self) -> None:
        from core.research.feature_lake import invalidate_code, load_stock_panel
        from core.research.lake_bridge import FAMILY_TC, FAMILY_TC_RANK
        from core.research.materialize.consumer import materialize_writer
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
        from core.research.panel_matrix import (
            _breadth_keys,
            _tau_stock_job,
            _tc_matrix_seed_names,
        )
        from core.research.tc_ridge import TAU_Z_FEATURES

        bars = _bars__panel_fingerprint(80, 10.0, 2)
        col_names = _tc_matrix_seed_names(list(TAU_Z_FEATURES), include_alpha_tc=False)
        slim_keys = _breadth_keys()
        kw = dict(
            use_minute=False,
            tau_hm="open",
            grid=None,
            min_history=12,
            include_alpha_tc=False,
        )
        with materialize_writer():
            pack_tc = _tau_stock_job(
                {"code": "RANK1", "bars": list(bars)}, col_names, slim_keys, **kw
            )
            self.assertIsNotNone(pack_tc)
            assert pack_tc is not None
            fp = make_fingerprint(
                bars_fp=bars_fingerprint(bars),
                tau_hm="open",
                min_history=12,
                include_alpha_tc=0,
                include_alpha_th=0,
                use_minute=0,
            )
            self.assertIsNotNone(load_stock_panel(FAMILY_TC, "RANK1", fingerprint=fp))

            with mock.patch(
                "core.research.tau_panel.collect_tau_open_panel",
                side_effect=AssertionError("tc_rank should copy tc"),
            ):
                pack_rank = _tau_stock_job(
                    {"code": "RANK1", "bars": list(bars)},
                    col_names,
                    slim_keys,
                    cache_head="tc_rank",
                    **kw,
                )
            self.assertIsNotNone(pack_rank)
            assert pack_rank is not None
            np.testing.assert_allclose(pack_tc[1], pack_rank[1])
            self.assertIsNotNone(load_stock_panel(FAMILY_TC_RANK, "RANK1", fingerprint=fp))

            invalidate_code(FAMILY_TC, "RANK1")
            pack_rank2 = _tau_stock_job(
                {"code": "RANK1", "bars": list(bars)},
                col_names,
                slim_keys,
                cache_head="tc_rank",
                **kw,
            )
            self.assertIsNotNone(pack_rank2)
            assert pack_rank2 is not None
            np.testing.assert_allclose(pack_rank[1], pack_rank2[1])

    def test_short_tc_series_skips_without_l1(self) -> None:
        from core.research.materialize.consumer import (
            MaterializeRequired,
            materialized_consumer,
        )
        from core.research.panel_matrix import _tau_stock_job

        bars = _bars__panel_fingerprint(57, 10.0, 3)
        with materialized_consumer():
            try:
                pack = _tau_stock_job(
                    {"code": "688825", "bars": bars},
                    ["gap_pct"],
                    (),
                    use_minute=False,
                    tau_hm="open",
                    grid=None,
                    min_history=12,
                    include_alpha_tc=True,
                )
            except MaterializeRequired as exc:
                self.fail(str(exc))
        self.assertIsNone(pack)


# --- from test_panel_matrix.py ---
def _bars__panel_matrix(n: int = 40, start: float = 10.0, seed: int = 0):
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
        bars = _bars__panel_matrix(40, 10, 0)
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

        # collect_tau_compact 算完会 pop bars；对照路径用独立副本
        stock_bars = [
            {"code": "AAA", "bars": _bars__panel_matrix(48, 10, 0)},
            {"code": "BBB", "bars": _bars__panel_matrix(48, 12, 1)},
            {"code": "CCC", "bars": _bars__panel_matrix(48, 8, 2)},
        ]
        stock_bars_dict = [
            {"code": it["code"], "bars": list(it["bars"])} for it in stock_bars
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
                stock_bars_dict,
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
                {"code": "AAA", "bars": _bars__panel_matrix(80, 10, 0)},
                {"code": "BBB", "bars": _bars__panel_matrix(80, 12, 1)},
                {"code": "CCC", "bars": _bars__panel_matrix(80, 8, 2)},
                {"code": "DDD", "bars": _bars__panel_matrix(80, 11, 3)},
                {"code": "EEE", "bars": _bars__panel_matrix(80, 9, 4)},
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

        from core.research.panel_matrix import _resolve_co_workers, _resolve_tau_workers

        with patch.dict(os.environ, {"CO_RIDGE_WORKERS": "4"}, clear=False):
            self.assertEqual(_resolve_co_workers(800, None), 4)
        # 硬顶 4：再高也会被压住
        self.assertEqual(_resolve_co_workers(800, 8), 4)
        self.assertEqual(_resolve_co_workers(2, None), 1)
        with patch.dict(os.environ, {"TAU_RIDGE_WORKERS": "4"}, clear=False):
            self.assertEqual(_resolve_tau_workers(800, None), 4)
        self.assertEqual(_resolve_tau_workers(800, 8), 4)
        self.assertEqual(_resolve_tau_workers(2, None), 1)

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
            collinearity_policy="keep_all",
        )
        mat_fit = fit_keepall_ridge_matrix(
            X,
            ys,
            names,
            ridge_lambda=1.0,
            sample_weights=weights,
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
