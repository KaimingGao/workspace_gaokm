"""ŷ_complexity 曲折度标签与 Ridge。"""

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


class CxLabelTests(unittest.TestCase):
    def test_straight_line_near_zero(self):
        from core.research.cx_panel import cx_complexity_label

        dkey = "2025-06-02"
        bars = [
            _bar(dkey, hm, 10.0 + i * 0.02)
            for i, hm in enumerate(_times_am_pm())
        ]
        y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(y)
        self.assertLess(float(y), 0.05, msg=meta)

    def test_oscillation_high_complexity(self):
        from core.research.cx_panel import cx_complexity_label

        dkey = "2025-06-02"
        bars = []
        for i, hm in enumerate(_times_am_pm()):
            close = 10.0 + (0.08 if i % 2 else 0.0)
            bars.append(_bar(dkey, hm, close, open_px=10.0))
        y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertGreater(float(y), 0.80, msg=meta)

    def test_lunch_gap_not_counted_as_wiggle(self):
        from core.research.cx_panel import cx_complexity_label

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
            bars.append(_bar(dkey, hm, pm0 + i * 0.01))
        y, reason, meta = cx_complexity_label(bars)
        self.assertEqual(reason, "ok")
        self.assertGreaterEqual(meta.get("n_skipped_gaps") or 0, 1)
        self.assertLess(float(y), 0.08, msg=meta)

    def test_too_few_bars(self):
        from core.research.cx_panel import cx_complexity_label

        bars = [_bar("2025-06-02", "09:35", 10.0), _bar("2025-06-02", "09:40", 10.1)]
        y, reason, _meta = cx_complexity_label(bars)
        self.assertIsNone(y)
        self.assertEqual(reason, "too_few_bars")

    def test_attach_cx_realized(self):
        from core.research.cx_panel import attach_cx_realized

        dkey = "2025-06-02"
        bars = []
        for i, hm in enumerate(_times_am_pm()):
            close = 10.0 + (0.08 if i % 2 else 0.0)
            bars.append(_bar(dkey, hm, close, open_px=10.0))
        day = attach_cx_realized({"scores": {"y_path": -1.2}}, bars)
        self.assertGreater(day.get("y_complexity"), 0.80)
        self.assertEqual(day.get("y_complexity"), day.get("complexity_realized"))
        self.assertEqual(day.get("y_complexity"), day.get("y_cx"))
        self.assertEqual(day.get("cx_realized"), day.get("complexity_realized"))
        self.assertEqual(day.get("cx_realized_reason"), "ok")
        self.assertEqual(day["scores"].get("y_complexity"), day.get("y_complexity"))
        self.assertEqual(day["direction_features"].get("y_complexity"), day.get("y_complexity"))
        self.assertIsNotNone(day.get("cx_efficiency"))
        self.assertIsNotNone(day.get("y_tpd"))
        self.assertEqual(day.get("y_tpd"), day.get("tpd_realized"))
        self.assertEqual(day.get("y_tpd"), day.get("y_complexity_tpd"))
        self.assertEqual(day["scores"].get("y_tpd"), day.get("y_tpd"))

    def test_cx_as_unit_01(self):
        from core.research.cx_panel import cx_as_unit_01

        self.assertAlmostEqual(cx_as_unit_01(0.62), 0.62)
        self.assertAlmostEqual(cx_as_unit_01(62.0), 0.62)
        self.assertAlmostEqual(
            cx_as_unit_01(40.0, model_doc={"return_model": {"y_spec": {"unit": "pct_complexity"}}}),
            0.4,
        )
        self.assertAlmostEqual(
            cx_as_unit_01(0.4, model_doc={"return_model": {"y_spec": {"unit": "complexity_01"}}}),
            0.4,
        )

    def test_pick_y_complexity_label_zero_is_valid(self):
        from core.research.cx_panel import pack_y_complexity_fields, pick_y_complexity_label

        self.assertEqual(pick_y_complexity_label({"y_complexity": 0.0}), 0.0)
        self.assertEqual(pick_y_complexity_label({"y_cx": 0.0}), 0.0)
        self.assertEqual(pick_y_complexity_label({"y_complexity": 0.0, "y_cx": 0.9}), 0.0)
        packed = pack_y_complexity_fields({"y_cx": 0.0, "cx_realized_reason": "flat_path"})
        self.assertEqual(packed["y_complexity"], 0.0)
        self.assertEqual(packed["y_cx"], 0.0)
        self.assertEqual(packed["complexity_realized"], 0.0)
        self.assertEqual(packed["complexity_realized_reason"], "flat_path")
        self.assertIsNone(pick_y_complexity_label({}))
        self.assertIsNone(pack_y_complexity_fields(None)["y_complexity"])

    def test_pick_y_tpd_label_zero_is_valid(self):
        from core.research.cx_panel import pack_y_tpd_fields, pick_y_tpd_label

        self.assertEqual(pick_y_tpd_label({"y_tpd": 0.0}), 0.0)
        self.assertEqual(pick_y_tpd_label({"y_complexity_tpd": 0.0}), 0.0)
        self.assertEqual(pick_y_tpd_label({"y_tpd": 0.0, "y_complexity_tpd": 0.9}), 0.0)
        packed = pack_y_tpd_fields({"y_tpd": 0.0})
        self.assertEqual(packed["y_tpd"], 0.0)
        self.assertEqual(packed["tpd_realized"], 0.0)
        self.assertIsNone(pick_y_tpd_label({}))
        self.assertIsNone(pack_y_tpd_fields(None)["y_tpd"])


class CxRidgeFitTests(unittest.TestCase):
    def test_fit_report_runs_on_synthetic(self):
        from datetime import date, timedelta

        from core.research.cx_ridge import fit_cx_ridge_report

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
        report = fit_cx_ridge_report(
            [{"code": "600000", "bars": daily}],
            minute_by_code_date={"600000": minute_by_date},
            ridge_lambda=1.0,
            tau_grid=["09:30", "10:30"],
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("schema"), "cx_ridge_v1")
        self.assertEqual(report.get("dual_score_head"), "y_complexity")
        oos = report.get("oos") or {}
        self.assertIn("ic", oos)
        self.assertIn("median_hit", oos)
        self.assertNotIn("sign_hit", oos)
        y_spec = (report.get("return_model") or {}).get("y_spec") or {}
        self.assertIn("1-D/L", y_spec.get("formula") or "")
        extras = (report.get("return_model") or {}).get("extra_features") or []
        self.assertIn("complexity_lag1", extras)
        self.assertIn("complexity_ma5", extras)
        self.assertIn("tpd_lag1", extras)
        self.assertIn("tpd_ma5", extras)
        self.assertNotIn("complexity_tpd_lag1", extras)
        self.assertNotIn("cx_L_lag1", extras)
        self.assertNotIn("cx_am_lag1", extras)
        labels = (report.get("return_model") or {}).get("feat_labels") or {}
        self.assertIn("complexity_lag1", labels)
        self.assertIn("tpd_lag1", labels)
        self.assertNotIn("cx_lag1", labels)
        self.assertNotIn("complexity_tpd_lag1", labels)


class CxLagFeatureTests(unittest.TestCase):
    def test_lag1_and_ma5_are_pit(self):
        from core.research.cx_panel import (
            cx_complexity_label,
            cx_lag_features,
            realized_cx_by_date,
        )
        from core.research.path_panel import PATH_Z_FEATURES

        self.assertNotIn("complexity_lag1", PATH_Z_FEATURES)
        self.assertNotIn("cx_lag1", PATH_Z_FEATURES)
        self.assertNotIn("path_lag1", PATH_Z_FEATURES)
        self.assertNotIn("cx_L_lag1", PATH_Z_FEATURES)
        self.assertNotIn("cx_am_lag1", PATH_Z_FEATURES)
        times = _times_am_pm()
        minute_by_date = {}
        daily = []
        px = 10.0
        days = ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05", "2025-06-06", "2025-06-09"]
        labels = []
        for i, dkey in enumerate(days):
            wiggly = i % 2 == 1
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.06 if (wiggly and j % 2) else (0.0 if wiggly else j * 0.01))
                bars_m.append(_bar(dkey, hm, c, open_px=px))
            minute_by_date[dkey] = bars_m
            y, reason, _ = cx_complexity_label(bars_m)
            self.assertEqual(reason, "ok")
            labels.append(float(y))
            daily.append({"date": dkey, "open": px, "close": bars_m[-1]["close"]})
            px = bars_m[-1]["close"]
        cx_map = realized_cx_by_date(minute_by_date)
        asof = days[-1]
        lags = cx_lag_features(
            hist_bars=daily,
            cx_by_date=cx_map,
            asof_date=asof,
        )
        self.assertAlmostEqual(lags["complexity_lag1"], labels[-2], places=5)
        self.assertAlmostEqual(lags["cx_lag1"], labels[-2], places=5)
        self.assertNotAlmostEqual(lags["complexity_lag1"], labels[-1], places=2)
        self.assertNotAlmostEqual(lags["cx_lag1"], labels[-1], places=2)
        expected_ma = sum(labels[-6:-1][-5:]) / 5.0
        self.assertAlmostEqual(lags["complexity_ma5"], expected_ma, places=5)
        self.assertAlmostEqual(lags["cx_ma5"], expected_ma, places=5)

    def test_lag_skips_days_without_minutes(self):
        from core.research.cx_panel import cx_lag_features

        hist = [
            {"date": "2025-06-02"},
            {"date": "2025-06-03"},
            {"date": "2025-06-04"},
            {"date": "2025-06-05"},
        ]
        cx_map = {"2025-06-02": 0.10, "2025-06-04": 0.40}
        lags = cx_lag_features(
            hist_bars=hist,
            cx_by_date=cx_map,
            asof_date="2025-06-05",
        )
        self.assertAlmostEqual(lags["complexity_lag1"], 0.40)
        self.assertAlmostEqual(lags["cx_lag1"], 0.40)
        self.assertAlmostEqual(lags["complexity_ma5"], 0.25)
        self.assertAlmostEqual(lags["cx_ma5"], 0.25)

    def test_attach_cx_lag_from_minute_map(self):
        from core.research.cx_panel import attach_cx_lag_features, cx_complexity_label

        times = _times_am_pm()
        d0, d1, d2 = "2025-06-02", "2025-06-03", "2025-06-04"
        m0 = [_bar(d0, hm, 10.0 + j * 0.01) for j, hm in enumerate(times)]
        m1 = [
            _bar(d1, hm, 10.0 + (0.08 if j % 2 else 0.0), open_px=10.0)
            for j, hm in enumerate(times)
        ]
        y1, _, _ = cx_complexity_label(m1)
        feats = attach_cx_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d1}, {"date": d2}],
            asof_date=d2,
            minute_by_date={d0: m0, d1: m1, d2: m0},
        )
        self.assertAlmostEqual(feats["complexity_lag1"], float(y1), places=5)
        self.assertAlmostEqual(feats["cx_lag1"], float(y1), places=5)
        self.assertIsNotNone(feats["complexity_ma5"])
        self.assertIsNotNone(feats["cx_ma5"])
        y2, _, _ = cx_complexity_label(m0)
        self.assertNotAlmostEqual(feats["complexity_lag1"], float(y2), places=2)
        self.assertNotAlmostEqual(feats["cx_lag1"], float(y2), places=2)

    def test_t_day_cx_label_not_used_as_factor(self):
        from core.research.cx_panel import (
            attach_cx_lag_features,
            cx_complexity_label,
            cx_lag_features,
        )

        times = _times_am_pm()
        d0, d_t = "2025-06-02", "2025-06-03"
        m0 = [_bar(d0, hm, 10.0 + j * 0.01) for j, hm in enumerate(times)]
        mt = [
            _bar(d_t, hm, 10.0 + (0.08 if j % 2 else 0.0), open_px=10.0)
            for j, hm in enumerate(times)
        ]
        y0, _, _ = cx_complexity_label(m0)
        yt, _, _ = cx_complexity_label(mt)
        feats = attach_cx_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d_t}],
            asof_date=d_t,
            minute_by_date={d0: m0, d_t: mt},
        )
        self.assertAlmostEqual(feats["complexity_lag1"], float(y0), places=5)
        self.assertAlmostEqual(feats["cx_lag1"], float(y0), places=5)
        self.assertNotAlmostEqual(feats["complexity_lag1"], float(yt), places=2)
        self.assertNotAlmostEqual(feats["cx_lag1"], float(yt), places=2)
        empty = cx_lag_features(
            hist_bars=[{"date": d0}, {"date": d_t}],
            cx_by_date={d0: y0, d_t: yt},
            asof_date="",
        )
        self.assertIsNone(empty["complexity_lag1"])
        self.assertIsNone(empty["cx_lag1"])

    def test_attach_does_not_write_withdrawn_hist_keys(self):
        from core.research.cx_panel import attach_cx_lag_features

        times = _times_am_pm()
        d0, d_t = "2025-06-02", "2025-06-03"
        m0 = [_bar(d0, hm, 10.0 + j * 0.01) for j, hm in enumerate(times)]
        mt = [
            _bar(d_t, hm, 10.0 + (0.08 if j % 2 else 0.0), open_px=10.0)
            for j, hm in enumerate(times)
        ]
        feats = attach_cx_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d_t}],
            asof_date=d_t,
            minute_by_date={d0: m0, d_t: mt},
        )
        self.assertNotIn("cx_am_lag1", feats)
        self.assertNotIn("cx_L_lag1", feats)


if __name__ == "__main__":
    unittest.main()
