"""ŷ_τ60：交易时钟 ⊕60m、标签 relabel、Ridge、做 T 旁路闸。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bar(dkey: str, hm: str, close: float, open_px: float | None = None, volume: float = 1000):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "datetime": f"{dkey} {hm}:00",
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": volume,
    }


def _times_am_pm():
    out = []
    for total in range(9 * 60 + 30, 11 * 60 + 30 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    for total in range(13 * 60, 15 * 60 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    return out


class SessionClockTests(unittest.TestCase):
    def test_add_session_minutes_lunch_and_close(self):
        from core.signal.minute_tau_grid import add_session_minutes, session_elapsed

        self.assertEqual(session_elapsed("09:30"), 0.0)
        self.assertEqual(session_elapsed("11:00"), 90.0)
        self.assertEqual(session_elapsed("11:30"), 120.0)
        self.assertEqual(session_elapsed("13:00"), 120.0)
        self.assertEqual(session_elapsed("14:00"), 180.0)
        self.assertEqual(add_session_minutes("09:30", 60), "10:30")
        self.assertEqual(add_session_minutes("11:00", 60), "13:30")
        self.assertEqual(add_session_minutes("11:30", 60), "14:00")
        self.assertEqual(add_session_minutes("14:00", 60), "15:00")
        self.assertEqual(add_session_minutes("13:55", 65), "15:00")
        self.assertIsNone(add_session_minutes("14:05", 60))
        self.assertIsNone(add_session_minutes("14:00", 65))
        self.assertIsNone(add_session_minutes("15:00", 60))

    def test_tau_clock_allows_t60_needs_65m(self):
        from core.signal.minute_tau_grid import tau_clock_allows_t60

        self.assertTrue(tau_clock_allows_t60("13:55"))
        self.assertFalse(tau_clock_allows_t60("14:00"))
        self.assertFalse(tau_clock_allows_t60("14:05"))

    def test_sub_session_and_lunch_flags(self):
        from core.signal.minute_tau_grid import (
            horizon_crosses_lunch,
            session_remain,
            sub_session_minutes,
        )

        self.assertEqual(sub_session_minutes("10:30", 60), "09:30")
        self.assertEqual(sub_session_minutes("13:30", 60), "11:00")
        self.assertIsNone(sub_session_minutes("10:00", 60))
        self.assertEqual(horizon_crosses_lunch("11:00", add_min=60), 1.0)
        self.assertEqual(horizon_crosses_lunch("10:30", add_min=60), 0.0)
        self.assertEqual(horizon_crosses_lunch("11:30", add_min=60), 1.0)
        self.assertEqual(session_remain("14:00"), 60.0)

    def test_extract_t60_seq_pack_trail_and_clock(self):
        from core.signal.minute_tau_feats import extract_t60_seq_pack

        dkey = "2025-06-02"
        bars = []
        px = 10.0
        for total in range(9 * 60 + 30, 10 * 60 + 30 + 1, 5):
            hm = f"{total // 60:02d}:{total % 60:02d}"
            c = px + (total - (9 * 60 + 30)) * 0.01
            bars.append(_bar(dkey, hm, c, open_px=px))
        pack = extract_t60_seq_pack(
            bars, trade_date=dkey, tau_hm="10:30", open_px=px
        )
        self.assertEqual(pack.get("session_elapsed"), 60.0)
        self.assertEqual(pack.get("session_remain"), 180.0)
        self.assertEqual(pack.get("crosses_lunch_60"), 0.0)
        self.assertNotIn("crosses_lunch", pack)
        self.assertAlmostEqual(
            pack["ret_last_5m"], (10.6 / 10.55 - 1.0) * 100.0, places=4
        )
        self.assertAlmostEqual(
            pack["ret_last_60m"], (10.6 / 10.0 - 1.0) * 100.0, places=4
        )
        self.assertIn("session_vwap_dev", pack)
        self.assertGreater(pack["session_vwap_dev"], 0.0)
        self.assertAlmostEqual(pack["vol_last_60m_vs_avg"], 1.0, places=4)
        lunch = extract_t60_seq_pack([], trade_date=dkey, tau_hm="11:00")
        self.assertEqual(lunch.get("crosses_lunch_60"), 1.0)
        self.assertEqual(lunch.get("session_elapsed"), 90.0)
        self.assertIsNone(lunch.get("session_vwap_dev"))
        early = extract_t60_seq_pack([], trade_date=dkey, tau_hm="10:00")
        self.assertIsNone(early.get("ret_last_60m"))
        self.assertIsNone(early.get("vol_last_60m_vs_avg"))
        self.assertEqual(early.get("session_elapsed"), 30.0)
        noon_t30 = extract_t60_seq_pack([], trade_date=dkey, tau_hm="11:00")
        self.assertEqual(noon_t30.get("crosses_lunch_60"), 1.0)

    def test_extract_t60_seq_pack_vol_ratio_uses_last_60m(self):
        from core.signal.minute_tau_feats import extract_t60_seq_pack

        dkey = "2025-06-02"
        bars = []
        px = 10.0
        for i, total in enumerate(range(9 * 60 + 30, 10 * 60 + 30 + 1, 5)):
            hm = f"{total // 60:02d}:{total % 60:02d}"
            c = px + i * 0.01
            vol = 400 if i == 0 else 1200
            bars.append(_bar(dkey, hm, c, open_px=px, volume=vol))
        pack = extract_t60_seq_pack(
            bars, trade_date=dkey, tau_hm="10:30", open_px=px
        )
        # 13 根：首根 400，后 12 根（>09:30）1200
        self.assertAlmostEqual(
            pack["vol_last_60m_vs_avg"], 1200.0 / (14800.0 / 13.0), places=4
        )

    def test_cross_section_attaches_t60_sector_ret(self):
        from core.research.tau_panel import attach_cross_section_breadth

        panels = [
            {
                "code": "600000",
                "xs": [{"gap_pct": 1.0}, {"gap_pct": 1.0}],
                "ys": [1.0, 1.0],
                "dates": ["2025-06-02", "2025-06-02"],
                "metas": [
                    {
                        "date": "2025-06-02",
                        "stock_code": "600000",
                        "gap_pct": 1.0,
                        "tau": "10:30",
                        "ret_last_60m": 1.0,
                    },
                    {
                        "date": "2025-06-02",
                        "stock_code": "600000",
                        "gap_pct": 1.0,
                        "tau": "11:00",
                        "ret_last_60m": 2.0,
                    },
                ],
            },
            {
                "code": "600001",
                "xs": [{"gap_pct": 1.0}, {"gap_pct": 1.0}],
                "ys": [1.0, 1.0],
                "dates": ["2025-06-02", "2025-06-02"],
                "metas": [
                    {
                        "date": "2025-06-02",
                        "stock_code": "600001",
                        "gap_pct": 1.0,
                        "tau": "10:30",
                        "ret_last_60m": 3.0,
                    },
                    {
                        "date": "2025-06-02",
                        "stock_code": "600001",
                        "gap_pct": 1.0,
                        "tau": "11:00",
                        "ret_last_60m": 4.0,
                    },
                ],
            },
        ]
        out = attach_cross_section_breadth(panels)
        self.assertAlmostEqual(out[0]["xs"][0]["sector_ret_last_60m"], 2.0, places=6)
        self.assertAlmostEqual(out[0]["xs"][0]["ret_last_60m_vs_sector"], -1.0, places=6)
        self.assertAlmostEqual(out[1]["xs"][0]["ret_last_60m_vs_sector"], 1.0, places=6)
        self.assertAlmostEqual(out[0]["xs"][1]["sector_ret_last_60m"], 3.0, places=6)

    def test_t60_grid_stops_at_1355(self):
        from core.signal.minute_tau_grid import DEFAULT_T60_TRAIN_TAU_GRID

        self.assertIn("09:30", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertIn("11:30", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertIn("13:05", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertIn("13:55", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertNotIn("13:00", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertNotIn("14:00", DEFAULT_T60_TRAIN_TAU_GRID)
        self.assertNotIn("14:05", DEFAULT_T60_TRAIN_TAU_GRID)


class RelabelT60Tests(unittest.TestCase):
    def test_y_t60_pct_geometry(self):
        from core.research.tau_panel import y_t60_pct

        self.assertAlmostEqual(y_t60_pct(10.0, 10.5), 5.0, places=6)
        self.assertIsNone(y_t60_pct(0, 10.5))
        self.assertIsNone(y_t60_pct(10.0, None))

    def test_t60_mean_session_three_bars(self):
        from core.research.tau_panel import price_at_t60_mean_session, y_t60_pct

        dkey = "2025-06-02"
        bars = []
        for hm in _times_am_pm():
            if hm == "10:25":
                c = 10.1
            elif hm == "10:30":
                c = 10.4
            elif hm == "10:35":
                c = 10.7
            else:
                c = 10.0
            bars.append(_bar(dkey, hm, c, open_px=10.0))
        hm, px = price_at_t60_mean_session(bars, trade_date=dkey, tau_hm="09:30")
        self.assertEqual(hm, "10:30")
        self.assertAlmostEqual(float(px), 10.4, places=6)
        self.assertAlmostEqual(y_t60_pct(10.0, px), 4.0, places=6)
        late_hm, late_px = price_at_t60_mean_session(
            bars, trade_date=dkey, tau_hm="14:00"
        )
        self.assertIsNone(late_hm)
        self.assertIsNone(late_px)
        ok_hm, ok_px = price_at_t60_mean_session(
            bars, trade_date=dkey, tau_hm="13:55"
        )
        self.assertEqual(ok_hm, "14:55")
        self.assertAlmostEqual(float(ok_px), 10.0, places=6)
        lunch_hm, lunch_px = price_at_t60_mean_session(
            bars, trade_date=dkey, tau_hm="11:00"
        )
        self.assertEqual(lunch_hm, "13:30")
        self.assertAlmostEqual(float(lunch_px), 10.0, places=6)

    def test_relabel_keeps_horizon_rows_and_drops_missing(self):
        from core.research.tau_panel import relabel_tau_panels_as_t60, y_t60_pct

        yt = y_t60_pct(10.0, 10.3)
        panel = {
            "xs": [{"gap_pct": 1.0}] * 5,
            "ys": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dates": [f"2025-06-0{i}" for i in range(2, 7)],
            "metas": [
                {"price_tau": 10.0, "price_tau60": 10.3, "tau": "09:35", "tau_plus_60": "10:35"},
                {"price_tau": 10.0, "price_tau60": 9.8, "tau": "11:00", "tau_plus_60": "13:30"},
                {"price_tau": 10.0, "price_tau60": None, "tau": "14:05"},
                {"price_tau": 10.1, "price_tau60": 10.2, "tau": "10:00", "tau_plus_60": "11:00"},
                {"price_tau": 10.2, "price_tau60": 10.4, "tau": "10:30", "tau_plus_60": "11:30"},
            ],
        }
        out = relabel_tau_panels_as_t60([panel])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["ys"]), 4)
        self.assertAlmostEqual(out[0]["ys"][0], yt, places=6)
        self.assertIn("y_τ60", out[0]["metas"][0])

    def test_relabel_attaches_same_clock_t60_lags(self):
        from core.research.tau_panel import relabel_tau_panels_as_t60

        dates = [f"2025-06-{i:02d}" for i in range(2, 8)]
        xs = [{"gap_pct": 1.0} for _ in dates]
        metas = [
            {
                "price_tau": 10.0,
                "price_tau60": 10.0 * (1.0 + (i + 1) * 0.01),
                "tau": "10:30",
            }
            for i in range(len(dates))
        ]
        out = relabel_tau_panels_as_t60(
            [{"xs": xs, "ys": [0.0] * len(dates), "dates": dates, "metas": metas}]
        )
        self.assertEqual(len(out), 1)
        last = out[0]["xs"][-1]
        self.assertAlmostEqual(float(last["t60_lag1"]), 5.0, places=5)
        self.assertAlmostEqual(float(last["t60_ma5"]), 3.0, places=5)
        self.assertIsNone(out[0]["xs"][0].get("t60_lag1"))

    def test_collect_stamps_lunch_crossing_price(self):
        from core.research.tau_panel import collect_tau_intraday_panel

        d0 = date(2025, 6, 2)
        daily = []
        minutes = []
        px = 10.0
        times = _times_am_pm()
        for i in range(18):
            day = d0 + timedelta(days=i)
            if day.weekday() >= 5:
                continue
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + j * 0.01
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
                }
            )
            px = close
        xs, ys, dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_grid=["11:00", "14:05"],
            min_history=5,
            stock_code="600000",
        )
        lunch = [m for m in metas if m.get("tau") == "11:00"]
        late = [m for m in metas if m.get("tau") == "14:05"]
        self.assertTrue(lunch)
        self.assertEqual(lunch[0].get("tau_plus_60"), "13:30")
        self.assertIsNotNone(lunch[0].get("price_tau60"))
        self.assertEqual(lunch[0].get("crosses_lunch_60"), 1.0)
        self.assertEqual(lunch[0].get("session_elapsed"), 90.0)
        lunch_i = next(i for i, m in enumerate(metas) if m.get("tau") == "11:00")
        self.assertNotIn("ret_last_60m", xs[lunch_i])
        self.assertNotIn("crosses_lunch_60", xs[lunch_i])
        self.assertTrue(late)
        self.assertIsNone(late[0].get("tau_plus_60"))
        self.assertIsNone(late[0].get("price_tau60"))

    def test_collect_day_index_keeps_t60_grid_fast(self):
        """按日切条后，全日 τ 网格不得再扫整段分钟历史。"""
        import time

        from core.research.tau_panel import collect_tau_intraday_panel
        from core.signal.minute_tau_grid import DEFAULT_T60_TRAIN_TAU_GRID

        d0 = date(2024, 1, 2)
        daily = []
        minutes = []
        px = 10.0
        times = _times_am_pm()
        for i in range(90):
            day = d0 + timedelta(days=i)
            if day.weekday() >= 5:
                continue
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + j * 0.01
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
                }
            )
            px = close
        t0 = time.perf_counter()
        xs, ys, _dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_grid=list(DEFAULT_T60_TRAIN_TAU_GRID),
            min_history=5,
            stock_code="600000",
        )
        elapsed = time.perf_counter() - t0
        self.assertGreater(len(ys), 100)
        self.assertTrue(any(m.get("tau_plus_60") == "13:30" for m in metas))
        self.assertLess(elapsed, 6.0, msg=f"collect too slow: {elapsed:.2f}s n={len(ys)}")


class T60RidgeFitTests(unittest.TestCase):
    def test_fit_report_runs_on_synthetic(self):
        from core.research.t60_ridge import T60_Z_FEATURES, fit_t60_ridge_report
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
        report = fit_t60_ridge_report(
            [{"code": "600000", "bars": daily, "minute_bars": minutes}],
            ridge_lambda=1.0,
            tau_grid=["09:30", "09:50", "11:00"],
            holdout_trading_days=5,
        )
        self.assertTrue(report.get("success"), msg=report)
        self.assertEqual(report.get("schema"), "t60_ridge_v2")
        self.assertEqual(report.get("dual_score_head"), "y_t60")
        self.assertEqual(report.get("target"), "price_tau_plus_60")
        y_spec = (report.get("return_model") or {}).get("y_spec") or {}
        self.assertIn("60", str(y_spec.get("formula") or ""))
        extras = (report.get("return_model") or {}).get("extra_features") or []
        self.assertEqual(list(extras), list(T60_Z_FEATURES))
        for k in (
            "ret_last_5m",
            "ret_last_60m",
            "session_elapsed",
            "session_remain",
            "crosses_lunch_60",
            "session_vwap_dev",
            "vol_last_60m_vs_avg",
            "sector_ret_last_60m",
            "ret_last_60m_vs_sector",
            "t60_lag1",
            "t60_ma5",
        ):
            self.assertIn(k, extras)
            self.assertNotIn(k, TAU_Z_FEATURES)
        self.assertNotIn("crosses_lunch", extras)
        for k in ("bounce_from_low", "pullback_from_high", "range_pct"):
            self.assertNotIn(k, extras)
        self.assertIn("ret_open_to_tau", extras)

    def test_explain_t60_prediction_head(self):
        from core.research.t60_ridge import explain_t60_prediction

        model = {
            "model_role": "research",
            "return_model": {
                "coefficients": {"gap_pct": 0.5},
                "intercept": 0.1,
                "zscore_means": {"gap_pct": 0.0},
                "zscore_stds": {"gap_pct": 1.0},
                "active_features": ["gap_pct"],
            },
        }
        expl = explain_t60_prediction({"gap_pct": 2.0}, model_doc=model)
        self.assertIsNotNone(expl)
        self.assertEqual(expl.get("head"), "t60")
        self.assertAlmostEqual(float(expl.get("total")), 1.1, places=5)


class T60GateTests(unittest.TestCase):
    def test_t60_enter_keys_dropped(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({})
        self.assertNotIn("y_t60_enter", cfg)
        self.assertNotIn("y_t60_enter_alt", cfg)
        self.assertNotIn("y_t60_strong", cfg)

    def test_scores_from_item_passes_y_t60(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item({"y_tau_oc": 1.2, "y_τ60": 0.4, "predicted_score_t60": 0.4})
        self.assertAlmostEqual(sc.get("y_τ60"), 0.4)
        self.assertAlmostEqual(sc.get("y_t60"), 0.4)

    def test_score_portrait_t60_hits(self):
        from core.t0.viz import build_score_portrait, build_score_portrait_by_slot

        days = [
            {
                "date": "2026-03-01",
                "open": 10.0,
                "close": 10.2,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": 1.8,
                        "y_τ60": 0.7,
                        "y_t60_realized": 0.40,
                        "pick": "buy_then_sell",
                    }
                ],
            },
            {
                "date": "2026-03-02",
                "open": 10.0,
                "close": 9.8,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": -1.0,
                        "y_τ60": 0.3,
                        "y_t60_realized": 0.50,
                        "pick": "buy_then_sell",
                    }
                ],
            },
        ]
        port = build_score_portrait(days)
        self.assertEqual(port["y_t60_hit"]["hit"], 1)
        self.assertEqual(port["y_t60_hit"]["miss"], 1)
        self.assertEqual(port["y_t60_band"]["hit"], 1)
        self.assertEqual(port["y_t60_band"]["miss"], 1)
        slots = {
            s["hm"]: s for s in (build_score_portrait_by_slot(days).get("slots") or [])
        }
        s = slots["09:35"]
        self.assertEqual(s["y_t60_hit"]["hit"], 1)
        self.assertEqual(s["y_t60_hit"]["miss"], 1)
        self.assertEqual(s["y_t60_band"]["hit"], 1)
        self.assertEqual(s["y_t60_band"]["miss"], 1)

    def test_scan_row_carries_y_t60_realized(self):
        from core.t0.config import load_t0_rules
        from core.t0.slots import _build_close_band_scan_trace

        dkey = "2025-06-03"
        mins = []
        for hm in _times_am_pm():
            px = 10.5 if hm >= "10:25" else 10.0
            mins.append(_bar(dkey, hm, px))
        bar = {
            "date": dkey,
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "prev_close": 10.0,
        }
        rows = _build_close_band_scan_trace(
            minute_bars=mins,
            bar=bar,
            daily_bar=bar,
            cfg=load_t0_rules({}),
            score_snap={"y_tau": 0.0, "y_tau_oc": 0.0, "y_τ60": 1.2},
            stock_code="",
            hist_bars=[],
            tau_pool_day=None,
        )
        row930 = next((r for r in rows if str(r.get("hm") or "")[:5] == "09:30"), None)
        self.assertIsNotNone(row930, rows[:3] if rows else rows)
        self.assertAlmostEqual(float(row930.get("y_t60_realized")), 5.0, places=3)
        self.assertAlmostEqual(float(row930.get("t60_realized")), 5.0, places=3)
        self.assertAlmostEqual(float(row930.get("y_τ60")), 1.2, places=3)

    def test_start_t60_ridge_job_returns_background(self):
        import time

        from core.job_progress import t60_ridge_job
        from quant.services.quant_service_factors import QuantFactorMixin

        if t60_ridge_job.is_running():
            t60_ridge_job.force_fail("test reset")
        svc = QuantFactorMixin()
        started = {"n": 0}

        def _fake(**_kwargs):
            started["n"] += 1
            time.sleep(0.25)
            return {"success": True, "return_model": {"intercept": 0.0}}

        svc.run_t60_ridge_experiment = _fake
        t0 = time.time()
        out = svc.start_t60_ridge_job(watching_limit=2)
        self.assertLess(time.time() - t0, 0.2)
        self.assertTrue(out.get("background"))
        self.assertTrue(out.get("success"))
        job_id = (out.get("job") or {}).get("id")
        self.assertTrue(job_id)
        deadline = time.time() + 4
        while time.time() < deadline and t60_ridge_job.is_running():
            time.sleep(0.05)
        snap = t60_ridge_job.get()
        self.assertEqual(snap.get("status"), "done")
        self.assertEqual(started["n"], 1)
        self.assertEqual(snap.get("id"), job_id)
        self.assertTrue((snap.get("result") or {}).get("success"))


if __name__ == "__main__":
    unittest.main()
