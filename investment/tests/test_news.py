import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.news.engine import (
    _JSONP_CB,
    build_news,
    fetch_news_rows,
    normalize_news_item,
)
from skills.news.handler import NewsHandler


class TestNews(unittest.TestCase):
    def test_normalize(self):
        item = normalize_news_item(
            {"新闻标题": "茅台公告", "发布时间": "2026-01-01", "文章来源": "测试"}
        )
        self.assertEqual(item["title"], "茅台公告")

    def test_build_mocked(self):
        rows = [
            {"新闻标题": "标题A", "发布时间": "t1", "文章来源": "s1"},
            {"新闻标题": "标题A", "发布时间": "t1", "文章来源": "s1"},
            {"新闻标题": "标题B", "发布时间": "t2", "文章来源": "s2"},
        ]
        with patch(
            "adapters.news.engine.query_quote",
            return_value={"success": True, "stock_code": "600519", "stock_name": "贵州茅台"},
        ), patch(
            "adapters.news.engine.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.news.engine.fetch_news_rows", return_value=rows
        ):
            result = build_news("茅台", limit=5)
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertIn("标题A", result["summary"])

    def test_fetch_news_rows_parses_jsonp(self):
        body = {
            "result": {
                "cmsArticleWebOld": [
                    {
                        "code": "202601011234567890",
                        "title": "<em>宁德时代</em>涨停",
                        "content": "摘要<em>内容</em>",
                        "date": "2026-01-01 10:00:00",
                        "mediaName": "测试源",
                        "url": "",
                    }
                ]
            }
        }
        text = f"{_JSONP_CB}({json.dumps(body, ensure_ascii=False)})"
        mock_resp = MagicMock()
        mock_resp.text = text
        mock_resp.raise_for_status = MagicMock()
        with patch("adapters.news.engine.requests.get", return_value=mock_resp) as get:
            rows = fetch_news_rows("300750", timeout=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["新闻标题"], "宁德时代涨停")
        self.assertIn("finance.eastmoney.com/a/", rows[0]["新闻链接"])
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs.get("timeout"), 3.0)

    def test_fetch_news_rows_propagates_timeout(self):
        with patch(
            "adapters.news.engine.requests.get",
            side_effect=requests_timeout(),
        ):
            with self.assertRaises(Exception) as ctx:
                fetch_news_rows("300750", timeout=2)
        msg = str(ctx.exception).lower()
        self.assertTrue("timeout" in msg or "timed out" in msg)

    def test_handler_missing(self):
        out = NewsHandler().execute({"parameters": {}})
        self.assertIn("stock_code", json.loads(out).get("error", out))


def requests_timeout():
    import requests

    return requests.exceptions.Timeout("timed out")


if __name__ == "__main__":
    unittest.main()
