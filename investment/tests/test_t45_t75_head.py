"""ŷ_τ45 / ŷ_τ75：交易时钟、序列特征、五头 ŷ_τw 符号和。"""

from __future__ import annotations

import os
import sys
import unittest

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


class SessionClockT45T75Tests(unittest.TestCase):
    def test_add_session_minutes_45_and_75(self):
        from core.signal.minute_tau_grid import (
            add_session_minutes,
            tau_clock_allows_t45,
            tau_clock_allows_t75,
        )

        self.assertEqual(add_session_minutes("09:30", 45), "10:15")
        self.assertEqual(add_session_minutes("11:00", 45), "13:15")
        self.assertEqual(add_session_minutes("14:15", 45), "15:00")
        self.assertEqual(add_session_minutes("14:10", 50), "15:00")
        self.assertIsNone(add_session_minutes("14:20", 45))
        self.assertIsNone(add_session_minutes("14:15", 50))
        self.assertTrue(tau_clock_allows_t45("14:10"))
        self.assertFalse(tau_clock_allows_t45("14:15"))
        self.assertFalse(tau_clock_allows_t45("14:20"))

        self.assertEqual(add_session_minutes("09:30", 75), "10:45")
        self.assertEqual(add_session_minutes("11:00", 75), "13:45")
        self.assertEqual(add_session_minutes("13:45", 75), "15:00")
        self.assertEqual(add_session_minutes("13:40", 80), "15:00")
        self.assertIsNone(add_session_minutes("13:50", 75))
        self.assertIsNone(add_session_minutes("13:45", 80))
        self.assertTrue(tau_clock_allows_t75("13:40"))
        self.assertFalse(tau_clock_allows_t75("13:45"))
        self.assertFalse(tau_clock_allows_t75("13:50"))

    def test_extract_t45_seq_pack_trail(self):
        from core.signal.minute_tau_feats import extract_t45_seq_pack

        dkey = "2025-06-02"
        bars = []
        px = 10.0
        for total in range(9 * 60 + 30, 10 * 60 + 15 + 1, 5):
            hm = f"{total // 60:02d}:{total % 60:02d}"
            c = px + (total - (9 * 60 + 30)) * 0.01
            bars.append(_bar(dkey, hm, c, open_px=px))
        pack = extract_t45_seq_pack(
            bars, trade_date=dkey, tau_hm="10:15", open_px=px
        )
        self.assertIn("ret_last_45m", pack)
        self.assertIn("crosses_lunch_45", pack)
        self.assertEqual(pack.get("crosses_lunch_45"), 0.0)

    def test_relabel_t45_uses_horizon_price(self):
        from core.research.tau_panel import relabel_tau_panels_as_t45

        panel = {
            "xs": [{"gap_pct": 1.0}] * 4,
            "dates": [f"2025-06-0{i}" for i in range(1, 5)],
            "metas": [
                {"price_tau": 10.0, "price_tau45": 10.2, "tau": "10:00"}
                for _ in range(4)
            ],
        }
        out = relabel_tau_panels_as_t45([panel])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["ys"][0], 2.0)
        self.assertAlmostEqual(out[0]["metas"][0]["y_τ45"], 2.0)

    def test_t45_t75_grids_stop_before_need_min(self):
        from core.signal.minute_tau_grid import (
            DEFAULT_T45_TRAIN_TAU_GRID,
            DEFAULT_T75_TRAIN_TAU_GRID,
        )

        self.assertIn("14:10", DEFAULT_T45_TRAIN_TAU_GRID)
        self.assertNotIn("14:15", DEFAULT_T45_TRAIN_TAU_GRID)
        self.assertIn("13:40", DEFAULT_T75_TRAIN_TAU_GRID)
        self.assertNotIn("13:45", DEFAULT_T75_TRAIN_TAU_GRID)

    def test_t45_t75_mean_session_three_bars(self):
        from core.research.tau_panel import (
            price_at_t45_mean_session,
            price_at_t75_mean_session,
            y_t45_pct,
            y_t75_pct,
        )

        dkey = "2025-06-02"
        bars = []
        for total in list(range(9 * 60 + 30, 11 * 60 + 30 + 1, 5)) + list(
            range(13 * 60, 15 * 60 + 1, 5)
        ):
            hm = f"{total // 60:02d}:{total % 60:02d}"
            if hm in ("10:10", "10:40"):
                c = 10.1
            elif hm in ("10:15", "10:45"):
                c = 10.4
            elif hm in ("10:20", "10:50"):
                c = 10.7
            else:
                c = 10.0
            bars.append(_bar(dkey, hm, c, open_px=10.0))
        hm45, px45 = price_at_t45_mean_session(bars, trade_date=dkey, tau_hm="09:30")
        self.assertEqual(hm45, "10:15")
        self.assertAlmostEqual(float(px45), 10.4, places=6)
        self.assertAlmostEqual(y_t45_pct(10.0, px45), 4.0, places=6)
        late45_hm, late45_px = price_at_t45_mean_session(
            bars, trade_date=dkey, tau_hm="14:15"
        )
        self.assertIsNone(late45_hm)
        self.assertIsNone(late45_px)
        ok45_hm, ok45_px = price_at_t45_mean_session(
            bars, trade_date=dkey, tau_hm="14:10"
        )
        self.assertEqual(ok45_hm, "14:55")
        self.assertAlmostEqual(float(ok45_px), 10.0, places=6)
        lunch45_hm, lunch45_px = price_at_t45_mean_session(
            bars, trade_date=dkey, tau_hm="11:15"
        )
        self.assertEqual(lunch45_hm, "13:30")
        self.assertAlmostEqual(float(lunch45_px), 10.0, places=6)

        hm75, px75 = price_at_t75_mean_session(bars, trade_date=dkey, tau_hm="09:30")
        self.assertEqual(hm75, "10:45")
        self.assertAlmostEqual(float(px75), 10.4, places=6)
        self.assertAlmostEqual(y_t75_pct(10.0, px75), 4.0, places=6)
        late75_hm, late75_px = price_at_t75_mean_session(
            bars, trade_date=dkey, tau_hm="13:45"
        )
        self.assertIsNone(late75_hm)
        self.assertIsNone(late75_px)
        ok75_hm, ok75_px = price_at_t75_mean_session(
            bars, trade_date=dkey, tau_hm="13:40"
        )
        self.assertEqual(ok75_hm, "14:55")
        lunch75_hm, lunch75_px = price_at_t75_mean_session(
            bars, trade_date=dkey, tau_hm="11:00"
        )
        self.assertEqual(lunch75_hm, "13:45")
        self.assertAlmostEqual(float(lunch75_px), 10.0, places=6)


class YtwFiveHeadTests(unittest.TestCase):
    def test_blend_five_heads_and_defaults(self):
        from core.t0.close_band import blend_y_tw, close_band_y_tw_skip_reason
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({})
        self.assertEqual(float(cfg["y_tw_strong"]), 5.0)
        self.assertAlmostEqual(float(cfg["y_tw_vote_margin"]), 2.0)
        self.assertNotIn("y_t45_strong", cfg)
        self.assertNotIn("y_t75_strong", cfg)
        # 旧落盘 3=关闸 → 迁到 5
        migrated = load_t0_rules({"y_tw_strong": 3.0})
        self.assertEqual(float(migrated["y_tw_strong"]), 5.0)

        self.assertAlmostEqual(blend_y_tw(0.8, 0.6, 0.55), 3.0)
        self.assertAlmostEqual(blend_y_tw(0.8, 0.6, 0.55, 0.7, 0.65), 5.0)
        self.assertAlmostEqual(blend_y_tw(0.8, 0.2, 0.3, 0.7, 0.2), -1.0)
        self.assertAlmostEqual(blend_y_tw(0.8, 0.51, 0.49, 0.52, 0.48), 1.0)
        self.assertIsNone(blend_y_tw(None, None, None, None, None))

        scores = {
            "y_τ30": 0.8,
            "y_τ45": 0.6,
            "y_τ60": 0.55,
            "y_τ75": 0.7,
            "y_τ90": 0.65,
        }
        self.assertIsNone(
            close_band_y_tw_skip_reason(
                scores, {"y_tw_strong": 5}, direction="sell_then_buy"
            )
        )
        skip = close_band_y_tw_skip_reason(
            scores, {"y_tw_strong": 0}, direction="sell_then_buy"
        )
        self.assertIsNotNone(skip)
        self.assertIn("ŷ_τw=+5", skip)


class T45T75GateTests(unittest.TestCase):
    def test_enter_skip_y_t45_t75(self):
        from core.t0.close_band import close_band_enter_skip_reason
        from core.t0.config import load_t0_rules
        from core.t0.viz import classify_t0_skip_reason

        self.assertNotIn("y_t45_enter", load_t0_rules({}))
        self.assertNotIn("y_t75_enter", load_t0_rules({}))
        self.assertNotIn("y_t45_strong", load_t0_rules({}))
        self.assertNotIn("y_t75_strong", load_t0_rules({}))

        cfg45 = {
            "y_tau_enter": 0.0,
            "y_path_enter": 0.0,
            "y_t45_enter": 0.5,
        }
        weak45 = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0, "y_τ45": 0.2}, cfg45, direction="buy_then_sell"
        )
        self.assertIsNotNone(weak45)
        self.assertEqual(classify_t0_skip_reason(weak45), "y_t45_flat")
        self.assertIsNone(
            close_band_enter_skip_reason(
                {"y_tau": 1.0, "y_path": 1.0, "y_τ45": 0.8}, cfg45, direction="buy_then_sell"
            )
        )
        self.assertIsNone(
            close_band_enter_skip_reason({"y_tau": 1.0, "y_path": 1.0}, cfg45, direction="buy_then_sell")
        )
        self.assertIsNone(
            close_band_enter_skip_reason(
                {"y_tau": 1.0, "y_path": 1.0, "y_τ45": 0.2},
                {**cfg45, "y_t45_enter": 0.9, "y_t45_enter_alt": 0.1},
                direction="buy_then_sell",
            )
        )

        cfg75 = {
            "y_tau_enter": 0.0,
            "y_path_enter": 0.0,
            "y_t75_enter": 0.5,
        }
        weak75 = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0, "y_τ75": 0.2}, cfg75, direction="buy_then_sell"
        )
        self.assertIsNotNone(weak75)
        self.assertEqual(classify_t0_skip_reason(weak75), "y_t75_flat")

    def test_scan_row_carries_y_t45_t75_realized(self):
        from core.t0.config import load_t0_rules
        from core.t0.slots import _build_close_band_scan_trace

        dkey = "2025-06-03"
        mins = []
        for total in list(range(9 * 60 + 30, 11 * 60 + 30 + 1, 5)) + list(
            range(13 * 60, 15 * 60 + 1, 5)
        ):
            hm = f"{total // 60:02d}:{total % 60:02d}"
            px = 10.5 if hm >= "10:10" else 10.0
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
            score_snap={
                "y_tau": 0.0,
                "y_tau_oc": 0.0,
                "y_τ45": 1.2,
                "y_τ75": 0.8,
            },
            stock_code="",
            hist_bars=[],
            tau_pool_day=None,
        )
        row930 = next((r for r in rows if str(r.get("hm") or "")[:5] == "09:30"), None)
        self.assertIsNotNone(row930, rows[:3] if rows else rows)
        self.assertAlmostEqual(float(row930.get("y_t45_realized")), 5.0, places=3)
        self.assertAlmostEqual(float(row930.get("t45_realized")), 5.0, places=3)
        self.assertAlmostEqual(float(row930.get("y_t75_realized")), 5.0, places=3)
        self.assertAlmostEqual(float(row930.get("t75_realized")), 5.0, places=3)


if __name__ == "__main__":
    unittest.main()
