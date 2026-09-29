"""ŷ_τc 树对照：同 Holdout vs Ridge；不进 live / 回测。"""

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
class TestTcTree(unittest.TestCase):
    def test_defaults_match_tau_tree(self):
        from core.research.tc_tree import DEFAULT_N_ESTIMATORS, TREE_HEAD, TREE_SCHEMA
        from core.research.tau_tree import DEFAULT_N_ESTIMATORS as TAU_N

        self.assertEqual(DEFAULT_N_ESTIMATORS, 80)
        self.assertEqual(DEFAULT_N_ESTIMATORS, TAU_N)
        self.assertEqual(TREE_HEAD, "y_tc_tree")
        self.assertEqual(TREE_SCHEMA, "tc_tree_shadow_v1")

    def test_fit_shadow_vs_ridge_no_live_file(self):
        from core.research.tc_ridge import load_tc_model, persist_tc_model
        from core.research.tc_tree import (
            fit_tc_tree_report,
            tc_tree_last_report_path,
            save_tc_tree_last_report,
        )

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        report = fit_tc_tree_report(
            stock_bars,
            ridge_lambda=1.0,
            theme_boost=1.5,
            backend="lightgbm",
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:30", "09:50", "10:30"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("task"), "tc_tree")
        self.assertEqual(report.get("head"), "y_tc_tree")
        self.assertEqual(report.get("schema"), "tc_tree_shadow_v1")
        self.assertEqual(report.get("backend"), "lightgbm")
        self.assertEqual(report.get("target"), "close_over_price_tau")
        self.assertEqual(report.get("tau"), "10:30")
        self.assertFalse(report.get("live_hook"))
        self.assertFalse(report.get("backtest_hook"))
        self.assertTrue((report.get("persisted") or {}).get("skipped"))
        self.assertNotIn("return_model", report)
        oos = report.get("oos") or {}
        ridge = report.get("ridge_oos") or {}
        self.assertIn("sign_hit", oos)
        self.assertIn("ic", oos)
        self.assertIn("residual_var", oos)
        self.assertEqual(oos.get("target"), "close_over_price_tau")
        self.assertIn("sign_hit", ridge)
        self.assertIn("residual_var", ridge)
        self.assertIn("delta_vs_ridge", report)
        self.assertTrue(report.get("feature_importance"))
        timing = report.get("timing") or {}
        self.assertIn("panel_s", timing)
        self.assertIn("tree_s", timing)
        self.assertIn("ridge_s", timing)
        self.assertIn("fit_s", timing)
        self.assertEqual((report.get("hyperparams") or {}).get("n_estimators"), 20)

        blocked = persist_tc_model(report, note="should fail", force=True)
        self.assertFalse(blocked.get("success"))

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_tc_tree_last_report(report)
                tree_path = tc_tree_last_report_path()
                self.assertTrue(os.path.isfile(tree_path))
                self.assertIn("tc_tree_last_report.json", tree_path)
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "tc_ridge_model.json"))
                )
                self.assertFalse(
                    os.path.isfile(os.path.join(live, "tc_ridge_model_research.json"))
                )
                self.assertIsNone(load_tc_model())

    def test_open_tau_coerced_to_1030(self):
        from core.research.tc_tree import fit_tc_tree_report

        report = fit_tc_tree_report(
            [_stock("A", 10.0, n_days=36), _stock("B", 11.0, n_days=36)],
            backend="lightgbm",
            n_estimators=8,
            tau_hm="open",
            tau_grid=["09:30", "10:30"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("tau"), "10:30")


if __name__ == "__main__":
    unittest.main()
