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
