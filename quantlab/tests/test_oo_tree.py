"""ŷ_oo_tree 影子头：日线面板 Holdout vs Ridge。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _synthetic_bars(n: int = 80, seed: int = 1):
    bars = []
    px = 10.0 + seed
    for i in range(n):
        d = f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}"
        o = px
        c = px * (1.0 + ((i % 7) - 3) * 0.002)
        h = max(o, c) * 1.01
        l = min(o, c) * 0.99
        bars.append(
            {
                "date": d,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
                "amount": 1e7 + i * 1e4,
            }
        )
        px = c
    return bars


class TestOoTree(unittest.TestCase):
    def test_fit_oo_tree_report_smoke(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.oo_tree import fit_oo_tree_report

        stock_bars = [
            {"code": "AAA", "bars": _synthetic_bars(90, seed=1)},
            {"code": "BBB", "bars": _synthetic_bars(90, seed=2)},
            {"code": "CCC", "bars": _synthetic_bars(90, seed=3)},
        ]
        # 因子面板依赖真实注册因子；合成 K 可能样本仍够跑通或明确失败
        report = fit_oo_tree_report(
            stock_bars,
            horizon_days=1,
            ridge_lambda=1.0,
            holdout_trading_days=8,
            include_alpha158=False,
            n_estimators=16,
            max_depth=2,
            window_days=0,
        )
        self.assertIsInstance(report, dict)
        self.assertEqual(report.get("task"), "oo_tree")
        self.assertEqual(report.get("head"), "y_oo_tree")
        if report.get("success"):
            self.assertIn("oos", report)
            self.assertIn("ridge_oos", report)
            self.assertIn("delta_vs_ridge", report)
            self.assertFalse(report.get("live_hook"))
            self.assertTrue(report.get("backtest_hook"))
            rm = report.get("tree_return_model") or {}
            self.assertTrue(rm.get("feature_names"))
            self.assertEqual(rm.get("head_kind"), "return")
            hp = rm.get("hyperparams") or {}
            self.assertTrue(hp.get("feature_zscore"))
            self.assertEqual(hp.get("zscore_scope"), "cross_section")
            self.assertFalse(hp.get("zscore_means"))
            oos = report["oos"]
            self.assertGreaterEqual(int(oos.get("n_test") or 0), 1)
        else:
            # 合成数据因子覆盖不足时允许失败，但错误应可读
            self.assertTrue(report.get("error"))

    def test_feature_zscore_cross_section_by_day(self):
        """有日期时按当天截面 z；两日互不借用均值。"""
        import numpy as np

        from core.research.feature_standardize import cross_section_zscore_matrix
        from core.research.tc_tree import fit_lgb_holdout

        xs = [
            {"a": 0.0, "b": 10.0},
            {"a": 2.0, "b": 14.0},
            {"a": 100.0, "b": 12.0},
            {"a": 100.0, "b": 16.0},
        ]
        ys = [0.2, -0.1, 0.4, 0.0]
        dates = ["d1", "d1", "d2", "d2"]
        expect = cross_section_zscore_matrix(
            np.asarray([[0.0, 10.0], [2.0, 14.0], [100.0, 12.0], [100.0, 16.0]]),
            dates,
        )
        expect = np.where(np.isfinite(expect), expect, 0.0)

        seen = {}

        def _fake_fit(x, y, w, **kwargs):
            seen["x"] = np.asarray(x, dtype=np.float64).copy()
            seen["y"] = np.asarray(y, dtype=np.float64).copy()

            class _Stub:
                best_iteration = None

            return _Stub(), np.zeros(x.shape[1], dtype=np.float64)

        with patch("core.research.tc_tree._fit_lightgbm", _fake_fit), patch(
            "core.research.tc_tree._predict_lightgbm",
            return_value=np.zeros(2, dtype=np.float64),
        ):
            _model, _gain, hyper, _means, _preds, _s = fit_lgb_holdout(
                xs,
                ys,
                xs[:2],
                ["a", "b"],
                n_estimators=8,
                max_depth=2,
                learning_rate=0.1,
                subsample=1.0,
                feature_zscore=True,
                train_dates=dates,
                test_dates=dates[:2],
            )
        self.assertTrue(hyper.get("feature_zscore"))
        self.assertEqual(hyper.get("zscore_scope"), "cross_section")
        self.assertFalse(hyper.get("zscore_means"))
        np.testing.assert_allclose(seen["x"], expect, rtol=1e-6)
        np.testing.assert_allclose(seen["y"], np.asarray(ys, dtype=np.float64))

    def test_panel_lgb_defaults(self):
        from core.research.tc_tree import (
            DEFAULT_TREE_STEP_DAYS,
            DEFAULT_TREE_WINDOW_DAYS,
            PANEL_LGB,
        )

        self.assertEqual(int(PANEL_LGB["max_depth"]), 6)
        self.assertEqual(int(PANEL_LGB["n_estimators"]), 300)
        self.assertEqual(int(PANEL_LGB["num_leaves"]), 64)
        self.assertEqual(float(PANEL_LGB["learning_rate"]), 0.2)
        self.assertEqual(float(PANEL_LGB["lambda_l1"]), 10.0)
        self.assertEqual(float(PANEL_LGB["lambda_l2"]), 20.0)
        self.assertNotIn("early_stopping_rounds", PANEL_LGB)
        self.assertEqual(int(DEFAULT_TREE_WINDOW_DAYS), 60)
        self.assertEqual(int(DEFAULT_TREE_STEP_DAYS), 20)

    def test_panel_lgb_fit_runs_full_rounds_without_early_stop(self):
        from unittest.mock import patch

        import numpy as np

        from core.research.tc_tree import PANEL_LGB, fit_lgb_holdout

        class _Stub:
            best_iteration = None

        calls = []

        def _fake_fit(x, y, w, **kwargs):
            calls.append(kwargs)
            return _Stub(), np.zeros(max(int(x.shape[1]), 1), dtype=np.float64)

        xs = [{"a": float(i), "b": float(i % 3)} for i in range(40)]
        ys = [0.1 * ((i % 5) - 2) for i in range(40)]
        with patch("core.research.tc_tree._fit_lightgbm", _fake_fit), patch(
            "core.research.tc_tree._predict_lightgbm",
            return_value=np.zeros(8, dtype=np.float64),
        ):
            _model, _gain, hyper, _means, _preds, _s = fit_lgb_holdout(
                xs,
                ys,
                xs[:8],
                ["a", "b"],
                n_estimators=int(PANEL_LGB["n_estimators"]),
                max_depth=int(PANEL_LGB["max_depth"]),
                learning_rate=float(PANEL_LGB["learning_rate"]),
                subsample=float(PANEL_LGB["subsample"]),
            )
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0].get("early_stopping_rounds"))
        self.assertIsNone(calls[0].get("x_valid"))
        self.assertIsNone(calls[0].get("init_model"))
        self.assertEqual(int(calls[0]["max_depth"]), 6)
        self.assertEqual(int(hyper["max_depth"]), 6)
        self.assertEqual(int(hyper["num_leaves"]), 64)
        self.assertEqual(int(hyper.get("window_days") or 0), 0)
        self.assertEqual(int(hyper.get("n_windows") or 0), 1)
        self.assertNotIn("preset", hyper)
        self.assertNotIn("early_stopping_rounds", hyper)

    def test_oos_pack_sign_hit_fallback_when_preds_tiny(self):
        """小幅度 ŷ 下命中率应回退全样本同号，不能变 None。"""
        from core.research.tc_tree import _oos_pack

        preds = [0.01, -0.02, 0.03, -0.01, 0.015, -0.008, 0.02, -0.011]
        ys = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
        metas = [{"date": "2026-09-01", "code": str(i)} for i in range(len(ys))]
        pack = _oos_pack(preds, ys, metas, use_minute=False)
        self.assertIsNotNone(pack.get("sign_hit"))
        self.assertGreaterEqual(float(pack["sign_hit"]), 0.99)

    def test_oos_pack_abs_top_buckets_when_fixed_thr_empty(self):
        from core.research.tc_tree import _delta_oos, _oos_pack

        rng_preds = [0.01 * ((i % 5) - 2) for i in range(40)]
        ys = [1.0 if i % 2 == 0 else -1.0 for i in range(40)]
        metas = [{"date": "2026-09-01", "code": str(i)} for i in range(40)]
        pack = _oos_pack(rng_preds, ys, metas, use_minute=False)
        buckets = pack.get("buckets") or {}
        self.assertEqual((buckets.get("abs_ge_0_6") or {}).get("n"), 0)
        self.assertIn("abs_top_30", buckets)
        self.assertGreaterEqual(int((buckets.get("abs_top_30") or {}).get("n") or 0), 5)
        self.assertIsNotNone((buckets.get("abs_top_30") or {}).get("sign_hit"))
        ridge = {
            "ic": 0.0,
            "sign_hit": 0.5,
            "residual_var": 1.0,
            "buckets": {
                "abs_ge_0_6": {"n": 0, "sign_hit": None},
                "abs_top_30": {"n": 12, "sign_hit": 0.5},
            },
        }
        delta = _delta_oos(pack, ridge)
        self.assertEqual(delta.get("strong_bucket"), "abs_top_30")
        self.assertIsNotNone(delta.get("strong_sign_hit"))

    def test_save_load_last_report(self):
        import tempfile

        from core.research import oo_tree as mod

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "oo_tree_last_report.json")
            with patch.object(mod, "oo_tree_last_report_path", return_value=path):
                mod.save_oo_tree_last_report(
                    {
                        "success": True,
                        "task": "oo_tree",
                        "head": "y_oo_tree",
                        "oos": {"ic": 0.1, "n_test": 12},
                    }
                )
                loaded = mod.load_oo_tree_last_report()
                self.assertIsNotNone(loaded)
                self.assertEqual((loaded or {}).get("oos", {}).get("ic"), 0.1)

    def test_service_oo_tree_uses_portfolio_bars(self):
        """与 co_tree / τc_tree 同源：勿退回逐只 bars_and_source。"""
        import inspect

        from quant.services.quant_service_factors import QuantFactorMixin

        src = inspect.getsource(QuantFactorMixin.run_oo_tree_experiment)
        self.assertIn("load_portfolio_stock_bars", src)
        self.assertNotIn("bars_and_source", src)


if __name__ == "__main__":
    unittest.main()
