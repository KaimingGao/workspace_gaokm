"""ŷ_τ30 树对照：同 Holdout vs Ridge；不进 live。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bar(dkey: str, hm: str, close: float, open_px: float | None = None):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": 1000,
    }


def _times_am_pm():
    out = []
    for total in range(9 * 60 + 30, 11 * 60 + 30 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    for total in range(13 * 60, 15 * 60 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    return out


def _stock(code: str, start: float, n_days: int = 40):
    d0 = date(2025, 6, 2)
    daily = []
    minutes = []
    px = start
    times = _times_am_pm()
    i = 0
    day = d0
    while len(daily) < n_days:
        if day.weekday() < 5:
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.04 if j % 2 else 0.0) + j * 0.002 + (i % 3) * 0.01
                bars_m.append(_bar(dkey, hm, c, open_px=px))
            minutes.extend(bars_m)
            close = bars_m[-1]["close"]
            daily.append(
                {
                    "date": dkey,
                    "open": px,
                    "high": max(px, close) * 1.01,
                    "low": min(px, close) * 0.99,
                    "close": close,
                    "volume": 1e6,
                    "prev_close": px * 0.995,
                }
            )
            px = close
            i += 1
        day = day + timedelta(days=1)
    return {"code": code, "bars": daily, "minute_bars": minutes}



def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except ImportError:
        return False



@unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
class TestT30Tree(unittest.TestCase):
    def test_defaults_match_tau_tree(self):
        from core.research.t30_tree import DEFAULT_N_ESTIMATORS, TREE_HEAD, TREE_SCHEMA
        from core.research.tc_tree import DEFAULT_N_ESTIMATORS as TAU_N

        self.assertEqual(DEFAULT_N_ESTIMATORS, 300)
        self.assertEqual(DEFAULT_N_ESTIMATORS, TAU_N)
        self.assertEqual(TREE_HEAD, "y_t30_tree")
        self.assertEqual(TREE_SCHEMA, "t30_tree_shadow_v5")

    def test_fit_shadow_vs_ridge_no_live_file(self):
        from core.research.t30_ridge import load_t30_model, persist_t30_model
        from core.research.t30_tree import (
            fit_t30_tree_report,
            save_t30_tree_last_report,
            t30_tree_last_report_path,
        )

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        report = fit_t30_tree_report(
            stock_bars,
            ridge_lambda=1.0,
            theme_boost=1.5,
            backend="lightgbm",
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:30", "09:50", "10:30", "11:15"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("task"), "t30_tree")
        self.assertEqual(report.get("head"), "y_t30_tree")
        self.assertEqual(report.get("schema"), "t30_tree_shadow_v5")
        self.assertEqual(report.get("backend"), "lightgbm")
        self.assertEqual(report.get("target"), "price_tau_plus_30")
        self.assertEqual(report.get("tau"), "10:30")
        self.assertFalse(report.get("live_hook"))
        self.assertTrue(report.get("backtest_hook"))
        self.assertIn("tree_return_model", report)
        rm = report.get("tree_return_model") or {}
        self.assertEqual(rm.get("head_kind"), "prob")
        self.assertIn("feature_names", rm)
        oos = report.get("oos") or {}
        ridge = report.get("ridge_oos") or {}
        self.assertIn("sign_hit", oos)
        self.assertIn("ic", oos)
        self.assertIn("residual_var", oos)
        self.assertIn("auc", oos)
        self.assertIn("brier", oos)
        self.assertEqual(oos.get("target"), "price_tau_plus_30")
        self.assertIn("sign_hit", ridge)
        self.assertIn("residual_var", ridge)
        self.assertIn("delta_vs_ridge", report)
        self.assertTrue(report.get("feature_importance"))
        feat_names = report.get("feature_names") or []
        ridge_names = report.get("ridge_feature_names") or []
        self.assertIn("ret_last_5m", feat_names)
        self.assertIn("ret_last_30m", feat_names)
        self.assertIn("t30_lag1", feat_names)
        for k in (
            "path_sign",
            "bounce_from_low",
            "pullback_from_high",
            "vp_confirm",
            "range_efficiency",
            "vol_up_share",
            "pullback_x_vol",
        ):
            self.assertIn(k, feat_names)
            self.assertNotIn(k, ridge_names)
        self.assertIn("path_sign", report.get("tree_shape_features") or [])
        self.assertIn("vp_confirm", report.get("tree_shape_features") or [])
        timing = report.get("timing") or {}
        self.assertIn("panel_s", timing)
        self.assertIn("tree_s", timing)
        self.assertIn("ridge_s", timing)
        self.assertIn("fit_s", timing)
        self.assertEqual((report.get("hyperparams") or {}).get("n_estimators"), 20)
        self.assertTrue((report.get("hyperparams") or {}).get("feature_zscore"))
        rm = report.get("tree_return_model") or {}
        self.assertTrue((rm.get("hyperparams") or {}).get("feature_zscore"))

        blocked = persist_t30_model(report, note="should fail", force=True)
        self.assertFalse(blocked.get("success"))

        from core.research.t30_tree import (
            persist_t30_tree_model,
            predict_t30_tree_from_features,
        )

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_t30_tree_last_report(report)
                tree_path = t30_tree_last_report_path()
                self.assertTrue(os.path.isfile(tree_path))
                self.assertIn("t30_tree_last_report.json", tree_path)
                saved = persist_t30_tree_model(report, force=True)
                self.assertTrue(saved.get("success"), saved)
                self.assertTrue(os.path.isfile(os.path.join(live, "t30_tree_model.json")))
                pred = predict_t30_tree_from_features({"gap_pct": 0.0, "ret_last_5m": -0.2})
                self.assertIsNotNone(pred)
                self.assertGreater(float(pred), 0.0)
                self.assertLess(float(pred), 1.0)
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t30_ridge_model.json"))
                )
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t30_ridge_model_research.json"))
                )
                self.assertIsNone(load_t30_model())

    def test_open_tau_coerced_to_1030(self):
        from core.research.t30_tree import fit_t30_tree_report

        report = fit_t30_tree_report(
            [_stock("A", 10.0, n_days=36), _stock("B", 11.0, n_days=36)],
            backend="lightgbm",
            n_estimators=8,
            tau_hm="open",
            tau_grid=["09:30", "10:30", "11:15"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("tau"), "10:30")


    def test_tree_z_adds_path_shape_ridge_keeps_drop(self):
        from core.research.t30_ridge import T30_Z_FEATURES
        from core.research.t30_tree import T30_TREE_Z_FEATURES
        from core.research.tc_ridge import (
            TAU_HORIZON_DROP_OC_SHAPE,
            TAU_HORIZON_TREE_SHAPE_FEATURES,
            with_horizon_tree_shape,
        )

        for k in TAU_HORIZON_TREE_SHAPE_FEATURES:
            self.assertNotIn(k, T30_Z_FEATURES)
            self.assertIn(k, T30_TREE_Z_FEATURES)
            self.assertIn(k, TAU_HORIZON_DROP_OC_SHAPE)
        for k in (
            "t_hi_frac",
            "t_lo_frac",
            "t_hi_minus_lo",
            "room_to_high",
            "room_to_low",
            "mom_accel_5_15",
            "mom_accel_5_30",
            "vol_down_up",
            "range_efficiency",
            "vp_confirm",
            "vol_up_share",
            "pullback_x_vol",
        ):
            self.assertIn(k, TAU_HORIZON_TREE_SHAPE_FEATURES)
        self.assertEqual(
            list(T30_TREE_Z_FEATURES),
            list(with_horizon_tree_shape(T30_Z_FEATURES)),
        )


if __name__ == "__main__":
    unittest.main()
