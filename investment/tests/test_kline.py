import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.kline.analyzer import describe_candle, summarize_bars
from skills.kline.handler import KlineHandler
from skills.common.history import resolve_market_code


def _bear_bars():
    return [
        {"date": "2026-01-01", "open": 10, "high": 10.2, "low": 9.8, "close": 10.1, "volume": 1000},
        {"date": "2026-01-02", "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.2, "volume": 1100},
        {
            "date": "2026-01-03",
            "open": 47.18,
            "high": 47.40,
            "low": 43.06,
            "close": 43.28,
            "volume": 2000,
        },
    ]


class TestKlineAnalyzer(unittest.TestCase):
    def test_describe_yin(self):
        bars = _bear_bars()
        d = describe_candle(bars[-1], bars[-2])
        self.assertEqual(d["direction"], "阴线")
        self.assertTrue(any("阴" in t for t in d["tags"]))

    def test_summarize(self):
        s = summarize_bars(_bear_bars())
        self.assertIn("阴线", s["summary"])
        self.assertEqual(len(s["candles"]), 3)

    def test_resolve_market(self):
        self.assertEqual(resolve_market_code("快手")[0], "HK")
        self.assertEqual(resolve_market_code("01024")[0], "HK")
        self.assertEqual(resolve_market_code("600519")[0], "CN")


class TestKlineHandler(unittest.TestCase):
    def test_execute_mocked(self):
        fake_quote = {
            "success": True,
            "stock_code": "01024",
            "stock_name": "快手-W",
            "price_raw": 43.28,
            "change_raw": -7.76,
        }
        with patch(
            "skills.kline.engine.get_quote", return_value=fake_quote
        ), patch(
            "skills.kline.engine.get_bars",
            return_value={"bars": _bear_bars(), "data_source": "akshare_hk_daily"},
        ):
            out = KlineHandler().execute({"parameters": {"stock_code": "快手", "limit": 10}})
        data = json.loads(out)
        self.assertTrue(data["success"])
        self.assertIn("阴线", data["summary"])
        self.assertEqual(data["data_source"], "akshare_hk_daily")
        self.assertIn("不保证收益", data["note"])

    def test_quote_fallback_still_success(self):
        fake_quote = {
            "success": True,
            "stock_code": "01024",
            "stock_name": "快手-W",
            "price": "HK$43.28",
            "price_raw": 43.28,
            "change_raw": -7.76,
            "open": "HK$47.18",
            "high": "HK$47.40",
            "low": "HK$43.06",
        }
        with patch(
            "skills.kline.engine.get_quote", return_value=fake_quote
        ), patch(
            "skills.kline.engine.get_bars",
            return_value={"bars": [], "data_source": "empty"},
        ):
            out = KlineHandler().execute({"parameters": {"stock_code": "快手", "limit": 5}})
        data = json.loads(out)
        self.assertTrue(data["success"])
        self.assertEqual(data["data_source"], "quote_fallback")
        self.assertEqual(data["depth"], "intraday_proxy")
        self.assertTrue(data.get("latest_tags") or data.get("summary"))


if __name__ == "__main__":
    unittest.main()
