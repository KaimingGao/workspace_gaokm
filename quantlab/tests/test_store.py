import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.store import (
    align_minute_volume_units,
    assess_quality,
    bars_backend,
    clear_daily_cache,
    load_daily_cache,
    load_minute_cache,
    merge_minute_bars_by_time,
    save_daily_cache,
    save_minute_cache,
)
from core.store_bars_sqlite import reset_conn_cache, touch_daily_fetched_at
from adapters.market.history import fetch_daily_bars


def _sample_bars(n=20):
    bars = []
    start = datetime.now() - timedelta(days=n + 2)
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        bars.append(
            {
                "date": d,
                "open": 10 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.8 + i * 0.1,
                "close": 10.2 + i * 0.1,
                "volume": 1000 + i,
            }
        )
    return bars


class _StoreBackendMixin:
    backend = "sqlite"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        reset_conn_cache()
        self._env = patch.dict(
            os.environ,
            {
                "INVESTMENT_STORE_DIR": self.tmp,
                "INVESTMENT_BARS_BACKEND": self.backend,
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(reset_conn_cache)

    def test_save_load_and_quality(self):
        self.assertEqual(bars_backend(), self.backend if self.backend != "file" else "json")
        bars = _sample_bars(25)
        path = save_daily_cache(
            "CN",
            "600519",
            bars,
            data_source="akshare_cn_daily",
            stock_code="600519",
            store_dir=self.tmp,
        )
        self.assertTrue(path)
        loaded = load_daily_cache("CN", "600519", store_dir=self.tmp)
        self.assertIsNotNone(loaded)
        lb, meta = loaded
        self.assertEqual(len(lb), 25)
        self.assertEqual(meta["quality"]["level"], "good")
        self.assertEqual(meta.get("bars_backend"), self.backend)

    def test_cache_expired(self):
        bars = _sample_bars(10)
        save_daily_cache(
            "CN",
            "000001",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        old = datetime.now() - timedelta(hours=48)
        if self.backend == "sqlite":
            self.assertTrue(
                touch_daily_fetched_at("CN", "000001", old, store_dir=self.tmp)
            )
        else:
            path = os.path.join(self.tmp, "daily", "CN", "000001.json")
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            payload["fetched_at"] = old.isoformat(timespec="seconds")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        self.assertIsNone(
            load_daily_cache("CN", "000001", max_age_hours=24, store_dir=self.tmp)
        )

    def test_assess_quality_thin(self):
        q = assess_quality(_sample_bars(5), data_source="akshare_cn_daily")
        self.assertEqual(q["level"], "thin")

    def test_fetch_daily_bars_uses_cache(self):
        bars = _sample_bars(30)
        save_daily_cache(
            "CN",
            "600519",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        with patch("adapters.market.history.fetch_a_daily_bars") as mock_fetch:
            out, src = fetch_daily_bars("600519", limit=20)
        self.assertEqual(len(out), 20)
        self.assertTrue(str(src).startswith("cache:"))
        mock_fetch.assert_not_called()

    def test_minute_roundtrip(self):
        bars = []
        start = datetime.now() - timedelta(days=2)
        for i in range(12):
            ts = (start + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")
            bars.append(
                {
                    "datetime": ts,
                    "date": ts[:10],
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                }
            )
        save_minute_cache(
            "CN",
            "600519",
            bars,
            period="5",
            data_source="test_minute",
            store_dir=self.tmp,
        )
        loaded = load_minute_cache("CN", "600519", "5", store_dir=self.tmp)
        self.assertIsNotNone(loaded)
        lb, meta = loaded
        self.assertEqual(len(lb), 12)
        self.assertEqual(meta.get("bars_backend"), self.backend)

    def test_minute_topup_does_not_wipe_older_days(self):
        old = []
        for hm in ("09:35:00", "14:55:00"):
            old.append(
                {
                    "datetime": f"2026-07-10 {hm}",
                    "date": "2026-07-10",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                }
            )
        save_minute_cache(
            "CN",
            "601898",
            old,
            period="5",
            data_source="hist",
            store_dir=self.tmp,
        )
        incoming = [
            {
                "datetime": "2026-09-11 09:35:00",
                "date": "2026-09-11",
                "open": 12,
                "high": 13,
                "low": 11,
                "close": 12.5,
                "volume": 200,
            },
            {
                "datetime": "2026-09-11 14:55:00",
                "date": "2026-09-11",
                "open": 12.5,
                "high": 13,
                "low": 12,
                "close": 12.8,
                "volume": 200,
            },
        ]
        save_minute_cache(
            "CN",
            "601898",
            incoming,
            period="5",
            data_source="topup",
            store_dir=self.tmp,
        )
        loaded = load_minute_cache(
            "CN", "601898", "5", min_bars=1, ignore_age=True, store_dir=self.tmp
        )
        self.assertIsNotNone(loaded)
        lb, meta = loaded
        days = {str(b.get("date") or "")[:10] for b in lb}
        self.assertIn("2026-07-10", days)
        self.assertIn("2026-09-11", days)
        self.assertGreaterEqual(len(lb), 4)
        self.assertEqual(str(meta.get("date_min") or "")[:10], "2026-07-10")
        self.assertEqual(str(meta.get("date_max") or "")[:10], "2026-09-11")

    def test_partial_minute_upsert_keeps_other_slots(self):
        day = "2026-08-24"
        old = [
            {
                "datetime": f"{day} 09:35:00",
                "date": day,
                "open": 6.0,
                "high": 6.2,
                "low": 5.9,
                "close": 6.1,
                "volume": 100,
            },
            {
                "datetime": f"{day} 10:00:00",
                "date": day,
                "open": 6.1,
                "high": 6.3,
                "low": 6.0,
                "close": 6.2,
                "volume": 100,
            },
        ]
        save_minute_cache(
            "CN", "601899", old, period="5", data_source="hist", store_dir=self.tmp
        )
        thin = [
            {
                "datetime": f"{day} 09:35:00",
                "date": day,
                "open": 6.05,
                "high": 6.2,
                "low": 6.0,
                "close": 6.15,
                "volume": 10000,
            }
        ]
        save_minute_cache(
            "CN", "601899", thin, period="5", data_source="partial", store_dir=self.tmp
        )
        loaded = load_minute_cache(
            "CN", "601899", "5", min_bars=1, ignore_age=True, store_dir=self.tmp
        )
        self.assertIsNotNone(loaded)
        lb, _meta = loaded
        by = {b["datetime"]: b for b in lb}
        self.assertEqual(set(by), {f"{day} 09:35:00", f"{day} 10:00:00"})
        self.assertAlmostEqual(by[f"{day} 10:00:00"]["close"], 6.2)
        self.assertAlmostEqual(by[f"{day} 09:35:00"]["close"], 6.15)
        self.assertEqual(by[f"{day} 09:35:00"]["volume"], 10000)

        covered = [
            {
                "datetime": f"{day} 09:35:00",
                "date": day,
                "open": 7.0,
                "high": 7.2,
                "low": 6.9,
                "close": 7.1,
                "volume": 200,
            },
            {
                "datetime": f"{day} 10:00:00",
                "date": day,
                "open": 7.1,
                "high": 7.3,
                "low": 7.0,
                "close": 7.2,
                "volume": 200,
            },
            {
                "datetime": f"{day} 10:05:00",
                "date": day,
                "open": 7.2,
                "high": 7.4,
                "low": 7.1,
                "close": 7.3,
                "volume": 200,
            },
        ]
        save_minute_cache(
            "CN", "601899", covered, period="5", data_source="fuller", store_dir=self.tmp
        )
        loaded = load_minute_cache(
            "CN", "601899", "5", min_bars=1, ignore_age=True, store_dir=self.tmp
        )
        lb, _meta = loaded
        by = {b["datetime"]: b for b in lb}
        self.assertEqual(
            set(by),
            {f"{day} 09:35:00", f"{day} 10:00:00", f"{day} 10:05:00"},
        )
        self.assertAlmostEqual(by[f"{day} 09:35:00"]["close"], 7.1)

    def test_empty_minute_save_does_not_wipe(self):
        day = "2026-08-24"
        old = [
            {
                "datetime": f"{day} 09:35:00",
                "date": day,
                "open": 6.0,
                "high": 6.2,
                "low": 5.9,
                "close": 6.1,
                "volume": 100,
            }
        ]
        save_minute_cache(
            "CN", "601900", old, period="5", data_source="hist", store_dir=self.tmp
        )
        save_minute_cache(
            "CN", "601900", [], period="5", data_source="empty", store_dir=self.tmp
        )
        loaded = load_minute_cache(
            "CN", "601900", "5", min_bars=1, ignore_age=True, store_dir=self.tmp
        )
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded[0]), 1)

    def test_short_daily_save_keeps_older_days(self):
        bars = _sample_bars(8)
        save_daily_cache(
            "CN",
            "600520",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        latest = dict(bars[-1])
        latest["close"] = 99.0
        save_daily_cache(
            "CN",
            "600520",
            [latest],
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        loaded = load_daily_cache("CN", "600520", min_bars=1, ignore_age=True, store_dir=self.tmp)
        self.assertIsNotNone(loaded)
        lb, _meta = loaded
        self.assertEqual(len(lb), 8)
        self.assertEqual(lb[0]["date"], bars[0]["date"])
        self.assertAlmostEqual(lb[-1]["close"], 99.0)

    def test_incomplete_daily_and_empty_do_not_wipe(self):
        bars = _sample_bars(4)
        save_daily_cache(
            "CN",
            "600521",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        thin = {"date": bars[-1]["date"], "close": 1.0}
        save_daily_cache(
            "CN",
            "600521",
            [thin],
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        save_daily_cache(
            "CN",
            "600521",
            [],
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        loaded = load_daily_cache("CN", "600521", min_bars=1, ignore_age=True, store_dir=self.tmp)
        self.assertIsNotNone(loaded)
        lb, _meta = loaded
        self.assertEqual(len(lb), 4)
        self.assertAlmostEqual(lb[-1]["close"], bars[-1]["close"])

    def test_clear_daily(self):
        save_daily_cache(
            "CN",
            "600519",
            _sample_bars(15),
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        n = clear_daily_cache(market="CN", store_dir=self.tmp)
        self.assertGreaterEqual(n, 1)
        self.assertIsNone(load_daily_cache("CN", "600519", store_dir=self.tmp))


class TestMinuteMergeLock(unittest.TestCase):
    def test_thinner_day_does_not_replace_closed_day(self):
        old = [
            {
                "datetime": "2026-08-24 09:35:00",
                "date": "2026-08-24",
                "open": 6.0,
                "close": 6.1,
                "volume": 100,
            },
            {
                "datetime": "2026-08-24 10:00:00",
                "date": "2026-08-24",
                "open": 6.1,
                "close": 6.2,
                "volume": 100,
            },
            {
                "datetime": "2026-08-25 09:35:00",
                "date": "2026-08-25",
                "open": 6.2,
                "close": 6.3,
                "volume": 100,
            },
        ]
        new = [
            {
                "datetime": "2026-08-24 09:35:00",
                "date": "2026-08-24",
                "open": 6.05,
                "close": 6.15,
                "volume": 10000,
            },
        ]
        merged = merge_minute_bars_by_time(old, new)
        days = {b["date"] for b in merged}
        self.assertEqual(days, {"2026-08-24", "2026-08-25"})
        d24 = [b for b in merged if b["date"] == "2026-08-24"]
        # 新日盖不住已有时刻：整天保留
        self.assertEqual(len(d24), 2)
        by = {b["datetime"]: b for b in d24}
        self.assertAlmostEqual(by["2026-08-24 10:00:00"]["close"], 6.2)
        self.assertAlmostEqual(by["2026-08-24 09:35:00"]["close"], 6.1)

    def test_covering_day_replaces_whole_day(self):
        old = [
            {
                "datetime": "2026-08-24 09:35:00",
                "date": "2026-08-24",
                "open": 6.0,
                "close": 6.1,
                "volume": 100,
            },
            {
                "datetime": "2026-08-24 10:00:00",
                "date": "2026-08-24",
                "open": 6.1,
                "close": 6.2,
                "volume": 100,
            },
        ]
        new = [
            {
                "datetime": "2026-08-24 09:35:00",
                "date": "2026-08-24",
                "open": 6.05,
                "close": 6.15,
                "volume": 10000,
            },
            {
                "datetime": "2026-08-24 10:00:00",
                "date": "2026-08-24",
                "open": 6.15,
                "close": 6.25,
                "volume": 10000,
            },
        ]
        merged = merge_minute_bars_by_time(old, new)
        self.assertEqual(len(merged), 2)
        by = {b["datetime"]: b for b in merged}
        self.assertAlmostEqual(by["2026-08-24 09:35:00"]["close"], 6.15)
        self.assertEqual(by["2026-08-24 10:00:00"]["volume"], 10000)

    def test_lock_off_keeps_ts_merge(self):
        old = [
            {"datetime": "2026-08-24 09:35:00", "date": "2026-08-24", "close": 1, "volume": 1},
            {"datetime": "2026-08-24 10:00:00", "date": "2026-08-24", "close": 2, "volume": 1},
        ]
        new = [
            {"datetime": "2026-08-24 09:35:00", "date": "2026-08-24", "close": 9, "volume": 9},
        ]
        merged = merge_minute_bars_by_time(old, new, lock_calendar_day=False)
        self.assertEqual(len(merged), 2)
        by = {b["datetime"]: b for b in merged}
        self.assertEqual(by["2026-08-24 09:35:00"]["close"], 9)
        self.assertEqual(by["2026-08-24 10:00:00"]["close"], 2)

    def test_align_hand_volume_x100(self):
        hand = []
        share = []
        for i in range(10):
            ts = f"2026-08-24 09:{35+i:02d}:00"
            hand.append(
                {"datetime": ts, "date": "2026-08-24", "close": 6.0, "volume": 1000.0 + i}
            )
            share.append(
                {
                    "datetime": ts,
                    "date": "2026-08-24",
                    "close": 6.0,
                    "volume": (1000.0 + i) * 100.0,
                }
            )
        scaled = align_minute_volume_units(hand, share)
        self.assertAlmostEqual(scaled[0]["volume"], 100000.0, places=1)
        self.assertEqual(scaled[0].get("volume_unit_scaled"), "hand_to_share_x100")


class TestDailyStoreSqlite(_StoreBackendMixin, unittest.TestCase):
    backend = "sqlite"


class TestDailyStoreJson(_StoreBackendMixin, unittest.TestCase):
    backend = "json"


if __name__ == "__main__":
    unittest.main()
