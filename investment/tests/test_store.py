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
    load_daily_cache,
    save_daily_cache,
)
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


class TestDailyStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_save_load_and_quality(self):
        bars = _sample_bars(25)
        path = save_daily_cache(
            "CN",
            "600519",
            bars,
            data_source="akshare_cn_daily",
            stock_code="600519",
            store_dir=self.tmp,
        )
        self.assertTrue(os.path.isfile(path))
        loaded = load_daily_cache("CN", "600519", store_dir=self.tmp)
        self.assertIsNotNone(loaded)
        lb, meta = loaded
        self.assertEqual(len(lb), 25)
        self.assertEqual(meta["quality"]["level"], "good")

    def test_cache_expired(self):
        bars = _sample_bars(10)
        path = save_daily_cache(
            "CN",
            "000001",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
        )
        old = datetime.now() - timedelta(hours=48)
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
        with patch.dict(os.environ, {"INVESTMENT_STORE_DIR": self.tmp}), patch(
            "skills.common.history.fetch_a_daily_bars"
        ) as mock_fetch:
            out, src = fetch_daily_bars("600519", limit=20)
        self.assertEqual(len(out), 20)
        self.assertTrue(str(src).startswith("cache:"))
        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
