"""日线/分钟线不能记到另一个代码上。"""

from __future__ import annotations

import unittest

from core.research.bar_identity import (
    drop_daily_series_mismatch,
    drop_minute_maps_mismatch,
)


class TestBarIdentity(unittest.TestCase):
    def test_cmb_key_drops_moutai_daily(self):
        bars = {
            "600036": [
                {"date": "2026-06-09", "open": 1262.99, "close": 1256.0},
            ]
        }

        def lookup(code, day):
            self.assertEqual(code, "600036")
            self.assertEqual(day, "2026-06-09")
            return 38.49

        kept, dropped = drop_daily_series_mismatch(bars, lookup_close=lookup)
        self.assertEqual(kept, {})
        self.assertEqual(dropped, ["600036"])

    def test_matching_daily_kept(self):
        bars = {
            "600036": [{"date": "2026-06-09", "open": 38.5, "close": 38.49}],
            "600519": [{"date": "2026-06-09", "open": 1262.99, "close": 1256.0}],
        }

        def lookup(code, _day):
            return {"600036": 38.49, "600519": 1256.0}[code]

        kept, dropped = drop_daily_series_mismatch(bars, lookup_close=lookup)
        self.assertEqual(set(kept), {"600036", "600519"})
        self.assertEqual(dropped, [])

    def test_missing_store_keeps_fixture(self):
        bars = {"600036": [{"date": "2026-01-40", "close": 100.0}]}
        kept, dropped = drop_daily_series_mismatch(
            bars, lookup_close=lambda _c, _d: None
        )
        self.assertIn("600036", kept)
        self.assertEqual(dropped, [])

    def test_cmb_key_drops_moutai_minute(self):
        maps = {
            "600036": {
                "2026-06-09": [
                    {
                        "datetime": "2026-06-09 09:35:00",
                        "open": 1233.79346017,
                        "close": 1240.0,
                    }
                ]
            }
        }

        def lookup(code, stamp):
            self.assertEqual(code, "600036")
            self.assertEqual(stamp, "2026-06-09 09:35:00")
            return 37.4747065

        kept, dropped = drop_minute_maps_mismatch(maps, lookup_open=lookup)
        self.assertEqual(kept, {})
        self.assertEqual(dropped, ["600036"])

    def test_matching_minute_kept(self):
        maps = {
            "600519": {
                "2026-06-09": [
                    {
                        "datetime": "2026-06-09 09:35:00",
                        "open": 1233.79346017,
                        "close": 1240.0,
                    }
                ]
            }
        }
        kept, dropped = drop_minute_maps_mismatch(
            maps, lookup_open=lambda _c, _s: 1233.79346017
        )
        self.assertIn("600519", kept)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
