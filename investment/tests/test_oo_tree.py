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
            self.assertTrue(hp.get("zscore_means"))
            self.assertTrue(hp.get("zscore_stds"))
            oos = report["oos"]
            self.assertGreaterEqual(int(oos.get("n_test") or 0), 1)
        else:
            # 合成数据因子覆盖不足时允许失败，但错误应可读
            self.assertTrue(report.get("error"))

    def test_feature_zscore_matches_ridge_and_predict(self):
        """树特征 z 与 ŷ_oo Ridge 的训练集总体 z 一致；缺测填均值后为 0。"""
        import numpy as np

        from core.research.factor_ols_fit import _zscore_complete_panel
        from core.research.horizon_tree import _row_matrix
        from core.research.tc_tree import fit_lgb_holdout

        xs = [
            {"a": 1.0, "b": 10.0},
            {"a": 3.0, "b": 14.0},
            {"a": 5.0, "b": 12.0},
            {"a": 7.0, "b": 16.0},
        ]
        ys = [0.2, -0.1, 0.4, 0.0]
        metas = [{"date": f"2026-01-0{i+1}"} for i in range(4)]
        xs_z, mu, sd = _zscore_complete_panel(xs, ["a", "b"])

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
            _model, _gain, hyper, means, _preds, _s = fit_lgb_holdout(
                xs,
                ys,
                metas,
                xs[:2],
                ["a", "b"],
                use_qlib=False,
                n_estimators=8,
                max_depth=2,
                learning_rate=0.1,
                subsample=1.0,
                feature_zscore=True,
            )
        self.assertTrue(hyper.get("feature_zscore"))
        self.assertFalse(hyper.get("label_cs_zscore"))
        for i, row in enumerate(xs_z):
            self.assertAlmostEqual(float(seen["x"][i, 0]), float(row["a"]), places=6)
            self.assertAlmostEqual(float(seen["x"][i, 1]), float(row["b"]), places=6)
        np.testing.assert_allclose(seen["y"], np.asarray(ys, dtype=np.float64))
        self.assertAlmostEqual(float(hyper["zscore_means"]["a"]), float(mu["a"]), places=6)
        self.assertAlmostEqual(float(hyper["zscore_stds"]["b"]), float(sd["b"]), places=6)

        pred_x = _row_matrix(
            {"a": 3.0, "b": None},
            ["a", "b"],
            {"a": float(means[0]), "b": float(means[1])},
            z_means=hyper["zscore_means"],
            z_stds=hyper["zscore_stds"],
        )
        self.assertAlmostEqual(float(pred_x[0, 0]), float(xs_z[1]["a"]), places=6)
        self.assertAlmostEqual(float(pred_x[0, 1]), 0.0, places=6)

    def test_cs_zscore_by_date_and_qlib_flag(self):
        from core.research.tc_tree import PANEL_LGB, QLIB_ALPHA158_LGB, _cs_zscore_by_date, resolve_use_qlib_lgb

        z = _cs_zscore_by_date([1.0, 3.0, 10.0, 12.0], ["d1", "d1", "d2", "d2"])
        self.assertAlmostEqual(float(z[0]), -1.0, places=6)
        self.assertAlmostEqual(float(z[1]), 1.0, places=6)
        self.assertIn("n_estimators", QLIB_ALPHA158_LGB)
        self.assertEqual(int(QLIB_ALPHA158_LGB["max_depth"]), 6)
        self.assertEqual(int(QLIB_ALPHA158_LGB["n_estimators"]), 300)
        self.assertEqual(int(QLIB_ALPHA158_LGB["num_leaves"]), 64)
        self.assertEqual(float(QLIB_ALPHA158_LGB["lambda_l1"]), 10.0)
        self.assertNotIn("early_stopping_rounds", QLIB_ALPHA158_LGB)
        self.assertNotIn("valid_trading_days", QLIB_ALPHA158_LGB)
        self.assertFalse(resolve_use_qlib_lgb(True))
        self.assertFalse(resolve_use_qlib_lgb(True, False))
        self.assertTrue(resolve_use_qlib_lgb(False, True))
        self.assertEqual(int(PANEL_LGB["max_depth"]), 5)
        self.assertEqual(int(PANEL_LGB["n_estimators"]), 300)

    def test_qlib_fit_runs_full_rounds_without_early_stop(self):
        from unittest.mock import patch

        import numpy as np

        from core.research.tc_tree import fit_lgb_holdout

        class _Stub:
            best_iteration = None

        calls = []

        def _fake_fit(x, y, w, **kwargs):
            calls.append(kwargs)
            return _Stub(), np.zeros(max(int(x.shape[1]), 1), dtype=np.float64)

        xs = [{"a": float(i), "b": float(i % 3)} for i in range(40)]
        ys = [0.1 * ((i % 5) - 2) for i in range(40)]
        metas = [{"date": f"2026-01-{(i % 28) + 1:02d}"} for i in range(40)]
        with patch("core.research.tc_tree._fit_lightgbm", _fake_fit), patch(
            "core.research.tc_tree._predict_lightgbm",
            return_value=np.zeros(8, dtype=np.float64),
        ):
            _model, _gain, hyper, _means, _preds, _s = fit_lgb_holdout(
                xs,
                ys,
                metas,
                xs[:8],
                ["a", "b"],
                use_qlib=True,
                n_estimators=80,
                max_depth=5,
                learning_rate=0.08,
                subsample=0.85,
            )
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0].get("early_stopping_rounds"))
        self.assertIsNone(calls[0].get("x_valid"))
        self.assertEqual(int(calls[0]["max_depth"]), 6)
        self.assertEqual(int(hyper["max_depth"]), 6)
        self.assertEqual(hyper.get("preset"), "qlib_alpha158")
        self.assertTrue(hyper.get("label_cs_zscore"))
        self.assertNotIn("early_stopping_rounds", hyper)

    def test_oos_pack_sign_hit_fallback_when_preds_tiny(self):
        """Qlib 截面 z 标签下 ŷ 常 <0.05；命中率应回退全样本同号，不能变 None。"""
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


if __name__ == "__main__":
    unittest.main()
