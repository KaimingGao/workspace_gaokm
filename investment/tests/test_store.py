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
from skills.common.history import fetch_daily_bars


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
        with patch("skills.common.history.fetch_a_daily_bars") as mock_fetch:
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
    def test_lock_calendar_day_replaces_whole_day(self):
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
        # 整日替换：旧 10:00 根不应残留
        self.assertEqual(len(d24), 1)
        self.assertAlmostEqual(d24[0]["close"], 6.15)
        self.assertEqual(d24[0]["volume"], 10000)

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
