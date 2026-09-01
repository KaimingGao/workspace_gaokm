"""分钟 τ 小包特征单测。"""

from __future__ import annotations

import unittest

from core.signal.minute_tau_feats import (
    MINUTE_TAU_PACK_KEYS,
    apply_sector_ret_cs,
    attach_ret_vs_sector,
    clear_sector_ret_cache,
    extract_minute_tau_pack,
    sector_ret_median,
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

    def test_apply_sector_ret_cs_and_median(self):
        self.assertAlmostEqual(sector_ret_median([1.0, 3.0, 2.0]), 2.0, places=6)
        self.assertAlmostEqual(sector_ret_median([1.0, 2.0]), 1.5, places=6)
        self.assertIsNone(sector_ret_median([]))
        feats = apply_sector_ret_cs({"ret_open_to_tau": 2.0}, 0.5)
        self.assertAlmostEqual(feats["sector_ret_to_tau"], 0.5, places=6)
        self.assertAlmostEqual(feats["ret_vs_sector"], 1.5, places=6)
        # 已有键不覆盖
        feats2 = apply_sector_ret_cs(
            {"ret_open_to_tau": 2.0, "sector_ret_to_tau": 0.8}, 0.1
        )
        self.assertAlmostEqual(feats2["sector_ret_to_tau"], 0.8, places=6)
        self.assertAlmostEqual(feats2["ret_vs_sector"], 1.2, places=6)

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
        # 09:35 … 11:30 覆盖训练网格 + 做T四轮前缀（末档 11:30）
        hms = []
        for hm in (
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
            "10:35",
            "10:40",
            "10:45",
            "10:50",
            "10:55",
            "11:00",
            "11:05",
            "11:10",
            "11:15",
            "11:20",
            "11:25",
            "11:30",
            "14:55",
        ):
            hms.append(hm)
        for i, hm in enumerate(hms):
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
        from core.research.tau_panel import DEFAULT_MINUTE_TAU_GRID

        _xs, ys, _dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_hm="10:30",
            tau_grid=list(DEFAULT_MINUTE_TAU_GRID),
            min_history=5,
        )
        taus = [m.get("tau") for m in metas if m.get("date") == day]
        self.assertEqual(taus, list(DEFAULT_MINUTE_TAU_GRID))
        self.assertEqual(len(ys), 5)
        # 同日标签统一 open→close，多 τ 行 y 相同；特征（前缀）不同
        self.assertTrue(all(abs(y - ys[0]) < 1e-9 for y in ys))
        self.assertEqual(_xs[0]["tau_elapsed_min"], 0.0)
        self.assertEqual(_xs[-1]["tau_elapsed_min"], 120.0)
        self.assertNotEqual(_xs[1].get("ret_open_to_tau"), _xs[-1].get("ret_open_to_tau"))
        self.assertIn("tau_elapsed_min", _xs[0])

    def test_default_grid_covers_t0_slots(self):
        from core.research.tau_panel import DEFAULT_MINUTE_TAU_GRID
        from core.t0.config import DEFAULT_T0_SLOT_CLOCKS

        self.assertEqual(tuple(DEFAULT_T0_SLOT_CLOCKS), ("10:00", "10:30", "11:00", "11:30"))
        self.assertTrue(set(DEFAULT_T0_SLOT_CLOCKS).issubset(DEFAULT_MINUTE_TAU_GRID))
        self.assertIn("09:30", DEFAULT_MINUTE_TAU_GRID)

    def test_open_clock_keeps_open_z_without_minute_pack(self):
        """09:30 无 ≤τ 分钟根：仍留开盘 Z 行（训练网格含开盘档，不做T）。"""
        from core.research.tau_panel import collect_tau_intraday_panel

        day = "2026-08-28"
        daily = [
            {"date": f"2026-08-{10 + i:02d}", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0}
            for i in range(6)
        ]
        daily[-1]["date"] = day
        minutes = _bars(day)
        xs, ys, dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_grid=["09:30"],
            min_history=5,
        )
        self.assertEqual(dates, [day])
        self.assertEqual([m.get("tau") for m in metas], ["09:30"])
        self.assertEqual(len(ys), 1)
        self.assertEqual(xs[0].get("tau_elapsed_min"), 0.0)
        self.assertNotIn("range_pct", xs[0])

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


    def test_merge_minute_tau_pack_into_feats(self):
        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        feats, as_of, y_spec = merge_minute_tau_pack_into_feats(
            {"gap_pct": 0.5},
            trade_date=day,
            open_px=10.0,
            prev_close=9.9,
            tau_hm="10:30",
            minute_bars=_bars(day)
            + [
                {
                    "date": day,
                    "datetime": f"{day} 10:30:00",
                    "open": 10.1,
                    "high": 10.2,
                    "low": 10.0,
                    "close": 10.15,
                    "volume": 1000,
                }
            ],
            load_cache_if_missing=False,
        )
        self.assertIsNotNone(feats.get("ret_open_to_tau"))
        self.assertIsNotNone(feats.get("range_pct"))
        self.assertEqual(as_of, f"{day}T10:30:00+08:00")
        self.assertEqual((y_spec or {}).get("tau"), "10:30")
        # 已有键不覆盖
        feats2, _, _ = merge_minute_tau_pack_into_feats(
            {"ret_open_to_tau": 9.9},
            trade_date=day,
            open_px=10.0,
            minute_bars=_bars(day),
            load_cache_if_missing=False,
        )
        self.assertAlmostEqual(feats2["ret_open_to_tau"], 9.9, places=4)

    def test_attach_dual_score_pit_uses_minute_bars(self):
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        day = "2026-08-28"
        item = {"stock_code": "600000", "predicted_score_eod": 0.5}
        quote = {"date": day, "open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": "2026-08-27", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
            {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
        ]
        mins = _bars(day) + [
            {
                "date": day,
                "datetime": f"{day} 10:30:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.15,
                "volume": 1000,
            }
        ]
        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
                "minute_tau_hm": "10:30",
                "tau": "open",
                "y_spec": {"formula": "close[T]/open[T]-1"},
            },
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=0.12
        ) as pred:
            attach_dual_score_pit(
                item,
                quote=quote,
                bars=bars,
                minute_bars=mins,
                fuse_intraday=True,
                sector_ret_to_tau=0.4,
            )
        self.assertIsNotNone(
            (item.get("features_tau") or {}).get("ret_open_to_tau"),
            item.get("features_tau"),
        )
        self.assertAlmostEqual(
            (item.get("features_tau") or {}).get("sector_ret_to_tau"), 0.4, places=5
        )
        self.assertIsNotNone((item.get("features_tau") or {}).get("ret_vs_sector"))
        # predict 收到含分钟小包的 feats
        call_feats = pred.call_args[0][0]
        self.assertIn("ret_open_to_tau", call_feats)
        self.assertNotEqual(call_feats.get("ret_open_to_tau"), 0)
        self.assertAlmostEqual(call_feats.get("sector_ret_to_tau"), 0.4, places=5)

    def test_attach_dual_score_pit_use_minute_tau_false_blocks_prefix(self):
        """做 T 选向：全日分钟不得写入 features_tau（禁前缀前瞻）。"""
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        day = "2026-08-28"
        item = {"stock_code": "600000", "predicted_score_eod": 0.5}
        quote = {"date": day, "open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": "2026-08-27", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
            {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
        ]
        mins = _bars(day) + [
            {
                "date": day,
                "datetime": f"{day} 10:30:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.15,
                "volume": 1000,
            }
        ]
        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
                "minute_tau_hm": "10:30",
                "tau": "open",
                "y_spec": {"formula": "close[T]/open[T]-1"},
            },
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=0.12
        ) as pred:
            attach_dual_score_pit(
                item,
                quote=quote,
                bars=bars,
                minute_bars=mins,
                fuse_intraday=True,
                sector_ret_to_tau=0.4,
                use_minute_tau=False,
            )
        feats = item.get("features_tau") or {}
        self.assertIsNone(feats.get("ret_open_to_tau"))
        self.assertIsNone(feats.get("sector_ret_to_tau"))
        self.assertIsNone(feats.get("ret_vs_sector"))
        call_feats = pred.call_args[0][0]
        self.assertIsNone(call_feats.get("ret_open_to_tau"))
        self.assertIsNone(call_feats.get("sector_ret_to_tau"))

    def test_attach_dual_score_pit_prefix_hm_blocks_1030_sector_fallback(self):
        """有前缀分钟时截面钟跟末根，不得回退配置 10:30。"""
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        day = "2026-08-28"
        item = {"stock_code": "600000", "predicted_score_eod": 0.5}
        quote = {"date": day, "open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": "2026-08-27", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
            {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
        ]
        # 仅到 10:00 的前缀
        mins = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": 10.0,
                "high": 10.1,
                "low": 9.95,
                "close": 10.05,
                "volume": 1000,
            },
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 10.05,
                "high": 10.2,
                "low": 10.0,
                "close": 10.15,
                "volume": 1000,
            },
        ]
        seen_hm = []

        def _fake_resolve(trade_date, tau_hm="10:30", **_kw):
            seen_hm.append(str(tau_hm))
            return None

        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
                "minute_tau_hm": "10:30",
                "tau": "open",
                "y_spec": {"formula": "close[T]/open[T]-1"},
            },
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=0.12
        ), patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_to_tau",
            side_effect=_fake_resolve,
        ):
            attach_dual_score_pit(
                item,
                quote=quote,
                bars=bars,
                minute_bars=mins,
                fuse_intraday=True,
                sector_ret_to_tau=None,
                use_minute_tau=True,
                minute_tau_hm="10:00",
            )
        self.assertTrue(seen_hm)
        self.assertTrue(all(h.startswith("10:00") for h in seen_hm), seen_hm)
        self.assertNotIn("10:30", seen_hm)


if __name__ == "__main__":
    unittest.main()
