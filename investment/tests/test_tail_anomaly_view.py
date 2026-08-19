"""tail_anomaly 分钟视图测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime

from core.signal.tail_anomaly_view import build_minute_tail_view


class TestTailAnomalyView(unittest.TestCase):
    def test_build_minute_tail_view_from_cache(self):
        from core.store import minute_cache_path, save_minute_cache

        with tempfile.TemporaryDirectory() as td:
            os.environ["INVESTMENT_STORE_DIR"] = td
            bars = []
            base_dt = "2026-08-19"
            price = 10.0
            for i, hm in enumerate(
                [
                    "09:35",
                    "09:40",
                    "09:45",
                    "10:00",
                    "11:00",
                    "13:05",
                    "14:00",
                    "14:30",
                    "14:45",
                    "14:55",
                ]
            ):
                vol = 1000.0 if hm < "14:30" else 5000.0
                if hm >= "14:45":
                    price -= 0.15
                bars.append(
                    {
                        "date": base_dt,
                        "datetime": f"{base_dt} {hm}:00",
                        "open": price,
                        "close": price,
                        "high": price + 0.05,
                        "low": price - 0.05,
                        "volume": vol,
                    }
                )
            save_minute_cache("CN", "600001", bars, period="5", data_source="test")
            path = minute_cache_path("CN", "600001", "5", td)
            self.assertTrue(os.path.isfile(path))

            out = build_minute_tail_view("600001", tail_minutes=30, max_age_hours=24)
            self.assertTrue(out.get("ok"), out)
            self.assertGreaterEqual(len(out.get("tail_bars") or []), 2)
            self.assertIsNotNone(out.get("tail_volume_ratio"))
            os.environ.pop("INVESTMENT_STORE_DIR", None)


if __name__ == "__main__":
    unittest.main()
