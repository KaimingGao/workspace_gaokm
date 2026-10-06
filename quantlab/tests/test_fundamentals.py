import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.fundamentals.engine import build_fundamentals, spot_to_metrics
from skills.fundamentals.handler import FundamentalsHandler


class TestFundamentals(unittest.TestCase):
    def test_spot_to_metrics(self):
        m = spot_to_metrics(
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1500, "涨跌幅": 1.0, "市盈率-动态": 25.5, "市净率": 8.2}
        )
        self.assertEqual(m["pe"], 25.5)
        self.assertEqual(m["pb"], 8.2)

    def test_build_mocked(self):
        fake_quote = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "price": "1500元",
            "change": "+1%",
        }
        spot = {"代码": "600519", "名称": "贵州茅台", "最新价": 1500, "市盈率-动态": 22.0, "市净率": 7.5}

        with patch("adapters.fundamentals.engine.query_quote", return_value=fake_quote), patch(
            "adapters.fundamentals.engine.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.fundamentals.engine.fetch_cn_spot_row", return_value=spot
        ), patch(
            "adapters.fundamentals.engine.fetch_cn_valuation_latest", return_value={}
        ), patch(
            "adapters.fundamentals.engine.fetch_cn_financial_latest",
            return_value={"roe": 28.0, "revenue_growth": 12.0, "profit_growth": 10.0, "as_of": "2024-12-31", "source": "mock"},
        ):
            result = build_fundamentals("茅台")

        self.assertTrue(result["success"])
        self.assertEqual(result["metrics"]["pe"], 22.0)
        self.assertEqual(result["metrics"]["roe"], 28.0)
        self.assertTrue(result["highlights"])

    def test_handler(self):
        with patch(
            "skills.fundamentals.handler.build_fundamentals",
            return_value={"success": True, "note": "市场有风险，不保证收益，不代客下单。"},
        ):
            out = FundamentalsHandler().execute({"parameters": {"stock_code": "600519"}})
        self.assertIn("不保证收益", out)

    def test_hk_partial_success_with_quote(self):
        fake_quote = {
            "success": True,
            "stock_code": "01024",
            "stock_name": "快手-W",
            "price": "HK$43.28",
            "change": "-7.76%",
            "price_raw": 43.28,
            "change_raw": -7.76,
        }
        with patch("adapters.fundamentals.engine.query_quote", return_value=fake_quote), patch(
            "adapters.fundamentals.engine.resolve_market_code", return_value=("HK", "01024")
        ), patch(
            "adapters.fundamentals.engine.fetch_hk_spot_row",
            return_value={"代码": "01024", "市盈率": 22.5, "总市值": "1800亿"},
        ):
            result = build_fundamentals("快手")
        self.assertTrue(result["success"])
        self.assertEqual(result["metrics"]["pe"], 22.5)
        self.assertEqual(result["coverage"], "full")


if __name__ == "__main__":
    unittest.main()
