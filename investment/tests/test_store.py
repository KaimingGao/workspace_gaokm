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
    assess_quality,
    bars_backend,
    clear_daily_cache,
    load_daily_cache,
    load_minute_cache,
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


class TestDailyStoreSqlite(_StoreBackendMixin, unittest.TestCase):
    backend = "sqlite"


class TestDailyStoreJson(_StoreBackendMixin, unittest.TestCase):
    backend = "json"


if __name__ == "__main__":
    unittest.main()
