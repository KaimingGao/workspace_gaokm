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


class TestQuoteBatch(unittest.TestCase):
    def setUp(self):
        StockAPI.clear_cache()

    def test_batch_tencent_uses_short_timeout(self):
        import requests

        seen = []

        def fake_get(url, **kwargs):
            seen.append((url, kwargs.get("timeout"), kwargs.get("retries")))
            raise requests.exceptions.Timeout("t")

        with patch("skills.common.quote_api.requests_get_with_retry", side_effect=fake_get):
            with patch.object(StockAPI, "_query_eastmoney") as serial:
                StockAPI.batch_query(["600519"])
                serial.assert_not_called()
        tencent = [x for x in seen if "gtimg" in x[0]]
        self.assertTrue(tencent)
        self.assertLessEqual(float(tencent[0][1]), 4.0)
        self.assertLessEqual(int(tencent[0][2]), 1)

    def test_batch_uses_stale_cache_when_http_fails(self):
        import time
        import requests

        StockAPI._cache["sh600519"] = (
            time.time() - 120,
            {
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "price": "1800.00元",
                "price_raw": 1800.0,
            },
        )
        with patch(
            "skills.common.quote_api.requests_get_with_retry",
            side_effect=requests.exceptions.Timeout("t"),
        ):
            out = StockAPI.batch_query(["600519"])
        self.assertTrue(out["600519"]["success"])
        self.assertTrue(out["600519"].get("cached"))
        self.assertTrue(out["600519"].get("stale"))

    def test_batch_falls_back_to_em_ulist_once(self):
        import requests

        calls = []

        class _Resp:
            status_code = 200
            content = b""

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "diff": [
                            {
                                "f43": 180000,
                                "f44": 181000,
                                "f45": 179000,
                                "f46": 180000,
                                "f47": 10000,
                                "f48": 0,
                                "f57": "600519",
                                "f58": "贵州茅台",
                                "f59": 2,
                                "f60": 179000,
                            }
                        ]
                    }
                }

        def fake_get(url, **kwargs):
            calls.append(url)
            if "gtimg" in str(url):
                raise requests.exceptions.Timeout("t")
            return _Resp()

        with patch("skills.common.quote_api.requests_get_with_retry", side_effect=fake_get):
            with patch.object(StockAPI, "_query_eastmoney") as serial:
                out = StockAPI.batch_query(["600519", "000858"])
                serial.assert_not_called()
        self.assertTrue(out["600519"]["success"])
        self.assertEqual(out["600519"]["stock_name"], "贵州茅台")
        self.assertEqual(sum(1 for u in calls if "ulist" in str(u)), 1)
        self.assertFalse(out["000858"].get("success"))


class TestQuotePrice(unittest.TestCase):
    def test_zero_last_is_missing(self):
        from core.ports.market import quote_mark_price, quote_price, quote_prev_close

        q = {"success": True, "price_raw": 0.0, "prev_close": 12.75}
        self.assertIsNone(quote_price(q))
        self.assertAlmostEqual(quote_prev_close(q), 12.75)
        self.assertAlmostEqual(quote_mark_price(q), 12.75)

    def test_parse_tencent_keeps_prev_close_when_last_zero(self):
        fields = ["0"] * 45
        fields[1] = "智飞生物"
        fields[2] = "300122"
        fields[3] = "0.00"
        fields[4] = "12.75"
        fields[5] = "0.00"
        fields[31] = "0"
        fields[32] = "0"
        out = StockAPI._parse_tencent_data("300122", "sz300122", "~".join(fields))
        self.assertTrue(out["success"])
        self.assertEqual(out["price_raw"], 0.0)
        self.assertAlmostEqual(out["prev_close"], 12.75)


class TestQuoteHandler(unittest.TestCase):
    def test_execute_empty(self):
        from skills.quote.handler import QuoteHandler

        handler = QuoteHandler()
        out = handler.execute({"parameters": {"stock_code": ""}})
        self.assertIn("请提供", out)


if __name__ == "__main__":
    unittest.main()
