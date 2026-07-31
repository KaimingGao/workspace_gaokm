import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.common.quote_api import StockAPI


class TestQuoteResolve(unittest.TestCase):
    def setUp(self):
        StockAPI.clear_cache()

    def test_resolve_a_share_name(self):
        self.assertEqual(StockAPI.resolve_symbol("茅台"), "sh600519")
        self.assertEqual(StockAPI.resolve_symbol("贵州茅台"), "sh600519")

    def test_resolve_a_share_code(self):
        self.assertEqual(StockAPI.resolve_symbol("600519"), "sh600519")
        self.assertEqual(StockAPI.resolve_symbol("000858"), "sz000858")
        self.assertEqual(StockAPI.resolve_symbol("300750"), "sz300750")

    def test_resolve_us_and_hk(self):
        self.assertEqual(StockAPI.resolve_symbol("AAPL"), "usAAPL")
        self.assertEqual(StockAPI.resolve_symbol("00700"), "hk00700")
        self.assertEqual(StockAPI.resolve_symbol("腾讯"), "hk00700")
        self.assertEqual(StockAPI.resolve_symbol("快手"), "hk01024")
        self.assertEqual(StockAPI.resolve_symbol("01024"), "hk01024")

    def test_unknown_chinese_name(self):
        self.assertIsNone(StockAPI.resolve_symbol("不存在的公司甲乙丙"))

    def test_query_unknown_returns_error(self):
        result = StockAPI.query("不存在的公司甲乙丙")
        self.assertFalse(result["success"])
        self.assertIn("无法识别", result["error"])

    def test_cache(self):
        fake = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "price": "100.00元",
            "symbol": "sh600519",
        }
        with patch.object(StockAPI, "_query_tencent", return_value=dict(fake)) as mocked:
            r1 = StockAPI.query("茅台")
            r2 = StockAPI.query("茅台")
            self.assertTrue(r1["success"])
            self.assertTrue(r2.get("cached"))
            self.assertEqual(mocked.call_count, 1)


class TestQuoteHandler(unittest.TestCase):
    def test_execute_empty(self):
        from skills.quote.handler import QuoteHandler

        handler = QuoteHandler()
        out = handler.execute({"parameters": {"stock_code": ""}})
        self.assertIn("请提供", out)


if __name__ == "__main__":
    unittest.main()
