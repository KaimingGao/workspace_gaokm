"""ŷ_τ 树对照：同 Holdout vs Ridge；不进 live / 回测。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 50, start: float = 10.0):
    from datetime import date, timedelta

    out = []
    px = start
    d0 = date(2025, 6, 1)
    for i in range(n):
        o = px
        c = px * (1.02 if i % 4 else 0.985)
        day = d0 + timedelta(days=i)
        out.append(
            {
                "date": day.isoformat(),
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1e6 + i * 1000,
            }
        )
        px = c
    return out


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


class TestTauBoost(unittest.TestCase):
    def test_numpy_gbm_recovers_stump(self):
        from core.research.tc_tree import _fit_numpy_gbm, _predict_numpy_gbm

        rng = np.random.default_rng(0)
        x = rng.normal(size=(240, 3))
        y = np.where(x[:, 0] > 0.0, 1.2, -0.8) + rng.normal(0, 0.05, size=240)
        w = np.ones(240)
        pack, gain = _fit_numpy_gbm(
            x[:180],
            y[:180],
            w[:180],
            n_estimators=20,
            max_depth=2,
            learning_rate=0.2,
            subsample=1.0,
            rng=rng,
        )
        pred = _predict_numpy_gbm(pack, x[180:])
        hit = np.mean((pred > 0) == (y[180:] > 0))
        self.assertGreater(hit, 0.85)
        self.assertGreater(float(gain[0]), float(gain[1]))

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_shadow_vs_ridge_no_live_file(self):
        from core.research.tc_tree import (
            fit_tau_tree_report,
            save_tau_tree_last_report,
            tau_tree_last_report_path,
        )
        from core.research.tc_ridge import load_tau_model, persist_tau_model

        stock_bars = [
            {"code": "A", "bars": _bars(50, 10)},
            {"code": "B", "bars": _bars(50, 12)},
            {"code": "C", "bars": _bars(50, 8)},
            {"code": "D", "bars": _bars(50, 15)},
        ]
        report = fit_tau_tree_report(
            stock_bars,
            ridge_lambda=1.0,
            theme_boost=1.5,
            backend="lightgbm",
            holdout_trading_days=8,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("task"), "tc_tree")
        self.assertEqual(report.get("head"), "y_tau_tree")
        self.assertEqual(report.get("schema"), "tau_tree_shadow_v2")
        self.assertEqual(report.get("backend"), "lightgbm")
        self.assertFalse(report.get("live_hook"))
        self.assertFalse(report.get("backtest_hook"))
        self.assertTrue((report.get("persisted") or {}).get("skipped"))
        self.assertNotIn("return_model", report)
        oos = report.get("oos") or {}
        ridge = report.get("ridge_oos") or {}
        self.assertIn("sign_hit", oos)
        self.assertIn("ic", oos)
        self.assertIn("residual_var", oos)
        self.assertIn("sign_hit", ridge)
        self.assertIn("residual_var", ridge)
        self.assertIn("delta_vs_ridge", report)
        self.assertTrue(report.get("feature_importance"))
        timing = report.get("timing") or {}
        self.assertIn("panel_s", timing)
        self.assertIn("tree_s", timing)
        self.assertIn("ridge_s", timing)
        self.assertIn("fit_s", timing)
        self.assertEqual((report.get("hyperparams") or {}).get("n_estimators"), 80)

        blocked = persist_tau_model(report, note="should fail", force=True)
        self.assertFalse(blocked.get("success"))

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_tau_tree_last_report(report)
                tree_path = tau_tree_last_report_path()
                self.assertTrue(os.path.isfile(tree_path))
                self.assertIn("tau_tree_last_report.json", tree_path)
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "tau_ridge_model.json"))
                )
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "tau_ridge_model_research.json"))
                )
                self.assertIsNone(load_tau_model())

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_backend_lightgbm_only(self):
        from core.research.tc_tree import resolve_tree_backend

        self.assertEqual(resolve_tree_backend("lightgbm"), "lightgbm")
        self.assertEqual(resolve_tree_backend(None), "lightgbm")
        self.assertEqual(resolve_tree_backend("auto"), "lightgbm")
        with self.assertRaises(ValueError):
            resolve_tree_backend("numpy_gbm")
        with self.assertRaises(ValueError):
            resolve_tree_backend("xgboost")

    def test_xgboost_native_train_no_sklearn(self):
        try:
            import xgboost  # noqa: F401
        except ImportError:
            self.skipTest("xgboost not installed")
        from core.research.tc_tree import _fit_xgboost, _predict_xgboost

        rng = np.random.default_rng(0)
        x = rng.normal(size=(80, 3))
        y = (x[:, 0] > 0).astype(np.float64) * 1.2 - 0.4
        w = np.ones(80)
        booster, gain = _fit_xgboost(
            x,
            y,
            w,
            n_estimators=8,
            max_depth=2,
            learning_rate=0.2,
            subsample=1.0,
        )
        pred = _predict_xgboost(booster, x)
        self.assertEqual(pred.shape, (80,))
        self.assertEqual(gain.shape, (3,))
        self.assertGreater(float(gain[0]), float(gain[1]))


if __name__ == "__main__":
    unittest.main()
