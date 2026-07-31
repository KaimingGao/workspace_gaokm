"""观察舆情：标题缓存 · 规则情绪 · 扫描告警。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import sentiment as sentiment_mod
from core.sentiment import (
    detect_alerts,
    fetch_stock_headlines,
    list_watchlist_sentiment,
    read_last_sentiment_alerts,
    scan_watchlist_sentiment,
    score_headlines,
)


class TestScoreHeadlines(unittest.TestCase):
    def test_neutral_empty(self):
        out = score_headlines([], lexicon={"positive": ["增持"], "negative": ["减持"]})
        self.assertEqual(out["label"], "neutral")
        self.assertIsNone(out["score"])

    def test_bullish(self):
        out = score_headlines(
            [{"title": "公司宣布增持与回购计划"}],
            lexicon={"positive": ["增持", "回购"], "negative": ["减持"]},
        )
        self.assertEqual(out["label"], "bullish")
        self.assertEqual(out["score"], 0.0)
        self.assertIn("增持", out["hit_pos"])

    def test_bearish(self):
        out = score_headlines(
            [{"title": "股东拟减持股份"}],
            lexicon={"positive": ["增持"], "negative": ["减持", "立案"]},
        )
        self.assertEqual(out["label"], "bearish")
        self.assertEqual(out["score"], 1.0)

    def test_mixed(self):
        out = score_headlines(
            [{"title": "增持同时面临立案调查"}],
            lexicon={"positive": ["增持"], "negative": ["立案"]},
        )
        self.assertEqual(out["label"], "mixed")
        self.assertEqual(out["score"], 0.5)


class TestFetchAndCache(unittest.TestCase):
    def test_fetch_uses_cache(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(sentiment_mod, "NEWS_STORE_DIR", td), patch(
                "skills.news.engine.build_news",
                return_value={
                    "success": True,
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "items": [
                        {"title": "茅台增持公告", "time": "t", "source": "s", "url": ""},
                    ],
                },
            ) as mock_news:
                first = fetch_stock_headlines(
                    "600519",
                    limit=3,
                    force=True,
                    lexicon={"positive": ["增持"], "negative": ["减持"]},
                )
                self.assertTrue(first["ok"])
                self.assertEqual(first["sentiment"]["label"], "bullish")
                self.assertFalse(first["from_cache"])
                second = fetch_stock_headlines(
                    "600519",
                    limit=3,
                    force=False,
                    ttl_sec=3600,
                    lexicon={"positive": ["增持"], "negative": ["减持"]},
                )
                self.assertTrue(second["from_cache"])
                self.assertEqual(mock_news.call_count, 1)

    def test_list_watchlist_sentiment(self):
        with tempfile.TemporaryDirectory() as td:
            uni = os.path.join(td, "watching.json")
            with open(uni, "w", encoding="utf-8") as f:
                json.dump({"watchlist": ["600519", "000001"], "sources": [{"type": "static"}]}, f)
            with patch.object(sentiment_mod, "NEWS_STORE_DIR", os.path.join(td, "news")), patch.object(
                sentiment_mod, "WATCHING_PATH", uni
            ), patch(
                "core.sentiment.fetch_stock_headlines",
                side_effect=lambda code, **kw: {
                    "ok": True,
                    "stock_code": code,
                    "stock_name": code,
                    "items": [{"title": f"{code} 新闻"}],
                    "sentiment": {"label": "neutral", "score": None},
                    "updated_at": "t",
                    "from_cache": False,
                    "error": None,
                },
            ):
                out = list_watchlist_sentiment(limit=2)
            self.assertTrue(out["ok"])
            self.assertEqual(out["count"], 2)


class TestAlerts(unittest.TestCase):
    def test_detect_new_bearish_title(self):
        before = {
            "items": [{"title": "日常经营动态"}],
            "sentiment": {"label": "neutral", "score": None},
        }
        after = {
            "ok": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "items": [
                {"title": "日常经营动态"},
                {"title": "股东拟大幅减持"},
            ],
            "sentiment": {
                "label": "bearish",
                "score": 1.0,
                "hit_neg": ["减持"],
            },
        }
        alert = detect_alerts(before, after)
        self.assertIsNotNone(alert)
        self.assertIn("新标题", alert["reasons"][0])

    def test_detect_score_cross(self):
        before = {
            "items": [{"title": "a"}],
            "sentiment": {"label": "mixed", "score": 0.4},
        }
        after = {
            "ok": True,
            "stock_code": "1",
            "stock_name": "A",
            "items": [{"title": "a"}],
            "sentiment": {"label": "bearish", "score": 0.8},
        }
        alert = detect_alerts(before, after, score_threshold=0.6)
        self.assertIsNotNone(alert)
        self.assertTrue(any("风险分" in r for r in alert["reasons"]))

    def test_no_alert_when_unchanged(self):
        row = {
            "ok": True,
            "stock_code": "1",
            "items": [{"title": "hello"}],
            "sentiment": {"label": "neutral", "score": None},
        }
        self.assertIsNone(detect_alerts(row, row))

    def test_scan_and_read_alerts(self):
        with tempfile.TemporaryDirectory() as td:
            news_dir = os.path.join(td, "news")
            os.makedirs(news_dir)
            last_run = os.path.join(td, "schedule_last_run.json")
            uni = os.path.join(td, "watching.json")
            with open(uni, "w", encoding="utf-8") as f:
                json.dump({"watchlist": ["600519"], "sources": [{"type": "static"}]}, f)
            # seed previous cache (neutral)
            with open(os.path.join(news_dir, "600519.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ok": True,
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "items": [{"title": "旧闻"}],
                        "sentiment": {"label": "neutral", "score": None},
                        "fetched_at": 1,
                    },
                    f,
                )
            with patch.object(sentiment_mod, "NEWS_STORE_DIR", news_dir), patch.object(
                sentiment_mod, "WATCHING_PATH", uni
            ), patch.object(sentiment_mod, "SCHEDULE_LAST_RUN_PATH", last_run), patch(
                "skills.news.engine.build_news",
                return_value={
                    "success": True,
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "items": [
                        {"title": "旧闻", "time": "", "source": "", "url": ""},
                        {"title": "公司被立案调查", "time": "", "source": "", "url": ""},
                    ],
                },
            ):
                scanned = scan_watchlist_sentiment(["600519"], limit=5)
                self.assertTrue(scanned["ok"])
                self.assertGreaterEqual(scanned["alert_count"], 1)
                # write like schedule does
                with open(last_run, "w", encoding="utf-8") as f:
                    json.dump({"ts": 1000.0, **scanned}, f)
                alerts = read_last_sentiment_alerts()
                self.assertEqual(alerts["kind"], "sentiment_scan")
                self.assertGreaterEqual(alerts["alert_count"], 1)


class TestScheduleKind(unittest.TestCase):
    def test_run_schedule_sentiment_scan(self):
        from core.schedule_jobs import run_schedule

        with patch("core.schedule_jobs.run_sentiment_scan", return_value={"ok": True, "kind": "sentiment_scan"}) as m:
            out = run_schedule("sentiment_scan", codes=["600519"], limit=3)
        self.assertTrue(out["ok"])
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
