"""分钟 τ 小包特征单测。"""

from __future__ import annotations

import unittest

from core.signal.minute_tau_feats import (
    MINUTE_TAU_PACK_KEYS,
    attach_ret_vs_sector,
    extract_minute_tau_pack,
)


def _bars(day: str = "2026-08-28"):
    # 先冲高再回落再抬：high 在 09:40，low 在 09:50 → path_sign=-1
    rows = [
        ("09:35", 10.0, 10.2, 9.95, 10.1, 1000),
        ("09:40", 10.1, 10.5, 10.05, 10.4, 1200),
        ("09:45", 10.4, 10.45, 10.2, 10.25, 800),
        ("09:50", 10.25, 10.3, 9.9, 10.0, 1500),
        ("09:55", 10.0, 10.15, 9.95, 10.1, 900),
    ]
    out = []
    for hm, o, h, l, c, v in rows:
        out.append(
            {
                "date": day,
                "datetime": f"{day} {hm}:00",
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
    return out


class TestMinuteTauPack(unittest.TestCase):
    def test_extract_small_pack_core_keys(self):
        pack = extract_minute_tau_pack(
            _bars(),
            trade_date="2026-08-28",
            tau_hm="09:55",
            open_px=10.0,
            prev_close=9.8,
        )
        for k in (
            "ret_open_to_tau",
            "ret_prev_to_tau",
            "range_pct",
            "loc_hl",
            "up_extent",
            "down_extent",
            "path_sign",
            "pullback_from_high",
            "bounce_from_low",
            "ret_last_15m",
            "realized_vol",
            "vol_last3_vs_avg",
        ):
            self.assertIn(k, pack, k)
        self.assertAlmostEqual(pack["ret_open_to_tau"], 1.0, places=4)  # 10.1/10-1
        self.assertAlmostEqual(pack["ret_prev_to_tau"], (10.1 / 9.8 - 1) * 100, places=4)
        self.assertEqual(pack["path_sign"], -1.0)
        self.assertGreater(pack["range_pct"], 0)
        self.assertTrue(0.0 <= pack["loc_hl"] <= 1.0)

    def test_tau_cutoff_excludes_later_bars(self):
        pack_early = extract_minute_tau_pack(
            _bars(),
            trade_date="2026-08-28",
            tau_hm="09:40",
            open_px=10.0,
        )
        pack_late = extract_minute_tau_pack(
            _bars(),
            trade_date="2026-08-28",
            tau_hm="09:55",
            open_px=10.0,
        )
        self.assertAlmostEqual(pack_early["ret_open_to_tau"], 4.0, places=4)  # 10.4
        self.assertLess(pack_late["ret_open_to_tau"], pack_early["ret_open_to_tau"])

    def test_need_two_bars(self):
        one = [
            {
                "date": "2026-08-28",
                "datetime": "2026-08-28 09:35:00",
                "open": 10,
                "high": 10.1,
                "low": 9.9,
                "close": 10,
                "volume": 1,
            }
        ]
        self.assertEqual(
            extract_minute_tau_pack(one, trade_date="2026-08-28", open_px=10.0),
            {},
        )

    def test_attach_ret_vs_sector(self):
        feats = {"ret_open_to_tau": 2.0, "sector_ret_to_tau": 0.5}
        attach_ret_vs_sector(feats)
        self.assertAlmostEqual(feats["ret_vs_sector"], 1.5, places=6)

    def test_pack_keys_constant(self):
        self.assertEqual(len(MINUTE_TAU_PACK_KEYS), 13)

    def test_variable_prefix_grid_rows(self):
        """少数时钟变长前缀：同日多行，非整根独立标签。"""
        from core.research.tau_panel import collect_tau_intraday_panel

        day = "2026-08-28"
        daily = []
        for d in [
            "2026-08-10",
            "2026-08-11",
            "2026-08-12",
            "2026-08-13",
            "2026-08-14",
            "2026-08-17",
            "2026-08-18",
            "2026-08-19",
            "2026-08-20",
            "2026-08-21",
            "2026-08-24",
            "2026-08-25",
            "2026-08-26",
            "2026-08-27",
            day,
        ]:
            daily.append(
                {"date": d, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0}
            )
        minutes = []
        # 09:35 … 10:30 = 12 根
        for i, hm in enumerate(
            [
                "09:35",
                "09:40",
                "09:45",
                "09:50",
                "09:55",
                "10:00",
                "10:05",
                "10:10",
                "10:15",
                "10:20",
                "10:25",
                "10:30",
                "14:55",
            ]
        ):
            px = 10.0 + i * 0.02
            minutes.append(
                {
                    "date": day,
                    "datetime": f"{day} {hm}:00",
                    "open": px,
                    "high": px + 0.05,
                    "low": px - 0.05,
                    "close": px,
                    "volume": 100,
                }
            )
        _xs, ys, _dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_hm="10:30",
            tau_grid=["09:45", "10:00", "10:15", "10:30"],
            min_history=5,
        )
        taus = [m.get("tau") for m in metas if m.get("date") == day]
        self.assertEqual(taus, ["09:45", "10:00", "10:15", "10:30"])
        self.assertEqual(len(ys), 4)
        # 同日标签统一 open→close，多 τ 行 y 相同；特征（前缀）不同
        self.assertTrue(all(abs(y - ys[0]) < 1e-9 for y in ys))
        self.assertNotEqual(_xs[0].get("ret_open_to_tau"), _xs[-1].get("ret_open_to_tau"))
        self.assertIn("tau_elapsed_min", _xs[0])
        self.assertEqual(_xs[-1]["tau_elapsed_min"], 60.0)

    def test_intraday_y_uses_daily_open_close(self):
        """标签用日线 open→close；分钟只供特征（可与日线复权错位）。"""
        from core.research.tau_panel import collect_tau_intraday_panel

        day = "2026-08-28"
        daily = []
        for i, d in enumerate(
            [
                "2026-08-10",
                "2026-08-11",
                "2026-08-12",
                "2026-08-13",
                "2026-08-14",
                "2026-08-17",
                "2026-08-18",
                "2026-08-19",
                "2026-08-20",
                "2026-08-21",
                "2026-08-24",
                "2026-08-25",
                "2026-08-26",
                "2026-08-27",
                day,
            ]
        ):
            daily.append(
                {
                    "date": d,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0 if d != day else 15.0,
                }
            )
        minutes = _bars(day)
        minutes.append(
            {
                "date": day,
                "datetime": f"{day} 14:55:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.12,
                "volume": 500,
            }
        )
        _xs, ys, _dates, metas = collect_tau_intraday_panel(
            daily, minutes, tau_hm="09:45", min_history=5
        )
        self.assertTrue(ys)
        self.assertAlmostEqual(ys[-1], (15.0 / 10.0 - 1.0) * 100.0, places=4)
        self.assertNotAlmostEqual(ys[-1], (10.12 / 10.0 - 1.0) * 100.0, places=2)
        self.assertEqual(metas[-1].get("close"), 15.0)
        self.assertEqual(metas[-1].get("open"), 10.0)
        self.assertEqual(metas[-1].get("close_minute"), 10.12)


if __name__ == "__main__":
    unittest.main()
