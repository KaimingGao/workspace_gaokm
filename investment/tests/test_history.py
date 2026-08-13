import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.common.history import fetch_daily_bars, resolve_market_code


class TestHistoryResolve(unittest.TestCase):
    def test_us(self):
        market, code = resolve_market_code("AAPL")
        self.assertEqual(market, "US")
        self.assertEqual(code, "AAPL")

    def test_fetch_us_mocked(self):
        bars = [
            {"date": "2026-01-01", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 10},
            {"date": "2026-01-02", "open": 1.5, "high": 2, "low": 1.4, "close": 1.8, "volume": 12},
        ]
        with patch(
            "skills.common.history.fetch_us_daily_bars", return_value=bars
        ), patch(
            "skills.common.history.resolve_market_code", return_value=("US", "AAPL")
        ):
            out, src = fetch_daily_bars("AAPL", limit=10)
        self.assertEqual(src, "akshare_us_daily")
        self.assertEqual(len(out), 2)

    def test_offline_ok_returns_stale_cache_without_network(self):
        stale_bars = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 10,
            }
            for i in range(1, 50)
        ]
        with patch(
            "skills.common.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "skills.common.history.load_daily_cache",
            side_effect=[
                None,  # fresh miss
                (stale_bars, {"data_source": "akshare_cn_daily:qfq"}),  # ignore_age
            ],
        ), patch(
            "skills.common.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars(
                "600519", limit=40, offline_ok=True, incremental=False
            )
        self.assertEqual(len(out), 40)
        self.assertTrue(str(src).startswith("cache"))

    def test_offline_only_skips_network_on_cache_miss(self):
        with patch(
            "skills.common.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "skills.common.history.load_daily_cache",
            return_value=None,
        ), patch(
            "skills.common.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars(
                "600519", limit=40, offline_only=True, incremental=False
            )
        self.assertEqual(out, [])
        self.assertEqual(src, "empty")

    def test_offline_only_returns_short_cache_without_network(self):
        short = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 10,
            }
            for i in range(1, 8)
        ]
        with patch(
            "skills.common.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "skills.common.history.load_daily_cache",
            return_value=(short, {"data_source": "akshare_cn_daily:qfq"}),
        ), patch(
            "skills.common.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars("600519", limit=40, offline_only=True)
        self.assertEqual(len(out), 7)
        self.assertTrue(str(src).startswith("cache"))

    def test_fetch_a_daily_falls_back_to_sina(self):
        from types import ModuleType

        from skills.common.history import fetch_a_daily_bars

        class _DF:
            empty = False

            def to_dict(self, orient="records"):
                return [
                    {
                        "date": "2026-01-01",
                        "open": 100,
                        "high": 102,
                        "low": 99,
                        "close": 101,
                        "volume": 1000,
                    },
                    {
                        "date": "2026-01-02",
                        "open": 101,
                        "high": 105,
                        "low": 100,
                        "close": 104,
                        "volume": 1200,
                    },
                ]

        fake = ModuleType("akshare")

        def _hist(**kwargs):
            raise RuntimeError("hist down")

        def _daily(symbol=None, **kwargs):
            return _DF()

        fake.stock_zh_a_hist = _hist
        fake.stock_zh_a_daily = _daily
        with patch.dict(sys.modules, {"akshare": fake}):
            bars = fetch_a_daily_bars("300750", limit=10)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]["close"], 104.0)

    def test_eval_required_paths_use_stocks(self):
        from evals.extract import check_required

        bundled = {
            "success": True,
            "stocks": [{"stock_code": "1"}, {"stock_code": "2"}],
        }
        self.assertEqual(
            check_required(bundled, ["success", "stocks"], min_items=2),
            [],
        )


if __name__ == "__main__":
    unittest.main()
