"""ŷ_tpd 转折点密度标签与 Ridge。"""

from __future__ import annotations

import os
import sys
import unittest

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


class TpdLabelTests(unittest.TestCase):
    def test_straight_line_near_zero(self):
        from core.research.cx_panel import cx_complexity_label

        dkey = "2025-06-02"
        bars = [
            _bar(dkey, hm, 10.0 + i * 0.02)
            for i, hm in enumerate(_times_am_pm())
        ]
        _y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(meta.get("tpd"))
        self.assertLess(float(meta["tpd"]), 0.05, msg=meta)

    def test_oscillation_high_tpd(self):
        from core.research.cx_panel import cx_complexity_label

        dkey = "2025-06-02"
        bars = []
        for i, hm in enumerate(_times_am_pm()):
            close = 10.0 + (0.08 if i % 2 else 0.0)
            bars.append(_bar(dkey, hm, close, open_px=10.0))
        _y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertGreater(float(meta["tpd"]), 0.80, msg=meta)

    def test_lunch_gap_not_counted_as_turn(self):
        from core.research.cx_panel import cx_complexity_label, tpd_from_closes

        dkey = "2025-06-02"
        times = _times_am_pm()
        am = [t for t in times if t < "12:00"]
        pm = [t for t in times if t >= "13:00"]
        bars = []
        for i, hm in enumerate(am):
            bars.append(_bar(dkey, hm, 10.0 + i * 0.01))
        am_last = 10.0 + (len(am) - 1) * 0.01
        pm0 = am_last * 1.03
        for i, hm in enumerate(pm):
            bars.append(_bar(dkey, hm, pm0 - i * 0.01))
        _y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertLess(float(meta["tpd"]), 0.05, msg=meta)
        closes = [b["close"] for b in bars]
        tpd_raw = tpd_from_closes(closes)
        self.assertIsNotNone(tpd_raw)
        self.assertGreater(float(tpd_raw), float(meta["tpd"]))

    def test_too_few_closes_none(self):
        from core.research.cx_panel import tpd_from_closes

        self.assertIsNone(tpd_from_closes([10.0, 10.1]))


class TpdLagFeatureTests(unittest.TestCase):
    def test_tpd_lag1_is_pit(self):
        from core.research.cx_panel import (
            attach_cx_lag_features,
            cx_complexity_label,
            cx_tpd_lag_features,
            realized_cx_stats_by_date,
        )

        times = _times_am_pm()
        days = ["2025-06-02", "2025-06-03", "2025-06-04"]
        minute_by_date = {}
        daily = []
        labels = []
        px = 10.0
        for i, dkey in enumerate(days):
            wiggly = i % 2 == 1
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.08 if (wiggly and j % 2) else (0.0 if wiggly else j * 0.01))
                bars_m.append(_bar(dkey, hm, c, open_px=px))
            minute_by_date[dkey] = bars_m
            _y, reason, meta = cx_complexity_label(bars_m)
            self.assertEqual(reason, "ok")
            labels.append(float(meta["tpd"]))
            daily.append({"date": dkey, "open": px, "close": bars_m[-1]["close"]})
            px = bars_m[-1]["close"]
        tpd_map = dict(realized_cx_stats_by_date(minute_by_date).get("tpd") or {})
        lags = cx_tpd_lag_features(
            hist_bars=daily,
            tpd_by_date=tpd_map,
            asof_date=days[-1],
        )
        self.assertAlmostEqual(lags["tpd_lag1"], labels[-2], places=5)
        self.assertAlmostEqual(lags["complexity_tpd_lag1"], labels[-2], places=5)
        self.assertNotAlmostEqual(lags["tpd_lag1"], labels[-1], places=2)
        feats = attach_cx_lag_features(
            {},
            hist_bars=daily,
            asof_date=days[-1],
            minute_by_date=minute_by_date,
        )
        self.assertAlmostEqual(feats["tpd_lag1"], labels[-2], places=5)
        self.assertAlmostEqual(feats["complexity_tpd_lag1"], labels[-2], places=5)
        self.assertAlmostEqual(feats["cx_tpd_lag1"], labels[-2], places=5)


class TpdRidgeFitTests(unittest.TestCase):
    def test_fit_report_runs_on_synthetic(self):
        from datetime import date, timedelta

        from core.research.cx_panel import TPD_Z_FEATURES
        from core.research.tpd_ridge import fit_tpd_ridge_report

        d0 = date(2025, 6, 2)
        daily = []
        minute_by_date = {}
        px = 10.0
        times = _times_am_pm()
        for i in range(28):
            day = d0 + timedelta(days=i)
            if day.weekday() >= 5:
                continue
            dkey = day.isoformat()
            wiggly = i % 2 == 1
            bars_m = []
            for j, hm in enumerate(times):
                if wiggly:
                    c = px + (0.06 if j % 2 else 0.0)
                else:
                    c = px + j * 0.01
                bars_m.append(_bar(dkey, hm, c, open_px=px))
            minute_by_date[dkey] = bars_m
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
        report = fit_tpd_ridge_report(
            [{"code": "600000", "bars": daily}],
            minute_by_code_date={"600000": minute_by_date},
            ridge_lambda=1.0,
            tau_grid=["09:30", "10:30"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("schema"), "tpd_ridge_v1")
        self.assertEqual(report.get("dual_score_head"), "y_tpd")
        oos = report.get("oos") or {}
        self.assertIn("ic", oos)
        self.assertIn("median_hit", oos)
        self.assertNotIn("sign_hit", oos)
        self.assertIn("spearman_y_complexity_y_tpd", oos)
        y_spec = (report.get("return_model") or {}).get("y_spec") or {}
        self.assertIn("TPD", y_spec.get("formula") or "")
        self.assertEqual(y_spec.get("unit"), "tpd_01")
        extras = (report.get("return_model") or {}).get("extra_features") or []
        self.assertEqual(list(extras), list(TPD_Z_FEATURES))
        self.assertIn("tpd_lag1", extras)
        self.assertIn("tpd_ma5", extras)
        self.assertIn("complexity_lag1", extras)
        self.assertIn("complexity_ma5", extras)
        self.assertNotIn("complexity_tpd_lag1", extras)
        labels = (report.get("return_model") or {}).get("feat_labels") or {}
        self.assertIn("tpd_lag1", labels)
        self.assertIn("complexity_lag1", labels)
        self.assertNotIn("cx_lag1", labels)
        self.assertNotIn("complexity_tpd_lag1", labels)


if __name__ == "__main__":
    unittest.main()
