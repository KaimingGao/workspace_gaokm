"""分钟 τ 小包特征单测。"""

from __future__ import annotations

import unittest

from core.signal.minute_tau_feats import (
    MINUTE_TAU_PACK_KEYS,
    apply_sector_ret_cs,
    attach_ret_vs_sector,
    attach_sector_ret_cs_if_missing,
    clear_minute_tau_pack_keys,
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
        self.assertAlmostEqual(pack["t_hi_frac"], 10.0 / 25.0, places=4)
        self.assertAlmostEqual(pack["t_lo_frac"], 20.0 / 25.0, places=4)

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

    def test_day_bars_upto_tau_indexes_long_history(self):
        from core.signal import minute_tau_feats as m

        day = "2026-08-28"
        bars = []
        for i in range(33):
            d = f"2026-07-{(i % 28) + 1:02d}"
            if i == 32:
                d = day
            for hm, c in (("09:35", 10.1), ("09:40", 10.4), ("10:30", 10.3)):
                bars.append(
                    {
                        "date": d,
                        "datetime": f"{d} {hm}:00",
                        "open": 10.0,
                        "high": c + 0.1,
                        "low": c - 0.1,
                        "close": c,
                        "volume": 1000,
                    }
                )
        self.assertGreater(len(bars), 96)
        indexed = m._day_bars_upto_tau(bars, trade_date=day, tau_hm="09:40")
        scanned = m._filter_day_bars_upto_tau(
            bars, day=day, target="0940", match_date=True
        )
        self.assertEqual(len(indexed), 2)
        self.assertEqual(
            [x["datetime"] for x in indexed],
            [x["datetime"] for x in scanned],
        )
        m.clear_sector_ret_cache()

    def test_one_bar_pack_ok_empty_prefix_not(self):
        """≥1 根即可出小包（09:35）；0 根仍空（09:30 无前缀）。"""
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
        pack = extract_minute_tau_pack(one, trade_date="2026-08-28", open_px=10.0)
        self.assertAlmostEqual(pack["ret_open_to_tau"], 0.0, places=6)
        self.assertAlmostEqual(pack["t_hi_frac"], 1.0, places=6)
        self.assertEqual(
            extract_minute_tau_pack([], trade_date="2026-08-28", open_px=10.0),
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

    def test_apply_sector_ret_last_30m_cs(self):
        from core.signal.minute_tau_feats import apply_sector_ret_last_30m_cs

        feats = apply_sector_ret_last_30m_cs({"ret_last_30m": 2.0}, 0.5)
        self.assertAlmostEqual(feats["sector_ret_last_30m"], 0.5, places=6)
        self.assertAlmostEqual(feats["ret_last_30m_vs_sector"], 1.5, places=6)
        kept = apply_sector_ret_last_30m_cs(
            {"ret_last_30m": 2.0, "sector_ret_last_30m": 0.8}, 0.1
        )
        self.assertAlmostEqual(kept["sector_ret_last_30m"], 0.8, places=6)
        overwritten = apply_sector_ret_last_30m_cs(
            {"ret_last_30m": 2.0, "sector_ret_last_30m": 0.8},
            0.1,
            overwrite=True,
        )
        self.assertAlmostEqual(overwritten["sector_ret_last_30m"], 0.1, places=6)
        self.assertAlmostEqual(overwritten["ret_last_30m_vs_sector"], 1.9, places=6)

    def test_attach_sector_ret_cs_if_missing_fills_serve_path(self):
        from unittest.mock import patch

        with patch(
            "core.signal.minute_tau_feats.resolve_sector_ret_to_tau",
            return_value=0.84,
        ):
            out = attach_sector_ret_cs_if_missing(
                {"ret_open_to_tau": -1.58},
                trade_date="2026-09-03",
                tau_hm="10:30",
            )
        self.assertAlmostEqual(out["sector_ret_to_tau"], 0.84, places=6)
        self.assertAlmostEqual(out["ret_vs_sector"], -2.42, places=5)

    def test_rebalance_cs_pin_survives_flush_after_1000(self):
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        m.clear_sector_ret_cache()
        key = ("2026-08-14", "10:00")
        m._SECTOR_RET_CACHE[key] = 1.25
        now = datetime(2026, 8, 14, 11, 0)
        with patch("core.signal.session_pit.shanghai_now", return_value=now), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-08-14"
        ):
            self.assertTrue(m._pin_rebalance_cs_key("2026-08-14", "10:00", 12))
            m._SECTOR_RET_PINNED.add(key)
            m._flush_sector_ret_medians()
        self.assertAlmostEqual(m._SECTOR_RET_CACHE[key], 1.25, places=6)
        m.clear_sector_ret_cache()

    def test_rebalance_cs_disk_first_write_wins(self):
        import os
        import tempfile
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path):
                m._write_rebalance_cs_disk("2026-09-18", "10:00", 0.39, n_rets=12)
                m._write_rebalance_cs_disk("2026-09-18", "10:00", 3.46, n_rets=80)
                self.assertAlmostEqual(
                    m._read_rebalance_cs_disk("2026-09-18", "10:00"), 0.39, places=4
                )
                self.assertAlmostEqual(
                    m._read_rebalance_cs_disk("2026-09-18", "1000"), 0.39, places=4
                )
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_rebalance_cs_after_1030_without_snapshot_uses_peers_pit(self):
        """10:30 后无落盘：按 τ=10:00 前缀从同伴重算，不得打成缺特征。"""
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_to_tau

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        now = datetime(2026, 9, 18, 15, 1)
        bars = [
            {
                "date": "2026-09-18",
                "datetime": "2026-09-18 09:35:00",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.05,
            },
            {
                "date": "2026-09-18",
                "datetime": "2026-09-18 10:00:00",
                "open": 10.05,
                "high": 10.4,
                "low": 10.0,
                "close": 10.35,
            },
        ]
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=now
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", return_value=["600000"]
            ), patch.object(
                m, "_peer_minute_bars_for_tau", return_value=bars
            ):
                got = resolve_sector_ret_to_tau("2026-09-18", "10:00")
                self.assertIsNotNone(got)
                self.assertGreater(float(got), 0.0)
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_rebalance_cs_after_1030_uses_disk_not_peers(self):
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_to_tau

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        now = datetime(2026, 9, 18, 15, 1)
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=now
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", side_effect=AssertionError("warehouse")
            ):
                m._write_rebalance_cs_disk("2026-09-18", "10:00", 0.39, n_rets=12)
                self.assertAlmostEqual(
                    resolve_sector_ret_to_tau("2026-09-18", "10:00"), 0.39, places=4
                )
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_pin_0940_at_0940_not_future_1000(self):
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        now = datetime(2026, 9, 18, 9, 40)
        with patch("core.signal.session_pit.shanghai_now", return_value=now), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
        ):
            self.assertTrue(m._pin_rebalance_cs_key("2026-09-18", "09:40", 12))
            self.assertFalse(m._pin_rebalance_cs_key("2026-09-18", "10:00", 12))

    def test_rebalance_cs_after_1030_0940_still_uses_peers(self):
        """09:40 不是 10:00 锁；下午重扫须能按 ≤τ 前缀重算板块中位。"""
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_to_tau

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        now = datetime(2026, 9, 18, 15, 1)
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=now
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", return_value=[]
            ) as peers:
                self.assertIsNone(resolve_sector_ret_to_tau("2026-09-18", "09:40"))
                peers.assert_called()
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_persist_0940_at_0940_survives_afternoon_lock(self):
        """09:40 早盘中位落盘后，下午清缓存仍能读回，不把板块项打成 z=0。"""
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_to_tau

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        morning = datetime(2026, 9, 18, 9, 40)
        afternoon = datetime(2026, 9, 18, 15, 1)
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=morning
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ):
                self.assertTrue(m._pin_rebalance_cs_key("2026-09-18", "09:40", 12))
                self.assertTrue(m._rebalance_cs_persist_window())
                m._write_rebalance_cs_disk("2026-09-18", "09:40", -0.685, n_rets=12)
            m.clear_sector_ret_cache()
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=afternoon
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", side_effect=AssertionError("warehouse")
            ):
                self.assertAlmostEqual(
                    resolve_sector_ret_to_tau("2026-09-18", "09:40"), -0.685, places=4
                )
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_horizon_cs_disk_shares_slot_with_oc(self):
        """开→τ 与 last_30m 同槽分字段；互不覆盖。"""
        import os
        import tempfile
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path):
                m._write_rebalance_cs_disk("2026-09-18", "09:40", -0.685, n_rets=12)
                m._write_rebalance_cs_disk(
                    "2026-09-18", "09:40", 0.12, n_rets=12, field="last_30m"
                )
                m._write_rebalance_cs_disk(
                    "2026-09-18", "09:40", 9.9, n_rets=80, field="last_30m"
                )
                m._write_rebalance_cs_disk("2026-09-18", "09:40", 3.46, n_rets=80)
                self.assertAlmostEqual(
                    m._read_rebalance_cs_disk("2026-09-18", "09:40"), -0.685, places=4
                )
                self.assertAlmostEqual(
                    m._read_rebalance_cs_disk(
                        "2026-09-18", "09:40", field="last_30m"
                    ),
                    0.12,
                    places=4,
                )
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_horizon_cs_after_1030_0940_still_uses_peers(self):
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_last_30m

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        now = datetime(2026, 9, 18, 15, 1)
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=now
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", return_value=[]
            ) as peers:
                self.assertIsNone(resolve_sector_ret_last_30m("2026-09-18", "09:40"))
                peers.assert_called()
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_horizon_cs_after_1030_without_snapshot_uses_peers_pit(self):
        """10:30 后无落盘：近 30m 截面仍按 τ=10:00 前缀从同伴重算。"""
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_last_30m

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        now = datetime(2026, 9, 18, 15, 1)
        bars = [
            {
                "date": "2026-09-18",
                "datetime": "2026-09-18 09:30:00",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
            },
            {
                "date": "2026-09-18",
                "datetime": "2026-09-18 10:00:00",
                "open": 10.05,
                "high": 10.4,
                "low": 10.0,
                "close": 10.35,
            },
        ]
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=now
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", return_value=["600000"]
            ) as peers, patch.object(
                m, "_peer_minute_bars_for_tau", return_value=bars
            ):
                got = resolve_sector_ret_last_30m("2026-09-18", "10:00")
                peers.assert_called()
                self.assertIsNotNone(got)
                self.assertGreater(float(got), 0.0)
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_horizon_cs_pin_survives_flush(self):
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        m.clear_sector_ret_cache()
        key = ("2026-09-18", "09:40")
        m._SECTOR_RET_30M_CACHE[key] = 0.12
        m._SECTOR_RET_45M_CACHE[key] = 0.22
        now = datetime(2026, 9, 18, 9, 40)
        with patch("core.signal.session_pit.shanghai_now", return_value=now), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
        ):
            self.assertTrue(m._pin_rebalance_cs_key("2026-09-18", "09:40", 12))
            m._SECTOR_RET_PINNED.add(key)
            m._flush_sector_ret_medians()
        self.assertAlmostEqual(m._SECTOR_RET_30M_CACHE[key], 0.12, places=6)
        self.assertAlmostEqual(m._SECTOR_RET_45M_CACHE[key], 0.22, places=6)
        m.clear_sector_ret_cache()

    def test_persist_0940_last_30m_survives_afternoon(self):
        import os
        import tempfile
        from datetime import datetime
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m
        from core.signal.minute_tau_feats import resolve_sector_ret_last_30m

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.clear_sector_ret_cache()
        morning = datetime(2026, 9, 18, 9, 40)
        afternoon = datetime(2026, 9, 18, 15, 1)
        try:
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=morning
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ):
                m._write_rebalance_cs_disk(
                    "2026-09-18", "09:40", 0.12, n_rets=12, field="last_30m"
                )
            m.clear_sector_ret_cache()
            with patch.object(m, "_rebalance_cs_store_path", return_value=path), patch(
                "core.signal.session_pit.shanghai_now", return_value=afternoon
            ), patch(
                "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
            ), patch.object(
                m, "_peer_codes_for_sector_ret", side_effect=AssertionError("warehouse")
            ):
                self.assertAlmostEqual(
                    resolve_sector_ret_last_30m("2026-09-18", "09:40"), 0.12, places=4
                )
        finally:
            if os.path.exists(path):
                os.remove(path)
            m.clear_sector_ret_cache()

    def test_clear_pack_keys_can_keep_sector_cs(self):
        feats = {
            "ret_open_to_tau": -1.58,
            "range_pct": 1.8,
            "t_hi_frac": 0.2,
            "sector_ret_to_tau": 0.84,
            "ret_vs_sector": -2.42,
            "gap_pct": 0.02,
        }
        kept = clear_minute_tau_pack_keys(dict(feats), include_cs=False)
        self.assertIsNone(kept.get("ret_open_to_tau"))
        self.assertIsNone(kept.get("t_hi_frac"))
        self.assertAlmostEqual(kept["sector_ret_to_tau"], 0.84, places=6)
        self.assertIsNone(kept.get("ret_open_to_tau"))
        self.assertAlmostEqual(kept["sector_ret_to_tau"], 0.84, places=6)
        self.assertEqual(kept.get("gap_pct"), 0.02)
        wiped = clear_minute_tau_pack_keys(dict(feats))
        self.assertIsNone(wiped.get("sector_ret_to_tau"))
        self.assertEqual(wiped.get("gap_pct"), 0.02)

    def test_pack_keys_constant(self):
        from core.signal.minute_tau_feats import MINUTE_TAU_SHAPE_KEYS

        self.assertEqual(len(MINUTE_TAU_PACK_KEYS), 13)
        self.assertEqual(len(MINUTE_TAU_SHAPE_KEYS), 2)
        self.assertEqual(set(MINUTE_TAU_SHAPE_KEYS), {"t_hi_frac", "t_lo_frac"})
        self.assertTrue(set(MINUTE_TAU_SHAPE_KEYS).isdisjoint(MINUTE_TAU_PACK_KEYS))

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
        # 09:35 … 14:00 覆盖训练网格 + 做T四轮前缀
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

        morning_grid = ["09:30", "10:00", "10:30", "11:00", "11:30"]
        _xs, ys, _dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_hm="10:30",
            tau_grid=morning_grid,
            min_history=5,
        )
        taus = [m.get("tau") for m in metas if m.get("date") == day]
        self.assertEqual(taus, morning_grid)
        self.assertEqual(len(ys), 5)
        # 同日标签统一 open→close，多 τ 行 y 相同；特征（前缀）不同
        self.assertTrue(all(abs(y - ys[0]) < 1e-9 for y in ys))
        self.assertEqual(_xs[0]["tau_elapsed_min"], 0.0)
        self.assertEqual(_xs[-1]["tau_elapsed_min"], 120.0)
        self.assertNotEqual(_xs[1].get("ret_open_to_tau"), _xs[-1].get("ret_open_to_tau"))
        self.assertIn("tau_elapsed_min", _xs[0])

    def test_minute_tau_grid_5m_range_morning_leg1(self):
        from core.research.tau_panel import (
            DEFAULT_MINUTE_TAU_GRID,
            DEFAULT_T0_TRAIN_TAU_GRID_5M,
            minute_tau_grid_5m_range,
        )
        from core.t0.config import T0_LAST_LEG1_HM

        morning = minute_tau_grid_5m_range(
            "09:30", T0_LAST_LEG1_HM, include_open=True
        )
        self.assertEqual(morning[0], "09:30")
        self.assertEqual(morning[1], "09:35")
        self.assertEqual(morning[-1], "11:00")
        self.assertEqual(len(morning), 19)
        self.assertEqual(DEFAULT_T0_TRAIN_TAU_GRID_5M, tuple(morning))
        self.assertEqual(tuple(DEFAULT_MINUTE_TAU_GRID), tuple(morning))
        from core.signal.minute_tau_grid import TRAIN_TAU_END_HM

        self.assertEqual(TRAIN_TAU_END_HM, T0_LAST_LEG1_HM)
        self.assertNotIn("13:00", DEFAULT_MINUTE_TAU_GRID)
        self.assertNotIn("14:00", DEFAULT_MINUTE_TAU_GRID)

    def test_format_shared_tau_formula_compact(self):
        from core.signal.minute_tau_grid import (
            DEFAULT_MINUTE_TAU_GRID,
            format_shared_tau_formula,
        )

        s = format_shared_tau_formula("close[T]/open[T]-1", DEFAULT_MINUTE_TAU_GRID)
        self.assertEqual(s, "close[T]/open[T]-1 · 5m τ 09:30–11:00")
        self.assertNotIn("τ∈{", s)
        self.assertEqual(
            format_shared_tau_formula("close[T]/open[T]-1", ["10:30"]),
            "close[T]/open[T]-1 · τ=10:30",
        )
        self.assertEqual(
            format_shared_tau_formula("close[T]/open[T]-1", []),
            "close[T]/open[T]-1",
        )

    def test_default_grid_covers_t0_slots(self):
        from core.research.tau_panel import DEFAULT_MINUTE_TAU_GRID
        from core.t0.config import DEFAULT_T0_SLOT_CLOCKS, T0_LAST_LEG1_HM

        self.assertEqual(tuple(DEFAULT_T0_SLOT_CLOCKS), ("11:00",))
        self.assertTrue(set(DEFAULT_T0_SLOT_CLOCKS).issubset(DEFAULT_MINUTE_TAU_GRID))
        self.assertIn("09:30", DEFAULT_MINUTE_TAU_GRID)
        self.assertIn("09:35", DEFAULT_MINUTE_TAU_GRID)
        self.assertIn(T0_LAST_LEG1_HM, DEFAULT_MINUTE_TAU_GRID)

    def test_causal_rebalance_tau_hm_morning_and_afternoon_cap(self):
        from core.signal.minute_tau_feats import causal_rebalance_tau_hm

        day = "2026-08-28"
        only_open = [
            {
                "date": day,
                "datetime": f"{day} 09:30:00",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.05,
                "volume": 500,
            }
        ]
        self.assertEqual(
            causal_rebalance_tau_hm(only_open, trade_date=day), "09:30"
        )
        self.assertEqual(
            causal_rebalance_tau_hm(_bars(day), trade_date=day), "09:55"
        )
        afternoon = _bars(day) + [
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.12,
                "volume": 800,
            },
            {
                "date": day,
                "datetime": f"{day} 10:30:00",
                "open": 10.12,
                "high": 10.3,
                "low": 10.1,
                "close": 10.2,
                "volume": 900,
            },
            {
                "date": day,
                "datetime": f"{day} 14:00:00",
                "open": 10.4,
                "high": 10.5,
                "low": 10.3,
                "close": 10.45,
                "volume": 700,
            },
        ]
        self.assertEqual(
            causal_rebalance_tau_hm(afternoon, trade_date=day), "10:00"
        )
        self.assertIsNone(causal_rebalance_tau_hm([], trade_date=day))

    def test_merge_causal_rebalance_does_not_fetch_future_clock(self):
        from unittest.mock import patch

        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        morning = [
            {
                "date": day,
                "datetime": f"{day} 09:30:00",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.05,
                "volume": 500,
            }
        ]
        with patch(
            "core.signal.minute_tau_feats._load_minute_bars_from_cache",
            return_value=morning,
        ), patch(
            "core.signal.minute_tau_feats._fetch_minute_bars_for_tau",
            return_value=_bars(day)
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
        ) as fetch_fn:
            feats, as_of, _ = merge_minute_tau_pack_into_feats(
                {"gap_pct": 0.5},
                code="000568",
                trade_date=day,
                open_px=10.0,
                causal_rebalance=True,
                load_cache_if_missing=True,
                fetch_if_missing=True,
            )
        fetch_fn.assert_not_called()
        self.assertEqual(as_of, f"{day}T09:30:00+08:00")
        self.assertAlmostEqual(feats["ret_open_to_tau"], 0.5, places=4)

    def test_merge_causal_rebalance_caps_afternoon_prefix(self):
        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        bars = _bars(day) + [
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.2,
                "volume": 800,
            },
            {
                "date": day,
                "datetime": f"{day} 14:00:00",
                "open": 10.5,
                "high": 10.6,
                "low": 10.4,
                "close": 10.55,
                "volume": 700,
            },
        ]
        feats, as_of, y_spec = merge_minute_tau_pack_into_feats(
            {},
            trade_date=day,
            open_px=10.0,
            minute_bars=bars,
            causal_rebalance=True,
            load_cache_if_missing=False,
        )
        self.assertEqual(as_of, f"{day}T10:00:00+08:00")
        self.assertEqual((y_spec or {}).get("tau"), "10:00")
        self.assertAlmostEqual(feats["ret_open_to_tau"], 2.0, places=4)

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
        # 按 τ 重切：收盘 leftover 不得挡住 10:30
        eod_ret = (9.0 / 10.0 - 1.0) * 100.0
        feats2, as_of2, _ = merge_minute_tau_pack_into_feats(
            {
                "ret_open_to_tau": eod_ret,
                "range_pct": 99.0,
                "t_hi_frac": 0.99,
            },
            trade_date=day,
            open_px=10.0,
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
                },
                {
                    "date": day,
                    "datetime": f"{day} 15:00:00",
                    "open": 9.2,
                    "high": 9.3,
                    "low": 8.9,
                    "close": 9.0,
                    "volume": 1000,
                },
            ],
            load_cache_if_missing=False,
        )
        self.assertAlmostEqual(feats2["ret_open_to_tau"], 1.5, places=4)  # 10.15/10
        self.assertNotAlmostEqual(feats2["ret_open_to_tau"], eod_ret, places=2)
        self.assertEqual(as_of2, f"{day}T10:30:00+08:00")
        self.assertLess(feats2["range_pct"], 99.0)
        self.assertIn("t_hi_frac", feats2)
        self.assertNotAlmostEqual(feats2["t_hi_frac"], 0.99, places=2)

    def test_path_shape_t_frac_is_pit(self):
        """t_hi/t_lo 进度按 τ 截断；不含更晚根。"""
        day = "2026-08-28"
        hms = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:30"]
        bars = []
        for i, hm in enumerate(hms):
            c = 10.0 + (0.08 if i % 2 else 0.0)
            bars.append(
                {
                    "date": day,
                    "datetime": f"{day} {hm}:00",
                    "open": 10.0,
                    "high": c + 0.05,
                    "low": c - 0.05,
                    "close": c,
                    "volume": 100,
                }
            )
        pack = extract_minute_tau_pack(
            bars, trade_date=day, tau_hm="10:00", open_px=10.0
        )
        self.assertIn("t_hi_frac", pack)
        self.assertIn("t_lo_frac", pack)
        self.assertTrue(0.0 <= pack["t_hi_frac"] <= 1.0)
        self.assertTrue(0.0 <= pack["t_lo_frac"] <= 1.0)
        # 截到 10:00：elapsed=30m；若最高在 09:40 则 frac=10/30
        pack_early = extract_minute_tau_pack(
            bars, trade_date=day, tau_hm="09:40", open_px=10.0
        )
        self.assertIn("t_hi_frac", pack_early)
        self.assertLessEqual(pack_early["t_hi_frac"], 1.0)

    def test_tau_z_excludes_shape_keys(self):
        from core.research.tau_ridge import TAU_Z_FEATURES
        from core.signal.minute_tau_feats import MINUTE_TAU_SHAPE_KEYS

        for k in MINUTE_TAU_SHAPE_KEYS:
            self.assertNotIn(k, TAU_Z_FEATURES)

    def test_merge_missing_tau_bar_does_not_stamp_1030(self):
        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        feats, as_of, y_spec = merge_minute_tau_pack_into_feats(
            {"ret_open_to_tau": -2.34, "gap_pct": 0.1},
            trade_date=day,
            open_px=10.0,
            tau_hm="10:30",
            minute_bars=_bars(day),  # 只到 09:55
            load_cache_if_missing=False,
        )
        self.assertIsNotNone(feats.get("ret_open_to_tau"))
        self.assertEqual(as_of, f"{day}T09:55:00+08:00")
        self.assertEqual((y_spec or {}).get("tau"), "09:55")

    def test_merge_no_bars_strips_eod_leftover_and_does_not_stamp(self):
        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        feats, as_of, y_spec = merge_minute_tau_pack_into_feats(
            {"ret_open_to_tau": -2.34, "gap_pct": 0.1, "t_hi_frac": 0.99},
            trade_date="2026-08-28",
            open_px=10.0,
            tau_hm="10:30",
            minute_bars=[],
            load_cache_if_missing=False,
        )
        self.assertIsNone(feats.get("ret_open_to_tau"))
        self.assertIsNone(feats.get("t_hi_frac"))
        self.assertEqual(feats.get("gap_pct"), 0.1)
        self.assertIsNone(as_of)
        self.assertIsNone(y_spec)

    def test_merge_fetch_if_missing_when_cache_lacks_tau_clock(self):
        from unittest.mock import patch

        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        fetched = _bars(day) + [
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
            "core.signal.minute_tau_feats._load_minute_bars_from_cache",
            return_value=[],
        ), patch(
            "core.signal.minute_tau_feats._fetch_minute_bars_for_tau",
            return_value=fetched,
        ) as fetch_fn:
            feats, as_of, _ = merge_minute_tau_pack_into_feats(
                {"gap_pct": 0.5},
                code="000568",
                trade_date=day,
                open_px=10.0,
                tau_hm="10:30",
                load_cache_if_missing=True,
                fetch_if_missing=True,
            )
        fetch_fn.assert_called_once_with("000568")
        self.assertAlmostEqual(feats["ret_open_to_tau"], 1.5, places=4)
        self.assertEqual(as_of, f"{day}T10:30:00+08:00")

    def test_merge_does_not_fetch_when_caller_passed_prefix(self):
        from unittest.mock import patch

        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats

        day = "2026-08-28"
        with patch(
            "core.signal.minute_tau_feats._fetch_minute_bars_for_tau",
            return_value=_bars(day)
            + [
                {
                    "date": day,
                    "datetime": f"{day} 15:00:00",
                    "open": 9.0,
                    "high": 9.1,
                    "low": 8.9,
                    "close": 9.0,
                    "volume": 1,
                }
            ],
        ) as fetch_fn:
            feats, as_of, _ = merge_minute_tau_pack_into_feats(
                {},
                code="000568",
                trade_date=day,
                open_px=10.0,
                tau_hm="10:30",
                minute_bars=_bars(day),
                load_cache_if_missing=True,
                fetch_if_missing=True,
            )
        fetch_fn.assert_not_called()
        self.assertEqual(as_of, f"{day}T09:55:00+08:00")
        self.assertAlmostEqual(feats["ret_open_to_tau"], 1.0, places=4)

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

    def test_attach_dual_score_pit_live_as_of_tau_is_session_not_yesterday(self):
        """盘中无 quote.date 时 as_of_tau 用会话日，不拿昨收 K 的 10:00。"""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        session = "2026-09-17"
        yday = "2026-09-16"
        item = {"stock_code": "600000", "predicted_score_eod": 0.5}
        quote = {"open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": yday, "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
        ]
        mins = _bars(session) + [
            {
                "date": session,
                "datetime": f"{session} 10:00:00",
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "close": 10.12,
                "volume": 800,
            }
        ]
        now = datetime(2026, 9, 17, 11, 42, tzinfo=timezone(timedelta(hours=8)))
        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
                "tau": "open",
                "y_spec": {"formula": "close[T]/open[T]-1"},
            },
        ), patch(
            "core.signal.session_pit.shanghai_now", return_value=now
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=0.12
        ):
            attach_dual_score_pit(
                item,
                quote=quote,
                bars=bars,
                minute_bars=mins,
                fuse_intraday=True,
            )
        asof = str(item.get("as_of_tau") or "")
        self.assertTrue(asof.startswith(f"{session}T"), asof)
        self.assertNotIn(yday, asof)

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

    def test_attach_dual_score_pit_overwrites_eod_leftover_at_1030(self):
        """簿上收盘 leftover 须被 10:30 前缀覆盖，不得前窥。"""
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        day = "2026-08-28"
        eod_ret = (9.0 / 10.0 - 1.0) * 100.0
        item = {
            "stock_code": "600000",
            "predicted_score_eod": 0.5,
            "features_tau": {"ret_open_to_tau": eod_ret, "range_pct": 50.0},
        }
        quote = {"date": day, "open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": "2026-08-27", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
            {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 9.0},
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
            },
            {
                "date": day,
                "datetime": f"{day} 15:00:00",
                "open": 9.2,
                "high": 9.3,
                "low": 8.9,
                "close": 9.0,
                "volume": 1000,
            },
        ]
        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
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
                minute_tau_hm="10:30",
            )
        rot = (item.get("features_tau") or {}).get("ret_open_to_tau")
        self.assertAlmostEqual(rot, 1.5, places=4)
        self.assertNotAlmostEqual(rot, eod_ret, places=2)
        self.assertAlmostEqual(pred.call_args[0][0].get("ret_open_to_tau"), 1.5, places=4)
        self.assertTrue(str(item.get("as_of_tau") or "").startswith(f"{day}T10:30"))

    def test_attach_dual_score_pit_use_minute_tau_false_strips_leftover(self):
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        day = "2026-08-28"
        item = {
            "stock_code": "600000",
            "predicted_score_eod": 0.5,
            "features_tau": {"ret_open_to_tau": -2.34},
        }
        quote = {"date": day, "open": 10.0, "prev_close": 9.9}
        bars = [
            {"date": "2026-08-27", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9},
            {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
        ]
        with patch(
            "core.signal.dual_score.resolve.get_dual_score_cfg",
            return_value={
                "enable_minute_tau": True,
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
                fuse_intraday=True,
                use_minute_tau=False,
            )
        self.assertIsNone((item.get("features_tau") or {}).get("ret_open_to_tau"))
        self.assertIsNone(pred.call_args[0][0].get("ret_open_to_tau"))

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

    def test_peer_minute_bars_reload_if_tau_missing(self):
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        m.clear_sector_ret_cache()
        morning = [
            {
                "date": "2026-09-16",
                "datetime": "2026-09-16 09:35:00",
                "open": 10,
                "high": 10.1,
                "low": 9.9,
                "close": 10,
                "volume": 1,
            }
        ]
        full = morning + [
            {
                "date": "2026-09-16",
                "datetime": "2026-09-16 10:40:00",
                "open": 10.2,
                "high": 10.3,
                "low": 10.1,
                "close": 10.25,
                "volume": 1,
            }
        ]
        m._PEER_MINUTE_BARS["000001"] = morning
        with patch(
            "core.ports.market.resolve_market_code", return_value=("CN", "000001")
        ), patch("core.store.load_minute_cache", return_value=(full,)) as load:
            bars = m._peer_minute_bars_for_tau(
                "000001", trade_date="2026-09-16", tau_hm="10:40"
            )
        load.assert_called_once()
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]["datetime"], "2026-09-16 10:40:00")
        with patch("core.store.load_minute_cache") as load2:
            again = m._peer_minute_bars_for_tau(
                "000001", trade_date="2026-09-16", tau_hm="10:40"
            )
        load2.assert_not_called()
        self.assertEqual(len(again), 2)

    def test_seed_peer_minute_bars_overrides_stale_same_clock(self):
        """本轮补拉的 09:45 必须盖掉早盘内存仓，否则 sector_ret 用残缺价。"""
        from unittest.mock import patch

        from core.signal import minute_tau_feats as m

        m.clear_sector_ret_cache()
        stale = [
            {
                "date": "2026-09-16",
                "datetime": "2026-09-16 09:45:00",
                "open": 10,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
            }
        ]
        fresh = [
            {
                "date": "2026-09-16",
                "datetime": "2026-09-16 09:45:00",
                "open": 10,
                "high": 10.2,
                "low": 9.8,
                "close": 10.4,
            }
        ]
        m._PEER_MINUTE_BARS["600183"] = stale
        m.seed_peer_minute_bars("600183", fresh)
        with patch("core.store.load_minute_cache") as load:
            bars = m._peer_minute_bars_for_tau(
                "600183", trade_date="2026-09-16", tau_hm="09:45"
            )
        load.assert_not_called()
        self.assertEqual(bars[-1]["close"], 10.4)
        m.clear_sector_ret_cache()

    def test_peer_fallback_does_not_stick_into_memo(self):
        from unittest.mock import mock_open, patch

        from core.signal import minute_tau_feats as m
        from core.t0.score_policy import clear_score_model_cache

        m.clear_sector_ret_cache()
        clear_score_model_cache()
        with patch(
            "core.t0.score_policy.current_t0_cs_universe_codes", return_value=[]
        ), patch(
            "core.t0.score_policy.active_book_codes_for_tau_pool", return_value=[]
        ), patch("os.path.isfile", return_value=True), patch(
            "builtins.open", mock_open(read_data='{"watchlist": ["WL001"]}')
        ):
            first = m._peer_codes_for_sector_ret()
        self.assertEqual(first, ["WL001"])
        self.assertIsNone(m._PEER_CODES_MEMO)
        m.set_peer_codes_for_sector_ret(["600183", "600875"])
        self.assertEqual(m._peer_codes_for_sector_ret()[:2], ["600183", "600875"])
        m.clear_sector_ret_cache()

    def test_merge_minute_pack_attaches_sibling_cs(self):
        from unittest.mock import patch

        from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats
        from core.signal import minute_tau_feats as m

        day = "2026-08-28"
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
        m.set_peer_codes_for_sector_ret(["600183", "600875"])
        try:
            with patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_to_tau",
                return_value=0.4,
            ), patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_last_30m",
                return_value=0.1,
            ), patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_last_45m",
                return_value=0.15,
            ), patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_last_60m",
                return_value=0.2,
            ), patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_last_75m",
                return_value=0.25,
            ), patch(
                "core.signal.minute_tau_feats.resolve_sector_ret_last_90m",
                return_value=0.3,
            ), patch(
                "core.signal.minute_tau_feats.extract_horizon_seq_packs",
                return_value={
                    "ret_last_30m": 1.0,
                    "ret_last_45m": 1.05,
                    "ret_last_60m": 1.1,
                    "ret_last_75m": 1.15,
                    "ret_last_90m": 1.2,
                },
            ):
                feats, _, _ = merge_minute_tau_pack_into_feats(
                    {"gap_pct": 0.5},
                    trade_date=day,
                    open_px=10.0,
                    prev_close=9.9,
                    tau_hm="10:30",
                    minute_bars=mins,
                    load_cache_if_missing=False,
                )
        finally:
            m.clear_sector_ret_cache()
        self.assertAlmostEqual(feats["sector_ret_to_tau"], 0.4, places=6)
        self.assertAlmostEqual(feats["sector_ret_last_30m"], 0.1, places=6)
        self.assertAlmostEqual(feats["sector_ret_last_45m"], 0.15, places=6)
        self.assertAlmostEqual(feats["sector_ret_last_60m"], 0.2, places=6)
        self.assertAlmostEqual(feats["sector_ret_last_75m"], 0.25, places=6)
        self.assertAlmostEqual(feats["sector_ret_last_90m"], 0.3, places=6)
        self.assertIn("ret_vs_sector", feats)


if __name__ == "__main__":
    unittest.main()
