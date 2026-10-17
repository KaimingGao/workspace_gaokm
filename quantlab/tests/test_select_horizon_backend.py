"""ŷ_τ30…90 双 backend：Holdout AUC 择优。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class SelectHorizonBackendTests(unittest.TestCase):
    def test_select_by_auc(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.62, "brier": 0.25, "success": True},
                {"auc": 0.55, "brier": 0.22, "success": True},
            ),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.20, "success": True},
                {"auc": 0.62, "brier": 0.25, "success": True},
            ),
            "ridge",
        )

    def test_tie_auc_uses_brier_then_ridge(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.20, "success": True},
                {"auc": 0.55, "brier": 0.24, "success": True},
            ),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.24, "success": True},
                {"auc": 0.55, "brier": 0.20, "success": True},
            ),
            "ridge",
        )
        # 打平 → ridge
        self.assertEqual(
            select_horizon_backend(
                {"auc": 0.55, "brier": 0.22, "success": True},
                {"auc": 0.55, "brier": 0.22, "success": True},
            ),
            "ridge",
        )

    def test_missing_sides(self):
        from core.research.horizon_prob import select_horizon_backend

        self.assertEqual(
            select_horizon_backend(None, {"auc": 0.7, "success": True}),
            "ridge",
        )
        self.assertEqual(
            select_horizon_backend({"auc": 0.7, "success": True}, None),
            "tree",
        )
        self.assertEqual(
            select_horizon_backend({"success": False}, {"auc": 0.6, "success": True}),
            "ridge",
        )
        self.assertEqual(select_horizon_backend(None, None), "ridge")

    def test_promote_gate_uses_winner_oos(self):
        from core.research.horizon_prob import horizon_promote_gate

        report = {
            "recommended_backend": "tree",
            "oos": {
                "auc": 0.60,
                "acc_at_50": 0.58,
                "n_test": 100,
                "ridge": {"auc": 0.51, "acc_at_50": 0.50, "n_test": 100},
                "tree": {"auc": 0.60, "acc_at_50": 0.58, "n_test": 100},
            },
        }
        gate = horizon_promote_gate(report)
        self.assertTrue(gate.get("ok"), gate)
        self.assertEqual(gate.get("backend"), "tree")
        self.assertAlmostEqual(float(gate["auc"]), 0.60)

        # 胜出侧未过闸
        report_bad = {
            "recommended_backend": "tree",
            "oos": {
                "ridge": {"auc": 0.60, "acc_at_50": 0.58, "n_test": 100},
                "tree": {"auc": 0.50, "acc_at_50": 0.50, "n_test": 100},
            },
        }
        gate_bad = horizon_promote_gate(report_bad)
        self.assertFalse(gate_bad.get("ok"), gate_bad)


class HorizonDualMergeTests(unittest.TestCase):
    def test_backend_pack_and_doc_backend(self):
        from core.research.horizon_ridge import _backend_pack_from_report, _doc_backend

        report = {
            "backends": {
                "ridge": {"return_model": {"coefficients": {"a": 1.0}}},
                "tree": {"return_model": {"feature_names": ["a"], "booster": {}}},
            },
            "return_model": {"coefficients": {"a": 1.0}},
        }
        self.assertIsNotNone(_backend_pack_from_report(report, "tree"))
        self.assertEqual(
            _doc_backend({"backend": "tree", "return_model": report["backends"]["tree"]["return_model"]}),
            "tree",
        )
        self.assertEqual(_doc_backend({"return_model": {"coefficients": {"a": 1}}}), "ridge")


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


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
    from datetime import date, timedelta

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


class HorizonDualFitTests(unittest.TestCase):
    def test_dual_falls_back_ridge_when_tree_fails(self):
        from unittest.mock import patch

        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        with patch(
            "core.research.horizon_tree.fit_horizon_tree_report",
            return_value={"success": False, "error": "mock tree down"},
        ):
            report = fit_horizon_dual_report(
                "t30",
                stock_bars,
                ridge_lambda=1.0,
                holdout_trading_days=8,
                tau_grid=["09:30", "09:50", "10:30", "11:15"],
            )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("recommended_backend"), "ridge")
        self.assertFalse(report.get("tree_success"))
        self.assertIn("ridge", report.get("backends") or {})
        self.assertNotIn("tree", report.get("backends") or {})

    def test_dual_keeps_tree_gain_when_ridge_wins(self):
        from unittest.mock import patch

        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        tree_fi = [{"key": "gap_pct", "gain": 1.0, "share": 1.0}]
        tree_rm = {"feature_names": ["gap_pct"], "booster": {}, "backend": "lightgbm"}
        with patch(
            "core.research.horizon_tree.fit_horizon_tree_report",
            return_value={
                "success": True,
                "oos": {"auc": 0.51, "brier": 0.30, "n_test": 40, "success": True},
                "tree_return_model": tree_rm,
                "feature_importance": tree_fi,
                "tree_shape_features": ["range_pct"],
                "timing": {"tree_s": 0.1},
            },
        ), patch(
            "core.research.horizon_ridge.select_horizon_backend",
            return_value="ridge",
        ):
            report = fit_horizon_dual_report(
                "t30",
                stock_bars,
                ridge_lambda=1.0,
                holdout_trading_days=8,
                tau_grid=["09:30", "09:50", "10:30", "11:15"],
            )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("recommended_backend"), "ridge")
        self.assertEqual(report.get("feature_importance"), tree_fi)
        tree_pack = (report.get("backends") or {}).get("tree") or {}
        self.assertEqual(
            (tree_pack.get("return_model") or {}).get("feature_importance"),
            tree_fi,
        )

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_dual_report_has_backends(self):
        from core.research.horizon_ridge import fit_horizon_dual_report

        stock_bars = [
            _stock("A", 10.0),
            _stock("B", 12.0),
            _stock("C", 8.0),
            _stock("D", 15.0),
        ]
        report = fit_horizon_dual_report(
            "t30",
            stock_bars,
            ridge_lambda=1.0,
            holdout_trading_days=8,
            n_estimators=20,
            tau_grid=["09:30", "09:50", "10:30", "11:15"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertIn(report.get("recommended_backend"), ("ridge", "tree"))
        backends = report.get("backends") or {}
        self.assertIn("ridge", backends)
        self.assertTrue(report.get("tree_success"), report.get("tree_error"))
        self.assertIn("tree", backends)
        oos = report.get("oos") or {}
        self.assertIn("ridge", oos)
        self.assertIn("tree", oos)
        self.assertIsInstance((backends["ridge"] or {}).get("return_model"), dict)
        self.assertIsInstance((backends["tree"] or {}).get("return_model"), dict)


if __name__ == "__main__":
    unittest.main()
