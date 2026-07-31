import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.compare.handler import CompareHandler


class TestCompare(unittest.TestCase):
    def test_need_two_stocks(self):
        handler = CompareHandler()
        result = handler.handle({"stock_codes": ["茅台"]})
        self.assertFalse(result["success"])

    def test_compare_success(self):
        from skills.compare.engine import CompareEngine

        def fake_query(code):
            data = {
                "茅台": {
                    "success": True,
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "price": "1600.00元",
                    "price_raw": 1600.0,
                    "change": "+1.00%",
                    "change_raw": 1.0,
                    "change_amount": "+16.00元",
                    "volume": "10万",
                    "currency": "CNY",
                    "market": "CN",
                },
                "五粮液": {
                    "success": True,
                    "stock_code": "000858",
                    "stock_name": "五粮液",
                    "price": "140.00元",
                    "price_raw": 140.0,
                    "change": "-0.50%",
                    "change_raw": -0.5,
                    "change_amount": "-0.70元",
                    "volume": "20万",
                    "currency": "CNY",
                    "market": "CN",
                },
            }
            return data[code]

        engine = CompareEngine()
        with patch("skills.compare.engine.StockAPI.query", side_effect=fake_query):
            result = engine.compare({"stock_codes": ["茅台", "五粮液"]})
            raw = CompareHandler().execute({"parameters": {"stock_codes": ["茅台", "五粮液"]}})

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["stocks"][0]["stock_name"], "贵州茅台")
        parsed = json.loads(raw)
        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["count"], 2)


if __name__ == "__main__":
    unittest.main()
