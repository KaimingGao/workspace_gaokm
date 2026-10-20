"""ŷ_τ30 树对照：同 Holdout vs Ridge；遗留 standalone；正式双训见主卡。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch


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
    def test_defaults_and_schema(self):
        from core.research.horizon_lgb import DEFAULT_N_ESTIMATORS
        from core.research.horizon_tree import _horizon_tree_config

        cfg = _horizon_tree_config("t30")
        self.assertEqual(DEFAULT_N_ESTIMATORS, 300)
        self.assertEqual(cfg["head_label"], "y_t30_tree")
        self.assertEqual(cfg["schema"], "t30_tree_shadow_v5")

    def test_fit_shadow_vs_ridge_no_live_file(self):
        from core.research.horizon_ridge import load_ridge_model, persist_ridge_model
        from core.research.horizon_tree import (
            fit_horizon_tree_report,
            save_tree_last_report,
            tree_last_report_path,
        )

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        report = fit_horizon_tree_report(
            "t30",
            stock_bars,
            ridge_lambda=1.0,
            theme_boost=1.5,
            backend="lightgbm",
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:35", "09:50", "10:30", "11:00"],
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
        self.assertIn("raw_ath_RET_LAST_5M", feat_names)
        self.assertIn("raw_ath_RET_LAST_30", feat_names)
        self.assertIn("raw_ath_H_LAG1", feat_names)
        self.assertIn("z_τh_gap_pct", feat_names)
        self.assertNotIn("ret_last_5m", feat_names)
        self.assertEqual(list(feat_names), list(ridge_names))
        self.assertNotIn("tree_shape_features", report)
        for k in (
            "path_sign",
            "bounce_from_low",
            "pullback_from_high",
            "vp_confirm",
            "range_efficiency",
            "vol_up_share",
            "pullback_x_vol",
        ):
            self.assertNotIn(k, feat_names)
            self.assertNotIn(k, ridge_names)
        timing = report.get("timing") or {}
        self.assertIn("panel_s", timing)
        self.assertIn("tree_s", timing)
        self.assertIn("ridge_s", timing)
        self.assertIn("fit_s", timing)
        self.assertEqual((report.get("hyperparams") or {}).get("n_estimators"), 20)
        self.assertTrue((report.get("hyperparams") or {}).get("feature_zscore"))
        rm = report.get("tree_return_model") or {}
        self.assertTrue((rm.get("hyperparams") or {}).get("feature_zscore"))

        # 树报告无 Ridge return_model；role=research 才走报告路径（默认 live 会晋级盘上研究槽）
        blocked = persist_ridge_model(
            "t30", report, note="should fail", force=True, role="research"
        )
        self.assertFalse(blocked.get("success"))

        from core.research.horizon_tree import (
            persist_tree_model_doc,
            predict_tree_from_features,
        )

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_tree_last_report("t30", report)
                tree_path = tree_last_report_path("t30")
                self.assertTrue(os.path.isfile(tree_path))
                self.assertIn("t30_tree_last_report.json", tree_path)
                saved = persist_tree_model_doc("t30", report, force=True)
                self.assertTrue(saved.get("success"), saved)
                self.assertTrue(os.path.isfile(os.path.join(live, "t30_tree_model.json")))
                pred = predict_tree_from_features("t30", {"gap_pct": 0.0, "ret_last_5m": -0.2})
                self.assertIsNotNone(pred)
                self.assertGreater(float(pred), 0.0)
                self.assertLess(float(pred), 1.0)
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t30_ridge_model.json"))
                )
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t30_ridge_model_research.json"))
                )
                self.assertIsNone(load_ridge_model("t30"))

    def test_open_tau_coerced_to_0935(self):
        from core.research.horizon_tree import fit_horizon_tree_report

        report = fit_horizon_tree_report(
            "t30",
            [_stock("A", 10.0, n_days=36), _stock("B", 11.0, n_days=36)],
            backend="lightgbm",
            n_estimators=8,
            tau_hm="open",
            tau_grid=["09:35", "10:30", "11:00"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("tau"), "09:35")

    def test_tree_z_matches_ridge_no_path_shape(self):
        from core.research.horizon_ridge import _horizon_ridge_config
        from core.research.horizon_tree import _horizon_tree_config
        from core.research.tc_ridge_compact import TAU_HORIZON_TREE_SHAPE_FEATURES

        ridge_z = list(_horizon_ridge_config("t30")["z_features"])
        tree_z = list(_horizon_tree_config("t30")["z_features"])
        self.assertEqual(tree_z, ridge_z)
        for k in TAU_HORIZON_TREE_SHAPE_FEATURES:
            self.assertNotIn(k, ridge_z)
            self.assertNotIn(k, tree_z)

if __name__ == "__main__":
    unittest.main()
