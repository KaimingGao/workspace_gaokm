"""tail_anomaly 分钟视图测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from core.signal.tail_anomaly_view import build_minute_tail_view


class TestTailAnomalyView(unittest.TestCase):
    def test_build_minute_tail_view_from_cache(self):
        from core.store import save_minute_cache

        with tempfile.TemporaryDirectory() as td:
            os.environ["INVESTMENT_STORE_DIR"] = td
            bars = []
            base_dt = "2026-08-19"
            price = 10.0
            for hm in (
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

            out = build_minute_tail_view("600001", tail_minutes=30, max_age_hours=24)
            self.assertTrue(out.get("ok"), out)
            self.assertGreaterEqual(len(out.get("tail_bars") or []), 2)
            self.assertIsNotNone(out.get("tail_volume_ratio"))
            day0 = (out.get("day_bars") or [None])[0]
            self.assertIsInstance(day0, dict)
            for k in ("open", "high", "low", "close", "label"):
                self.assertIn(k, day0)
            self.assertGreater(day0["high"], day0["low"] - 1e-9)
            self.assertIn((out.get("meta") or {}).get("source"), {"cache", "stale_cache"})
            os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_build_minute_tail_view_fetches_when_cache_missing(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["INVESTMENT_STORE_DIR"] = td
            fake_bars = [
                {
                    "date": "2026-08-28",
                    "datetime": f"2026-08-28 {hm}:00",
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.05,
                    "volume": 1000.0,
                }
                for hm in ("09:35", "09:40", "09:45", "10:00")
            ]
            with mock.patch(
                "core.ports.market.fetch_minute_bars",
                return_value=(
                    fake_bars,
                    {
                        "period": "5",
                        "data_source": "test_fetch",
                        "date_max": "2026-08-28",
                    },
                ),
            ) as fetch_fn:
                out = build_minute_tail_view(
                    "600519",
                    tail_minutes=30,
                    max_age_hours=12,
                    fetch_if_missing=True,
                )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(len(out.get("day_bars") or []), 4)
            self.assertEqual((out.get("day_bars") or [])[0].get("open"), 10.0)
            self.assertEqual((out.get("meta") or {}).get("source"), "test_fetch")
            fetch_fn.assert_called_once()
            os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_build_minute_tail_view_can_skip_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["INVESTMENT_STORE_DIR"] = td
            with mock.patch("core.ports.market.fetch_minute_bars") as fetch_fn:
                out = build_minute_tail_view(
                    "600519",
                    fetch_if_missing=False,
                    max_age_hours=12,
                )
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("reason"), "no_minute_cache")
            fetch_fn.assert_not_called()
            os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_build_minute_tail_view_uses_as_of_day_not_cache_last(self):
        from core.store import save_minute_cache

        with tempfile.TemporaryDirectory() as td:
            os.environ["INVESTMENT_STORE_DIR"] = td
            bars = []
            for day, close in (("2026-08-27", 9.0), ("2026-08-28", 10.0)):
                for hm in ("09:35", "09:40", "10:00", "14:55"):
                    bars.append(
                        {
                            "date": day,
                            "datetime": f"{day} {hm}:00",
                            "open": close,
                            "close": close,
                            "high": close + 0.05,
                            "low": close - 0.05,
                            "volume": 1000.0,
                        }
                    )
            save_minute_cache("CN", "600002", bars, period="5", data_source="test")
            out = build_minute_tail_view(
                "600002",
                as_of="2026-08-27",
                fetch_if_missing=False,
                max_age_hours=24,
            )
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("as_of"), "2026-08-27")
            self.assertEqual((out.get("meta") or {}).get("date_max"), "2026-08-27")
            self.assertTrue(out.get("day_bars"))
            self.assertAlmostEqual(out["day_bars"][0]["close"], 9.0, places=4)
            os.environ.pop("INVESTMENT_STORE_DIR", None)


if __name__ == "__main__":
    unittest.main()
