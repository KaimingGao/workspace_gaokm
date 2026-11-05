"""观察舆情：标题缓存 · 规则情绪 · 扫描告警。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

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

    def test_sparse_branch_fine_not_full_bearish(self):
        """分行违规夹在多数无命中标题里，不得虚高到 1.0 影响徽章标签。"""
        titles = [
            "邮储银行参加创新成果播种行动并发布初创贷科创金融产品",
            "邮储银行南平市分行被罚200万元",
            "涉贷款管理不到位等违规，邮储银行南平分行被罚200万元",
            "邮储银行如何运用AI赋能千行百业",
            "邮储银行：数智赋能筑防线",
        ]
        out = score_headlines(
            [{"title": t} for t in titles],
            lexicon={"positive": ["增持"], "negative": ["违规", "处罚", "减持"]},
        )
        self.assertEqual(out["label"], "bearish")
        self.assertLess(float(out["score"]), 0.6)
        self.assertEqual(out.get("n_hit_titles"), 1)
        self.assertEqual(out.get("n_titles"), 5)

    def test_profit_drop_is_bearish(self):
        out = score_headlines(
            [{"title": "供销大集：前三季度净利润同比减少185.88%"}]
        )
        self.assertEqual(out["label"], "bearish")
        self.assertEqual(out["score"], 1.0)
        self.assertEqual(out["hit_neg"], ["净利润减少"])
        self.assertEqual(out["hit_pos"], [])

    def test_loss_narrowing_and_cost_cut_are_not_bearish(self):
        narrowed = score_headlines(
            [
                {"title": "公司亏损同比减少30%"},
                {"title": "年报减亏"},
                {"title": "季度亏损收窄"},
            ],
            lexicon={"positive": ["扭亏"], "negative": ["亏损", "预亏", "下滑"]},
        )
        self.assertEqual(narrowed["label"], "bullish")
        self.assertEqual(narrowed["hit_pos"], ["亏损收窄"])
        self.assertNotIn("亏损", narrowed["hit_neg"])

        costs = score_headlines(
            [
                {"title": "管理费用同比减少20%"},
                {"title": "营业成本同比下降10%"},
                {"title": "有息负债减少"},
            ],
            lexicon={"positive": ["扭亏"], "negative": ["亏损", "下滑", "下跌"]},
        )
        self.assertEqual(costs["label"], "neutral")
        self.assertIsNone(costs["score"])

    def test_wider_loss_is_not_growth(self):
        out = score_headlines(
            [{"title": "法尔胜：净利润同比增亏"}],
            lexicon={"positive": ["增持"], "negative": ["亏损"]},
        )
        self.assertEqual(out["label"], "bearish")
        self.assertIn("增亏", out["hit_neg"])
        self.assertNotIn("净利润增长", out["hit_pos"])

    def test_faersheng_bundle_bearish_without_price_words(self):
        out = score_headlines(
            [
                {"title": "法尔胜：前三季度净利润同比减少94.98%"},
                {"title": "法尔胜：2026年第三季度营业收入为68560726.12元"},
                {"title": "一季度暴涨两倍大牛股法尔胜：净资产为负"},
            ]
        )
        self.assertEqual(out["label"], "bearish")
        self.assertEqual(out["n_titles"], 3)
        self.assertEqual(out["n_hit_titles"], 2)
        self.assertEqual(out["score"], round(2 / 3, 4))
        self.assertEqual(out["hit_neg"], ["净利润减少", "净资产为负"])
        self.assertNotIn("暴涨", out["hit_pos"])
        self.assertEqual(out["hit_pos"], [])

    def test_guangju_bundle_mixed(self):
        out = score_headlines(
            [
                {"title": "广聚能源：前三季度净利润同比增长177.71%"},
                {"title": "广聚能源：前三季度净利润约8518.14万元，同比增177.71%"},
                {"title": "广聚能源：第三季度归母净利润1657.65万元，同比下降22.35%"},
            ]
        )
        self.assertEqual(out["label"], "mixed")
        self.assertEqual(out["hit_pos"], ["净利润增长"])
        self.assertEqual(out["hit_neg"], ["归母净利润下降"])
        self.assertEqual(out["n_hit_titles"], 3)
        self.assertEqual(out["score"], 0.5)

    def test_weichai_record_high_and_long_cut_are_mixed(self):
        out = score_headlines(
            [
                {"title": "摩根大通在潍柴动力的H股好仓比例下降0.03%至8.97%"},
                {"title": "【调研快报】潍柴动力接待参加公司2026年三季报业绩交流会的投资者约420人调研"},
                {"title": "潍柴动力三季度经营业绩再创历史新高 高股息与高成长属性兼具"},
            ]
        )
        self.assertEqual(out["label"], "mixed")
        self.assertEqual(out["hit_pos"], ["历史新高"])
        self.assertEqual(out["hit_neg"], ["好仓下降"])
        self.assertEqual(out["n_titles"], 3)
        self.assertEqual(out["n_hit_titles"], 2)
        self.assertEqual(out["score"], round(0.5 * 2 / 3, 4))

        neutral = score_headlines(
            [
                {"title": "公司推进创新高质量发展"},
                {"title": "毛利率下降但未披露净利润"},
            ],
            lexicon={"positive": ["增持"], "negative": ["减持", "下滑", "下跌"]},
        )
        self.assertEqual(neutral["label"], "neutral")

    def test_shorthand_earnings_direction(self):
        up = score_headlines(
            [
                {"title": "五粮液前三季度净利同比增长49.98%"},
                {"title": "上海石化午后涨停 第三季度净利润同比大增2463.62%"},
                {"title": "粤海饲料：前三季度归母净利润同比翻倍增长"},
                {"title": "康强电子第三季度净利润环比翻倍"},
                {"title": "中国长城：前三季度净利润为2.43亿元 同比增长1410.39%"},
                {"title": "北化股份前三季度扣非净利增长73.22%"},
                {"title": "我爱我家前三季度扣非盈利增长155.62%"},
                {"title": "旺能环境三季度营收净利润均增长"},
            ]
        )
        self.assertEqual(up["label"], "bullish")
        self.assertTrue(up["hit_pos"])
        self.assertEqual(up["hit_neg"], [])

        down = score_headlines(
            [
                {"title": "*ST皇庭：第三季度营业收入同比降98.2%至5498万元"},
                {"title": "顺发恒能前三季度净利约2039.13万元，同比下降67.63%"},
                {"title": "中电鑫龙：第三季度营收同比降21.5%，归母净利润降55.3%"},
                {"title": "科伦药业前三季度扣非利润同比下降7%"},
                {"title": "北方铜业：三季度利润环比下降主要系铜精矿加工费持续低迷"},
            ]
        )
        self.assertEqual(down["label"], "bearish")
        self.assertTrue(down["hit_neg"])
        self.assertEqual(down["hit_pos"], [])

    def test_same_title_revenue_up_profit_down_is_mixed(self):
        out = score_headlines(
            [
                {
                    "title": "吉林化纤：2026年内前三季度营收增6%净利降三成，三季度单季转亏998万元"
                }
            ]
        )
        self.assertEqual(out["label"], "mixed")
        self.assertIn("营收增长", out["hit_pos"])
        self.assertIn("净利下降", out["hit_neg"])

    def test_loss_narrow_with_amount_gap_is_bullish(self):
        out = score_headlines(
            [{"title": "武汉凡谷：第三季度亏损572万元 同比收窄52.40%"}],
            lexicon={"positive": ["扭亏"], "negative": ["亏损", "下滑"]},
        )
        self.assertEqual(out["label"], "bullish")
        self.assertIn("亏损收窄", out["hit_pos"])
        self.assertNotIn("亏损", out["hit_neg"])

    def test_breakout_and_drop_need_a_company_subject(self):
        lex = {"positive": ["突破", "增持"], "negative": ["下跌", "下滑", "诉讼"]}
        scans = score_headlines(
            [
                {"title": "【盘中播报】233只个股突破半年线"},
                {"title": "今日盘中突破年线个股"},
                {"title": "机构风向标 | 罗牛山持股比例合计下跌1.55个百分点"},
                {"title": "煤炭概念下跌0.94%，主力资金净流出57股"},
                {"title": "16只白酒股下跌 贵州茅台1355.9元/股收盘"},
                {"title": "英力特：将多举措减轻烧碱价格下跌对公司经营的负面影响"},
                {"title": "北新建材：防渗新市场将对冲部分需求下滑，形成新的增长引擎"},
                {"title": "陕西金叶：公司及控股子公司不存在逾期担保及涉及诉讼的担保"},
            ],
            lexicon=lex,
        )
        self.assertEqual(scans["label"], "neutral")

        kept = score_headlines(
            [
                {"title": "广汽集团埃安i60交付突破十万辆"},
                {"title": "上汽集团前三季度海外销售突破百万，已超去年全年水平"},
            ],
            lexicon=lex,
        )
        self.assertEqual(kept["label"], "bullish")
        self.assertIn("突破", kept["hit_pos"])

        price = score_headlines(
            [{"title": "宁德时代股价下跌"}],
            lexicon=lex,
        )
        self.assertEqual(price["label"], "bearish")
        self.assertIn("下跌", price["hit_neg"])

    def test_more_titles_can_flip_neutral_to_bullish(self):
        """表列 limit=3 与明细 limit=8 曾导致「中」vs「多」；同池须共用条数。"""
        items = [
            {"title": "江海股份股价震荡整理"},
            {"title": "电容行业供需平稳"},
            {"title": "江海股份今日成交活跃"},
            {"title": "江海股份获机构增持"},
            {"title": "江海股份签订重大合同中标"},
        ]
        self.assertEqual(score_headlines(items[:3])["label"], "neutral")
        self.assertEqual(score_headlines(items)["label"], "bullish")

class TestFetchAndCache(unittest.TestCase):
    def test_fetch_uses_cache(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []

            def fetcher(code):
                calls.append(code)
                return [
                    {
                        "title": "茅台增持公告",
                        "time": "2024-06-01 10:00:00",
                        "source": "s",
                        "url": "",
                    }
                ]

            with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_DIR": td}), patch(
                "core.news_archive._default_fetcher", side_effect=fetcher
            ), patch(
                "core.data.facade.get_news",
                side_effect=lambda code, limit=5, use_cache=True: {
                    "success": True,
                    "stock_code": code,
                    "stock_name": "贵州茅台",
                    "items": fetcher(code),
                },
            ):
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
                self.assertEqual(len(calls), 1)

    def test_empty_ok_cache_refetches(self):
        """空成功写库后不占 TTL；单票路径会打网，且刷新时 bypass news_snap。"""
        with tempfile.TemporaryDirectory() as td:
            from core.news_archive import ingest_title_rows

            ingest_title_rows(
                "300750",
                [],
                fetched_at="2099-01-01T00:00:00",
                store_dir=td,
                ok=True,
                degraded=False,
            )
            calls = []

            def get_news(code, limit=5, use_cache=True):
                calls.append({"code": code, "use_cache": use_cache})
                return {
                    "success": True,
                    "stock_code": "300750",
                    "stock_name": "宁德时代",
                    "items": [
                        {
                            "title": "宁德时代公告",
                            "time": "2024-06-01",
                            "source": "s",
                            "url": "",
                        },
                    ],
                }

            with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_DIR": td}), patch(
                "core.data.facade.get_news", side_effect=get_news
            ):
                out = fetch_stock_headlines(
                    "300750",
                    limit=3,
                    force=False,
                    ttl_sec=3600,
                )
            self.assertTrue(out["ok"])
            self.assertEqual(len(out["items"]), 1)
            self.assertEqual(out["items"][0]["title"], "宁德时代公告")
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0]["use_cache"])

    def test_list_watchlist_sentiment(self):
        with tempfile.TemporaryDirectory() as td:
            uni = os.path.join(td, "watching.json")
            archive = os.path.join(td, "archive")
            os.makedirs(archive, exist_ok=True)
            with open(uni, "w", encoding="utf-8") as f:
                json.dump({"watchlist": ["600519", "000001"], "sources": [{"type": "static"}]}, f)
            calls = []

            def fetcher(code):
                calls.append(code)
                return [
                    {
                        "新闻标题": f"{code} 新闻",
                        "发布时间": "2026-11-04 10:00:00",
                        "文章来源": "东财",
                        "新闻链接": "http://example.test/n",
                    }
                ]

            with patch.object(sentiment_mod, "WATCHING_PATH", uni), patch.dict(
                os.environ, {"QUANTLAB_NEWS_ARCHIVE_DIR": archive}
            ), patch("core.news_archive._default_fetcher", side_effect=fetcher):
                first = list_watchlist_sentiment(limit=2)
                second = list_watchlist_sentiment(limit=2)
            self.assertTrue(first["ok"])
            self.assertEqual(first["count"], 2)
            self.assertEqual(sorted(calls), ["000001", "600519"])
            self.assertTrue(all(row.get("from_archive") for row in first["items"]))
            self.assertTrue(all(row.get("from_cache") for row in second["items"]))
            self.assertEqual(len(calls), 2)

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
            from core.news_archive import ingest_title_rows

            archive = os.path.join(td, "archive")
            os.makedirs(archive)
            last_run = os.path.join(td, "schedule_last_run.json")
            uni = os.path.join(td, "watching.json")
            with open(uni, "w", encoding="utf-8") as f:
                json.dump({"watchlist": ["600519"], "sources": [{"type": "static"}]}, f)
            ingest_title_rows(
                "600519",
                [{"title": "旧闻", "time": "2024-06-01"}],
                fetched_at="2024-06-01T10:00:00",
                store_dir=archive,
            )
            with patch.dict(
                os.environ, {"QUANTLAB_NEWS_ARCHIVE_DIR": archive}
            ), patch.object(sentiment_mod, "WATCHING_PATH", uni), patch.object(
                sentiment_mod, "SCHEDULE_LAST_RUN_PATH", last_run
            ), patch(
                "core.data.facade.get_news",
                return_value={
                    "success": True,
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "items": [
                        {"title": "旧闻", "time": "2024-06-01", "source": "", "url": ""},
                        {
                            "title": "公司被立案调查",
                            "time": "2024-06-02",
                            "source": "",
                            "url": "",
                        },
                    ],
                },
            ):
                scanned = scan_watchlist_sentiment(["600519"], limit=5)
                self.assertTrue(scanned["ok"])
                self.assertGreaterEqual(scanned["alert_count"], 1)
                with open(last_run, "w", encoding="utf-8") as f:
                    json.dump({"ts": 1000.0, **scanned}, f)
                alerts = read_last_sentiment_alerts()
                self.assertEqual(alerts["kind"], "sentiment_scan")
                self.assertGreaterEqual(alerts["alert_count"], 1)

class TestScheduleKind(unittest.TestCase):
    def test_run_schedule_sentiment_scan(self):
        from core.schedule_jobs import run_schedule

        with patch("core.platform.schedule_jobs.run_sentiment_scan", return_value={"ok": True, "kind": "sentiment_scan"}) as m:
            out = run_schedule("sentiment_scan", codes=["600519"], limit=3)
        self.assertTrue(out["ok"])
        m.assert_called_once()

if __name__ == "__main__":
    unittest.main()
