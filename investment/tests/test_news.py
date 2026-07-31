import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.news.engine import build_news, normalize_news_item
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
            "skills.news.engine.StockAPI.query",
            return_value={"success": True, "stock_code": "600519", "stock_name": "贵州茅台"},
        ), patch(
            "skills.news.engine.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "skills.news.engine.fetch_news_rows", return_value=rows
        ):
            result = build_news("茅台", limit=5)
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertIn("标题A", result["summary"])

    def test_handler_missing(self):
        out = NewsHandler().execute({"parameters": {}})
        self.assertIn("stock_code", json.loads(out).get("error", out))


if __name__ == "__main__":
    unittest.main()
