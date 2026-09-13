"""ŷ_τc：close[T]/price(τ)−1 标签与 Ridge。"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date, timedelta

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


class YRPctTests(unittest.TestCase):
    def test_y_r_pct_matches_geometry(self):
        from core.research.tau_panel import y_r_pct

        self.assertAlmostEqual(y_r_pct(69.20, 68.12), (68.12 / 69.20 - 1.0) * 100.0, places=6)
        self.assertIsNone(y_r_pct(0, 68.12))
        self.assertIsNone(y_r_pct(69.20, None))

    def test_relabel_keeps_price_tau_rows(self):
        from core.research.tau_panel import relabel_tau_panels_as_r, y_r_pct

        yr = y_r_pct(10.5, 10.0)
        panel = {
            "xs": [
                {"gap_pct": 1.0},
                {"gap_pct": 0.5},
                {"gap_pct": -0.2},
                {"gap_pct": 0.1},
                {"gap_pct": 0.3},
            ],
            "ys": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dates": [
                "2025-06-02",
                "2025-06-03",
                "2025-06-04",
                "2025-06-05",
                "2025-06-06",
            ],
            "metas": [
                {"price_tau": 10.5, "close": 11.0, "close_minute": 10.0},
                {"price_tau": 9.8, "close": 11.0, "close_minute": 10.0},
                {"price_tau": None, "close": 11.0, "close_minute": 10.0},
                {"price_tau": 10.2, "close": 11.0, "close_minute": 10.1},
                {"price_tau": 10.1, "close": 11.0, "close_minute": 10.0},
            ],
        }
        out = relabel_tau_panels_as_r([panel])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["ys"]), 4)
        self.assertAlmostEqual(out[0]["ys"][0], yr, places=6)
        self.assertIn("y_r", out[0]["metas"][0])

    def test_relabel_drops_daily_close_only_rows(self):
        from core.research.tau_panel import relabel_tau_panels_as_r

        panel = {
            "xs": [{"gap_pct": 1.0}] * 5,
            "ys": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dates": [f"2025-06-0{i}" for i in range(2, 7)],
            "metas": [{"price_tau": 10.0, "close": 11.0} for _ in range(5)],
        }
        self.assertEqual(relabel_tau_panels_as_r([panel]), [])


class RRidgeFitTests(unittest.TestCase):
    def test_fit_report_runs_on_synthetic(self):
        from core.research.r_ridge import fit_r_ridge_report
        from core.research.tau_ridge import TAU_Z_FEATURES

        d0 = date(2025, 6, 2)
        daily = []
        minutes = []
        px = 10.0
        times = _times_am_pm()
        for i in range(45):
            day = d0 + timedelta(days=i)
            if day.weekday() >= 5:
                continue
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.04 if j % 2 else 0.0) + j * 0.002
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
        report = fit_r_ridge_report(
            [{"code": "600000", "bars": daily, "minute_bars": minutes}],
            ridge_lambda=1.0,
            tau_grid=["09:30", "09:50", "10:30"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), msg=report)
        self.assertEqual(report.get("schema"), "r_ridge_v1")
        self.assertEqual(report.get("dual_score_head"), "y_r")
        self.assertEqual(report.get("target"), "close_over_price_tau")
        y_spec = (report.get("return_model") or {}).get("y_spec") or {}
        self.assertIn("price", str(y_spec.get("formula") or ""))
        self.assertEqual(y_spec.get("unit"), "pct")
        extras = (report.get("return_model") or {}).get("extra_features") or []
        self.assertEqual(list(extras), list(TAU_Z_FEATURES))
        oos = report.get("oos") or {}
        self.assertEqual(oos.get("split_mode"), "holdout_days")
        self.assertEqual(int(oos.get("holdout_trading_days") or 0), 5)
        self.assertEqual(int(oos.get("n_test_days") or 0), 5)
        self.assertGreater(int(oos.get("n_train") or 0), int(oos.get("n_test") or 0))
        self.assertEqual(report.get("eval_start"), oos.get("eval_start"))


class YRDisplayOnlyTests(unittest.TestCase):
    def test_scores_from_item_passes_y_r(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "y_tau_oc": 1.2,
                "predicted_score_r": 0.85,
                "y_r_hat": 0.85,
                "y_r": 0.85,
                "r_realized": 0.4,
            }
        )
        self.assertAlmostEqual(sc.get("y_tau"), 1.2)
        self.assertAlmostEqual(sc.get("predicted_score_r"), 0.85)
        self.assertAlmostEqual(sc.get("y_r"), 0.85)
        self.assertAlmostEqual(sc.get("y_τc"), 0.85)
        self.assertAlmostEqual(sc.get("r_realized"), 0.4)

    def test_scores_from_item_does_not_invert_bare_y_r(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item({"y_tau_oc": 0.1, "y_r": -0.8})
        self.assertAlmostEqual(sc.get("y_τc"), -0.8, places=6)
        self.assertAlmostEqual(sc.get("y_r"), -0.8, places=6)

    def test_explain_r_prediction_head(self):
        from core.research.r_ridge import explain_r_prediction

        model = {
            "model_role": "research",
            "return_model": {
                "coefficients": {"gap_pct": 0.5},
                "intercept": 0.1,
                "zscore_means": {"gap_pct": 0.0},
                "zscore_stds": {"gap_pct": 1.0},
                "active_features": ["gap_pct"],
            }
        }
        expl = explain_r_prediction({"gap_pct": 2.0}, model_doc=model)
        self.assertIsNotNone(expl)
        self.assertEqual(expl.get("head"), "r")
        self.assertEqual(expl.get("model_role"), "research")
        self.assertAlmostEqual(float(expl.get("total")), 1.1, places=5)
        keys = [t.get("key") for t in (expl.get("terms") or [])]
        self.assertIn("gap_pct", keys)

    def test_close_band_enter_does_not_read_y_r(self):
        from core.t0 import close_band

        src = inspect.getsource(close_band.close_band_enter_skip_reason)
        self.assertNotIn("y_r", src)
        src2 = inspect.getsource(close_band._enter_profile_skip_reason)
        self.assertNotIn("y_r", src2)


if __name__ == "__main__":
    unittest.main()
