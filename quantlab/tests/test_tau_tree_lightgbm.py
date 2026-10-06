"""LightGBM 回归后端测试：resolve_tree_backend + _fit_lightgbm + pack/predict round-trip。

未安装 lightgbm 时全部 skip。
"""

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


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
class TestTauTreeLightgbm(unittest.TestCase):
    def test_resolve_backend_lightgbm(self):
        from core.research.tc_tree import resolve_tree_backend

        self.assertEqual(resolve_tree_backend("lightgbm"), "lightgbm")
        self.assertEqual(resolve_tree_backend("lgb"), "lightgbm")
        self.assertEqual(resolve_tree_backend(None), "lightgbm")
        with self.assertRaises(ValueError):
            resolve_tree_backend("auto")
        with self.assertRaises(ValueError):
            resolve_tree_backend("xgboost")
        with self.assertRaises(ValueError):
            resolve_tree_backend("numpy_gbm")

    def test_fit_predict_lightgbm_shapes(self):
        from core.research.tc_tree import _fit_lightgbm, _predict_lightgbm

        rng = np.random.default_rng(42)
        n, p = 200, 5
        x = rng.normal(size=(n, p))
        y = x[:, 0] * 0.5 + x[:, 1] * -0.3 + rng.normal(scale=0.1, size=n)
        w = np.ones(n, dtype=np.float64)
        booster, gain = _fit_lightgbm(
            x,
            y,
            w,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.1,
            subsample=1.0,
        )
        self.assertEqual(gain.shape, (p,))
        self.assertGreaterEqual(float(gain.sum()), 0.0)  # 归一化后 ≥0
        preds = _predict_lightgbm(booster, x)
        self.assertEqual(preds.shape, (n,))
        self.assertTrue(np.all(np.isfinite(preds)))

    def test_pack_and_predict_round_trip(self):
        """horizon_tree.pack_tree_return_model + predict_tree_p_up round-trip。"""
        from core.research.horizon_tree import (
            pack_tree_return_model,
            predict_tree_p_up,
        )
        from core.research.tc_tree import _fit_lightgbm

        rng = np.random.default_rng(7)
        n, p = 120, 4
        x = rng.normal(size=(n, p))
        y = (x[:, 0] > 0).astype(np.float64)
        w = np.ones(n, dtype=np.float64)
        booster, _ = _fit_lightgbm(
            x,
            y,
            w,
            n_estimators=20,
            max_depth=2,
            learning_rate=0.1,
            subsample=1.0,
        )
        means = np.zeros(p, dtype=np.float64)
        feat_names = [f"f{i}" for i in range(p)]
        rm = pack_tree_return_model(
            head="t_test",
            backend="lightgbm",
            feature_names=feat_names,
            impute_means=means,
            model_obj=booster,
            schema="lightgbm_round_trip_v1",
            hyperparams={"n_estimators": 20},
        )
        self.assertEqual(rm.get("backend"), "lightgbm")
        self.assertIsInstance(rm.get("booster"), dict)
        # 预测
        feats = {f"f{i}": float(rng.normal()) for i in range(p)}
        p_up = predict_tree_p_up(feats, rm)
        self.assertIsNotNone(p_up)
        self.assertGreater(float(p_up), 0.0)
        self.assertLess(float(p_up), 1.0)

    def test_pack_unknown_backend_raises(self):
        from core.research.horizon_tree import pack_tree_return_model

        with self.assertRaises(ValueError):
            pack_tree_return_model(
                head="x",
                backend="unknown_backend",
                feature_names=["a"],
                impute_means=np.zeros(1),
                model_obj=None,
                schema="x",
            )

    def test_fit_tau_tree_include_alpha158_flag(self):
        """树侧可开 Alpha158；Ridge 对照特征不含 raw_alpha158_*。"""
        from datetime import date, timedelta

        from core.research.tc_tree import fit_tau_tree_report

        def _bars(n=90, start=10.0, seed=0):
            out = []
            px = start
            d0 = date(2025, 1, 1)
            for i in range(n):
                o = px
                c = px * (1.008 if (i + seed) % 2 == 0 else 0.992)
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

        stock_bars = [
            {"code": "A", "bars": _bars(90, 10, 0)},
            {"code": "B", "bars": _bars(90, 12, 1)},
            {"code": "C", "bars": _bars(90, 8, 2)},
            {"code": "D", "bars": _bars(90, 15, 3)},
        ]
        off = fit_tau_tree_report(
            stock_bars,
            include_alpha158=False,
            holdout_trading_days=5,
            n_estimators=20,
            max_depth=2,
        )
        self.assertTrue(off.get("success"), off.get("error"))
        self.assertFalse(off.get("include_alpha158"))
        self.assertEqual(int(off.get("n_alpha158_features") or 0), 0)

        on = fit_tau_tree_report(
            stock_bars,
            include_alpha158=True,
            holdout_trading_days=5,
            n_estimators=20,
            max_depth=2,
        )
        self.assertTrue(on.get("success"), on.get("error"))
        self.assertTrue(on.get("include_alpha158"))
        self.assertGreater(int(on.get("n_alpha158_features") or 0), 0)
        self.assertTrue(
            any(str(k).startswith("raw_alpha158_") for k in (on.get("feature_names") or []))
        )
        self.assertFalse(
            any(
                str(k).startswith("raw_alpha158_")
                for k in (on.get("ridge_feature_names") or [])
            )
        )

    def test_fit_shadow_vs_ridge_no_live_file(self):
        from core.research.tc_tree import (
            fit_tau_tree_report,
            save_tau_tree_last_report,
            tau_tree_last_report_path,
        )
        from core.research.tc_ridge import load_tau_model, persist_tau_model
        from datetime import date, timedelta

        def _bars(n=50, start=10.0):
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
            include_alpha158=False,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("task"), "tc_tree")
        self.assertEqual(report.get("head"), "y_tau_tree")
        self.assertEqual(report.get("schema"), "tau_tree_shadow_v2")
        self.assertEqual(report.get("backend"), "lightgbm")
        self.assertFalse(report.get("live_hook"))
        self.assertTrue(report.get("backtest_hook"))
        self.assertTrue((report.get("persisted") or {}).get("skipped"))
        self.assertNotIn("return_model", report)
        rm = report.get("tree_return_model") or {}
        self.assertTrue(rm.get("feature_names"))
        self.assertEqual(rm.get("head_kind"), "return")
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
        self.assertEqual((report.get("hyperparams") or {}).get("n_estimators"), 300)
        self.assertTrue((report.get("hyperparams") or {}).get("feature_zscore"))
        self.assertTrue((rm.get("hyperparams") or {}).get("feature_zscore"))

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


if __name__ == "__main__":
    unittest.main()
