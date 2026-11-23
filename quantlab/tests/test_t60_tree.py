"""ŷ_τ60 树对照：同 Holdout vs Ridge；遗留 standalone；正式双训见主卡。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from tests.conftest import _has_lightgbm
from tests.conftest import _horizon_tree_stock as _stock


@unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
class TestT60Tree(unittest.TestCase):
    def test_defaults_and_schema(self):
        from core.research.horizon_lgb import DEFAULT_N_ESTIMATORS
        from core.research.horizon_tree import _horizon_tree_config

        cfg = _horizon_tree_config("t60")
        self.assertEqual(DEFAULT_N_ESTIMATORS, 300)
        self.assertEqual(cfg["head_label"], "y_t60_tree")
        self.assertEqual(cfg["schema"], "t60_tree_shadow_v5")

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
            "t60",
            stock_bars,
            ridge_lambda=1.0,
            theme_boost=1.5,
            backend="lightgbm",
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:35", "09:50", "10:30", "11:00"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("task"), "t60_tree")
        self.assertEqual(report.get("head"), "y_t60_tree")
        self.assertEqual(report.get("schema"), "t60_tree_shadow_v5")
        self.assertEqual(report.get("backend"), "lightgbm")
        self.assertEqual(report.get("target"), "price_tau_plus_60")
        self.assertEqual(report.get("tau"), "10:30")
        self.assertFalse(report.get("live_hook"))
        self.assertTrue(report.get("backtest_hook"))
        self.assertIn("tree_return_model", report)
        oos = report.get("oos") or {}
        ridge = report.get("ridge_oos") or {}
        self.assertIn("sign_hit", oos)
        self.assertIn("ic", oos)
        self.assertIn("residual_var", oos)
        self.assertEqual(oos.get("target"), "price_tau_plus_60")
        self.assertIn("sign_hit", ridge)
        self.assertIn("residual_var", ridge)
        self.assertIn("delta_vs_ridge", report)
        self.assertTrue(report.get("feature_importance"))
        feat_names = report.get("feature_names") or []
        ridge_names = report.get("ridge_feature_names") or []
        self.assertIn("raw_ath_RET_LAST_5M", feat_names)
        self.assertIn("raw_ath_RET_LAST_60", feat_names)
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

        blocked = persist_ridge_model(
            "t60", report, note="should fail", force=True, role="research"
        )
        self.assertFalse(blocked.get("success"))

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_tree_last_report("t60", report)
                tree_path = tree_last_report_path("t60")
                self.assertTrue(os.path.isfile(tree_path))
                self.assertIn("t60_tree_last_report.json", tree_path)
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t60_ridge_model.json"))
                )
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "t60_ridge_model_research.json"))
                )
                self.assertIsNone(load_ridge_model("t60"))

    def test_open_tau_coerced_to_0935(self):
        from core.research.horizon_tree import fit_horizon_tree_report

        report = fit_horizon_tree_report(
            "t60",
            [_stock("A", 10.0, n_days=36), _stock("B", 11.0, n_days=36)],
            backend="lightgbm",
            n_estimators=8,
            tau_hm="open",
            tau_grid=["09:35", "10:30", "11:00"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("tau"), "09:35")

if __name__ == "__main__":
    unittest.main()
