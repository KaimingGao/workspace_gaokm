"""本地新闻库：交易日对齐、去重、决策窗标签（软先验闸已下掉，引擎单测仍覆盖）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch


_HOLIDAYS: set = set()
_LEX = {"positive": ["增持"], "negative": ["减持"]}


class NewsArchiveAlignTests(unittest.TestCase):
    def _day(self, published, fetched, quality, **extra):
        from core.news_archive import assign_decision_day

        published_at = ""
        published_date = ""
        if quality == "timestamp":
            published_at = published
            published_date = published[:10]
        elif quality == "date_only":
            published_date = published
        return assign_decision_day(
            published_at=published_at,
            published_date=published_date,
            clock_quality=quality,
            fetched_at=fetched,
            fill_clock="09:30",
            holidays=_HOLIDAYS,
            **extra,
        )

    def test_six_alignment_cases(self):
        self.assertEqual(
            self._day("2026-11-03T08:10:00", "2026-11-03T09:20:00", "timestamp"),
            "2026-11-03",
        )
        self.assertEqual(
            self._day("2026-11-03T10:00:00", "2026-11-03T11:30:00", "timestamp"),
            "2026-11-04",
        )
        self.assertEqual(
            self._day("2026-10-31T14:00:00", "2026-10-31T20:30:00", "timestamp"),
            "2026-11-02",
        )
        self.assertEqual(
            self._day("2026-11-03", "2026-11-03T15:10:00", "date_only"),
            "2026-11-04",
        )
        self.assertEqual(
            self._day("2026-11-03", "2026-11-04T15:10:00", "date_only"),
            "2026-11-05",
        )
        self.assertEqual(self._day("", "2026-11-03T15:10:00", "unknown"), "")
        self.assertEqual(
            self._day(
                "2026-10-22T17:39:00",
                "2026-11-04T17:19:29",
                "timestamp",
                respect_fetch=False,
            ),
            "2026-10-23",
        )
        self.assertEqual(
            self._day(
                "2026-11-03",
                "2026-11-04T15:10:00",
                "date_only",
                respect_fetch=False,
            ),
            "2026-11-04",
        )

    def test_due_slots(self):
        from core.news_archive import due_poll_slot

        self.assertEqual(
            due_poll_slot(datetime(2026, 11, 4, 9, 19), holidays=_HOLIDAYS),
            "",
        )
        self.assertEqual(
            due_poll_slot(datetime(2026, 11, 4, 9, 20), holidays=_HOLIDAYS),
            "2026-11-04T09:20",
        )
        self.assertEqual(
            due_poll_slot(datetime(2026, 11, 4, 12, 0), holidays=_HOLIDAYS),
            "2026-11-04T11:30",
        )
        self.assertEqual(
            due_poll_slot(datetime(2026, 11, 1, 9, 30), holidays=_HOLIDAYS),
            "",
        )
        self.assertEqual(
            due_poll_slot(datetime(2026, 11, 1, 21, 0), holidays=_HOLIDAYS),
            "2026-11-01T20:30",
        )

    def test_next_poll_slot_is_strictly_later(self):
        from core.news_archive import next_poll_slot

        self.assertEqual(
            next_poll_slot(datetime(2026, 11, 4, 9, 19), holidays=_HOLIDAYS),
            "2026-11-04T09:20",
        )
        self.assertEqual(
            next_poll_slot(datetime(2026, 11, 4, 9, 20), holidays=_HOLIDAYS),
            "2026-11-04T11:30",
        )
        self.assertEqual(
            next_poll_slot(datetime(2026, 11, 4, 21, 0), holidays=_HOLIDAYS),
            "2026-11-05T09:20",
        )
        self.assertEqual(
            next_poll_slot(datetime(2026, 11, 1, 10, 0), holidays=_HOLIDAYS),
            "2026-11-01T20:30",
        )
        self.assertEqual(
            next_poll_slot(datetime(2026, 11, 1, 21, 0), holidays=_HOLIDAYS),
            "2026-11-02T09:20",
        )


class NewsArchiveStoreTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.store = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_duplicate_does_not_move_publish_later(self):
        from core.news_archive import ingest_title_rows, news_db_path
        import sqlite3

        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-03 08:10:00"}],
            fetched_at="2026-11-03T09:20:00",
            store_dir=self.store,
        )
        again = ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-03 18:00:00"}],
            fetched_at="2026-11-03T18:05:00",
            store_dir=self.store,
        )
        self.assertEqual(again["n_inserted"], 0)
        self.assertEqual(again["n_kept"], 1)
        conn = sqlite3.connect(news_db_path(self.store))
        try:
            row = conn.execute(
                "SELECT published_at, fetched_at FROM headline WHERE code = ?",
                ("600000",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "2026-11-03T08:10:00")
        self.assertEqual(row[1], "2026-11-03T09:20:00")

    def test_rejects_publish_after_fetch_and_blank_date(self):
        from core.news_archive import ingest_title_rows

        late = ingest_title_rows(
            "600000",
            [{"title": "未来稿", "time": "2026-11-03 12:00:00"}],
            fetched_at="2026-11-03T09:20:00",
            store_dir=self.store,
        )
        blank = ingest_title_rows(
            "600000",
            [{"title": "无日期"}],
            fetched_at="2026-11-03T09:20:00",
            store_dir=self.store,
        )
        self.assertEqual(late["n_inserted"], 0)
        self.assertEqual(late["n_rejected"], 1)
        self.assertEqual(blank["n_rejected"], 1)

    def test_window_labels_and_gate(self):
        from core.news_archive import (
            ingest_title_rows,
            load_decision_labels,
            sentiment_prior_of,
        )

        fetched = "2026-11-03T11:30:00"
        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-03 10:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        ingest_title_rows(
            "600002",
            [{"title": "公司增持", "time": "2026-11-03 10:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        ingest_title_rows(
            "600004",
            [],
            fetched_at=fetched,
            store_dir=self.store,
            ok=True,
        )
        ingest_title_rows(
            "600005",
            [],
            fetched_at=fetched,
            store_dir=self.store,
            ok=False,
            degraded=True,
            error="timeout",
        )
        labels = load_decision_labels(
            ["600000", "600002", "600003", "600004", "600005"],
            "2026-11-04",
            "09:30",
            store_dir=self.store,
            lexicon=_LEX,
            holidays=_HOLIDAYS,
        )
        self.assertEqual(labels["600000"]["label"], "bearish")
        self.assertEqual(labels["600000"]["coverage_status"], "ok")
        bear = sentiment_prior_of(labels["600000"])
        self.assertEqual(bear["label"], "bearish")
        self.assertAlmostEqual(bear["fuse_delta"], -0.001)
        self.assertAlmostEqual(bear["rank_mult"], 2.0)
        self.assertEqual(labels["600002"]["label"], "bullish")
        bull = sentiment_prior_of(labels["600002"])
        self.assertEqual(bull["label"], "bullish")
        self.assertAlmostEqual(bull["fuse_delta"], 0.001)
        self.assertAlmostEqual(bull["rank_mult"], 0.5)
        from core.news_archive import (
            SENTIMENT_FUSE_DELTA,
            SENTIMENT_RANK_MULT_BEAR,
            SENTIMENT_RANK_MULT_BULL,
        )

        self.assertAlmostEqual(SENTIMENT_FUSE_DELTA, 0.001)
        self.assertAlmostEqual(SENTIMENT_RANK_MULT_BEAR, 2.0)
        self.assertAlmostEqual(SENTIMENT_RANK_MULT_BULL, 0.5)
        self.assertEqual(labels["600003"]["coverage_status"], "gap")
        self.assertIsNone(sentiment_prior_of(labels["600003"]))
        self.assertEqual(labels["600004"]["coverage_status"], "empty")
        self.assertIsNone(sentiment_prior_of(labels["600004"]))
        self.assertEqual(labels["600005"]["coverage_status"], "degraded")
        self.assertIsNone(sentiment_prior_of(labels["600005"]))

    def test_poll_slot_runs_once(self):
        from core.news_archive import maybe_poll_news_archive

        calls = []

        def fetcher(code):
            calls.append(code)
            return [{"title": "股东减持", "time": "2026-11-04 08:00:00"}]

        with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_POLL": "1"}):
            first = maybe_poll_news_archive(
                now=datetime(2026, 11, 4, 9, 20),
                store_dir=self.store,
                codes=["600519"],
                fetcher=fetcher,
                holidays=_HOLIDAYS,
            )
            second = maybe_poll_news_archive(
                now=datetime(2026, 11, 4, 9, 25),
                store_dir=self.store,
                codes=["600519"],
                fetcher=fetcher,
                holidays=_HOLIDAYS,
            )
        self.assertTrue(first["ran"])
        self.assertEqual(first["n_ok"], 1)
        self.assertFalse(second["ran"])
        self.assertEqual(second["reason"], "already")
        self.assertEqual(calls, ["600519"])

    def test_rank_lot_sentiment_soft_prior(self):
        from core.news_archive import ingest_title_rows
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        fetched = "2026-11-03T11:30:00"
        for code, title in (("600000", "股东减持"), ("600001", "股东减持"), ("600002", "公司增持")):
            ingest_title_rows(
                code,
                [{"title": title, "time": "2026-11-03 10:00:00"}],
                fetched_at=fetched,
                store_dir=self.store,
            )
        held = {
            "stock_code": "600000",
            "shares": 100,
            "lots": [
                {
                    "shares": 100,
                    "bought_at": "2026-11-03T09:30:00.000",
                    "bought_date": "2026-11-03",
                }
            ],
        }
        # 1.05% 空 → 先验后 0.95% 不过 1% 入场；0.95% 多 → 1.05% 过入场。
        scored = [
            {"stock_code": "600000", "y_oo_fuse": 1.5, "y_on": 0.0},
            {"stock_code": "600001", "y_oo_fuse": 1.05, "y_on": 0.0},
            {"stock_code": "600002", "y_oo_fuse": 0.95, "y_on": 0.0},
            {"stock_code": "600003", "y_oo_fuse": 2.0, "y_on": 0.0},
        ]
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            out = plan_rank_lot_day(
                scored=scored,
                holdings=[held],
                cash=1_000_000,
                prices={c["stock_code"]: 10.0 for c in scored},
                cfg={
                    "fuse_enter": 0.01,
                    "fuse_strong": 0.02,
                    "cash_floor": 0.0,
                    "holdings_mv_cap": 0.0,
                    "t0_sell_blocks": {},
                    "top_k": 5,
                    "fusion_w_oo": 0.5,
                    "fusion_w_oc": 0.5,
                    "fill_clock": "09:30",
                    "sentiment_gate_enabled": True,
                },
                as_of="2026-11-04",
                bars_by_code={},
            )
        sells = {s["stock_code"]: s for s in out["sells"]}
        self.assertNotIn("600000", sells)
        buy_codes = [b["stock_code"] for b in out["buys"]]
        self.assertNotIn("600001", buy_codes)
        self.assertIn("600002", buy_codes)
        self.assertIn("600003", buy_codes)
        skip_codes = {s["stock_code"]: s for s in out["skips"]}
        self.assertIn("600001", skip_codes)
        reason_bear = str(skip_codes["600001"]["reason"])
        self.assertIn("未过入场", reason_bear)
        self.assertIn("舆情空", reason_bear)
        # 原因写原始 fuse（1.05%），不写先验后的 0.95%。
        self.assertIn("fuse=1.050%", reason_bear)
        self.assertNotIn("fuse=0.950%", reason_bear)
        self.assertEqual(skip_codes["600001"].get("sentiment_prior"), "bearish")
        # 表头写原始 fuse；先验只进门槛。
        self.assertAlmostEqual(
            float(skip_codes["600001"].get("y_oo_fuse_score") or 0), 0.0105, places=5
        )
        self.assertAlmostEqual(
            float(skip_codes["600001"].get("y_oo_fuse") or 0), 1.05, places=5
        )
        buy_by = {b["stock_code"]: b for b in out["buys"]}
        self.assertAlmostEqual(
            float(buy_by["600002"].get("y_oo_fuse_score") or 0), 0.0095, places=5
        )
        self.assertIn("舆情多", str(buy_by["600002"].get("reason") or ""))
        labels = out["sentiment_labels"]
        self.assertEqual(labels["600000"], "bearish")
        self.assertEqual(labels["600002"], "bullish")
        self.assertEqual(labels["600003"], "missing")
        self.assertEqual(out["sentiment_stamp"].get("mode"), "prior")
        self.assertEqual(out["sentiment_stamp"].get("n_bearish"), 2)
        self.assertEqual(out["sentiment_stamp"].get("n_bullish"), 1)

        # 名次闸：原始 6/5，×2 后 12/10 不过 10；表与原因写原始名次。
        ingest_title_rows(
            "600009",
            [{"title": "股东减持", "time": "2026-11-03 10:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        scored_rank = [
            {
                "stock_code": "600009",
                "y_oo_fuse": 2.0,
                "y_on": 0.0,
                "y_oo_rank": 6,
                "y_oo_rank_n": 20,
                "y_tc_rank": 5,
                "y_tc_rank_n": 20,
            }
        ]
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            rank_out = plan_rank_lot_day(
                scored=scored_rank,
                holdings=[],
                cash=1_000_000,
                prices={"600009": 10.0},
                cfg={
                    "fuse_enter": 0.005,
                    "fuse_strong": 0.05,
                    "cash_floor": 0.0,
                    "holdings_mv_cap": 0.0,
                    "t0_sell_blocks": {},
                    "top_k": 5,
                    "fusion_w_oo": 0.5,
                    "fusion_w_oc": 0.5,
                    "fill_clock": "09:30",
                    "sentiment_gate_enabled": True,
                    "oo_rank_max": 10,
                },
                as_of="2026-11-04",
                bars_by_code={},
            )
        rank_skip = {s["stock_code"]: s for s in rank_out["skips"]}["600009"]
        rank_reason = str(rank_skip.get("reason") or "")
        self.assertIn("oo_rank=6", rank_reason)
        self.assertIn("τc_rank=5", rank_reason)
        self.assertNotIn("oo_rank=12", rank_reason)
        self.assertEqual(float(rank_skip.get("y_oo_rank")), 6.0)
        self.assertEqual(float(rank_skip.get("y_tc_rank")), 5.0)

    def test_preopen_clock_ignores_titles_fetched_after_the_open(self):
        from core.news_archive import ingest_title_rows
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-04 09:40:00"}],
            fetched_at="2026-11-04T09:50:00",
            store_dir=self.store,
        )
        ingest_title_rows(
            "600001",
            [{"title": "股东减持", "time": "2026-11-03 10:00:00"}],
            fetched_at="2026-11-03T20:30:00",
            store_dir=self.store,
        )
        held = [
            {
                "stock_code": code,
                "shares": 100,
                "lots": [
                    {
                        "shares": 100,
                        "bought_at": "2026-11-03T09:30:00.000",
                        "bought_date": "2026-11-03",
                    }
                ],
            }
            for code in ("600000", "600001")
        ]
        scored = [
            {"stock_code": "600000", "y_oo_fuse": 1.5, "y_on": 0.0},
            {"stock_code": "600001", "y_oo_fuse": 1.5, "y_on": 0.0},
        ]
        cfg = {
            "fuse_enter": 0.01,
            "fuse_strong": 0.02,
            "cash_floor": 0.0,
            "holdings_mv_cap": 0.0,
            "t0_sell_blocks": {},
            "top_k": 5,
            "fusion_w_oo": 0.5,
            "fusion_w_oc": 0.5,
            "fill_clock": "10:00",
            "sentiment_gate_enabled": True,
        }
        prices = {c["stock_code"]: 10.0 for c in scored}
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            pinned = plan_rank_lot_day(
                scored=scored,
                holdings=held,
                cash=1_000_000,
                prices=prices,
                cfg=cfg,
                as_of="2026-11-04",
                bars_by_code={},
                sentiment_clock="09:30",
            )
            leaked = plan_rank_lot_day(
                scored=scored,
                holdings=held,
                cash=1_000_000,
                prices=prices,
                cfg=cfg,
                as_of="2026-11-04",
                bars_by_code={},
            )
        # 软先验不清仓；09:30 针仍按入库窗标标签，10:00 针会吃到开盘后抓取的标题。
        self.assertEqual(pinned["sells"], [])
        self.assertEqual(leaked["sells"], [])
        self.assertEqual(pinned["sentiment_labels"]["600000"], "bearish")
        self.assertEqual(pinned["sentiment_basis"]["600000"], "library")
        self.assertEqual(pinned["sentiment_basis"]["600001"], "gate")
        self.assertEqual(pinned["sentiment_stamp"]["fill_clock"], "09:30")
        self.assertEqual(leaked["sentiment_labels"]["600000"], "bearish")
        self.assertEqual(leaked["sentiment_basis"]["600000"], "gate")
        self.assertEqual(leaked["sentiment_stamp"]["fill_clock"], "10:00")

    def test_replay_keys_off_publish_time(self):
        from core.news_archive import ingest_title_rows, load_decision_labels, news_db_path
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-10-22 17:39:00"}],
            fetched_at="2026-11-04T17:19:29",
            store_dir=self.store,
        )
        ingest_title_rows(
            "600001",
            [{"title": "股东减持", "time": "2026-10-23 10:00:00"}],
            fetched_at="2026-11-04T17:19:29",
            store_dir=self.store,
        )
        held = {
            "stock_code": "600000",
            "shares": 100,
            "lots": [
                {
                    "shares": 100,
                    "bought_at": "2026-10-22T09:30:00.000",
                    "bought_date": "2026-10-22",
                }
            ],
        }
        scored = [
            {"stock_code": "600000", "y_oo_fuse": 1.5, "y_on": 0.0},
            {"stock_code": "600001", "y_oo_fuse": 1.5, "y_on": 0.0},
        ]
        cfg = {
            "fuse_enter": 0.01,
            "fuse_strong": 0.02,
            "cash_floor": 0.0,
            "holdings_mv_cap": 0.0,
            "t0_sell_blocks": {},
            "top_k": 5,
            "fusion_w_oo": 0.5,
            "fusion_w_oc": 0.5,
            "fill_clock": "09:30",
            "sentiment_gate_enabled": True,
        }
        prices = {c["stock_code"]: 10.0 for c in scored}
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            published = load_decision_labels(
                ["600000", "600001", "600002"],
                "2026-10-23",
                "09:30",
                align="publish",
            )
            import sqlite3

            conn = sqlite3.connect(news_db_path(self.store))
            try:
                n_cached = conn.execute(
                    "SELECT COUNT(*) FROM decision_label WHERE decision_date = '2026-10-23'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(n_cached, 0)
            fetched = load_decision_labels(
                ["600000", "600001"],
                "2026-10-23",
                "09:30",
            )
            replay = plan_rank_lot_day(
                scored=scored,
                holdings=[held],
                cash=1_000_000,
                prices=prices,
                cfg=cfg,
                as_of="2026-10-23",
                bars_by_code={},
                sentiment_clock="09:30",
                sentiment_align="publish",
            )
            live = plan_rank_lot_day(
                scored=scored,
                holdings=[held],
                cash=1_000_000,
                prices=prices,
                cfg=cfg,
                as_of="2026-10-23",
                bars_by_code={},
                sentiment_clock="09:30",
            )
        self.assertEqual(published["600000"]["label"], "bearish")
        self.assertEqual(published["600000"]["coverage_status"], "ok")
        self.assertEqual(published["600001"]["label"], "missing")
        self.assertEqual(published["600002"]["label"], "missing")
        self.assertEqual(fetched["600000"]["label"], "missing")
        self.assertEqual(replay["sells"], [])
        self.assertEqual(replay["sentiment_basis"]["600000"], "gate")
        self.assertEqual(replay["sentiment_labels"]["600000"], "bearish")
        self.assertEqual(replay["sentiment_labels"]["600001"], "missing")
        self.assertNotIn("library", replay["sentiment_basis"].values())
        self.assertEqual(live["sells"], [])
        self.assertEqual(live["sentiment_stamp"]["align"], "fetch")
        self.assertEqual(replay["sentiment_stamp"]["align"], "publish")
        cfg_off = dict(cfg)
        cfg_off["sentiment_gate_enabled"] = False
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            off = plan_rank_lot_day(
                scored=scored,
                holdings=[held],
                cash=1_000_000,
                prices=prices,
                cfg=cfg_off,
                as_of="2026-10-23",
                bars_by_code={},
                sentiment_clock="09:30",
                sentiment_align="publish",
            )
        self.assertEqual(off["sells"], [])
        self.assertFalse(any("舆情空" in str(s.get("reason") or "") for s in off["skips"]))
        self.assertEqual(off["sentiment_labels"]["600000"], "bearish")
        self.assertEqual(off["sentiment_basis"]["600000"], "gate")

    def test_desk_keeps_sentiment_skip(self):
        from core.news_archive import SENTIMENT_GATE_REASON
        from core.paper.rebalance.desk import DESK_ROW_HARD_CAP, desk_rows_from_result

        report = [
            {
                "stock_code": f"{i:06d}",
                "action": "skip",
                "reason": "fuse<入场 未过入场",
            }
            for i in range(1, DESK_ROW_HARD_CAP + 1)
        ]
        # 历史硬闸 reason 仍钉住
        report.append(
            {
                "stock_code": "600519",
                "action": "skip",
                "reason": SENTIMENT_GATE_REASON,
            }
        )
        # 软先验说明也钉住
        report.append(
            {
                "stock_code": "600518",
                "action": "skip",
                "reason": "fuse=-0.050%<入场0.500% 未过入场 · 舆情空 fuse-0.1% rank×2",
                "sentiment_prior": True,
            }
        )
        rows = desk_rows_from_result({"rebalance_report": report})
        by = {str(r.get("stock_code") or ""): r for r in rows}
        self.assertIn("600519", by)
        self.assertTrue(by["600519"].get("sentiment_gate"))
        self.assertIn("600518", by)
        self.assertTrue(by["600518"].get("sentiment_gate"))

    def test_desk_rows_carry_decision_label(self):
        from core.news_archive import ingest_title_rows
        from core.paper.rebalance.desk import (
            attach_sentiment_labels,
            desk_rows_from_result,
            sentiment_display_for_codes,
        )

        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-03 10:00:00"}],
            fetched_at="2026-11-03T11:30:00",
            store_dir=self.store,
        )
        copied = desk_rows_from_result(
            {
                "rebalance_report": [
                    {
                        "stock_code": "600000",
                        "action": "hold",
                        "reason": "持有",
                        "sentiment_label": "bearish",
                    }
                ]
            }
        )
        self.assertEqual(copied[0]["sentiment_label"], "bearish")
        ingest_title_rows(
            "600008",
            [{"title": "股东减持", "time": "2026-11-04 16:00:00"}],
            fetched_at="2026-11-04T16:08:00",
            store_dir=self.store,
        )
        rows = [
            {"stock_code": "600000"},
            {"stock_code": "600008"},
            {"stock_code": "600009"},
        ]
        with patch("core.news_archive.archive_store_dir", return_value=self.store), patch(
            "core.news_archive._lexicon_version", return_value="test"
        ), patch("core.sentiment.load_lexicon", return_value=_LEX):
            attach_sentiment_labels(rows, session="2026-11-04", fill_clock="09:30")
            shown = sentiment_display_for_codes(
                ["600000", "600008", "600009"],
                session="2026-11-04",
                fill_clock="09:30",
            )
        self.assertEqual(rows[0]["sentiment_label"], "bearish")
        self.assertEqual(rows[0]["sentiment_basis"], "gate")
        self.assertEqual(rows[1]["sentiment_label"], "bearish")
        self.assertEqual(rows[1]["sentiment_basis"], "library")
        self.assertEqual(rows[2]["sentiment_label"], "missing")
        self.assertEqual(shown["600008"], {"label": "bearish", "basis": "library"})
        self.assertEqual(shown["600000"]["basis"], "gate")

    def test_universe_is_the_full_watchlist(self):
        import json

        from core.news_archive import archive_universe_codes

        watching = os.path.join(self.store, "watching.json")
        paper = os.path.join(self.store, "paper.json")
        codes = [f"{i:06d}" for i in range(1, 801)]
        with open(watching, "w", encoding="utf-8") as f:
            json.dump({"watchlist": codes}, f)
        with open(paper, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "cash": 0,
                    "holdings": [
                        {"stock_code": "900001", "shares": 100},
                        {"stock_code": "600000", "shares": 0},
                    ],
                },
                f,
            )
        with patch("core.paths.WATCHING_PATH", watching), patch("core.paths.PAPER_PATH", paper):
            full = archive_universe_codes()
            clipped = archive_universe_codes(cap=40)
        self.assertEqual(len(full), 801)
        self.assertEqual(full[0], "000001")
        self.assertEqual(full[-2], "000800")
        self.assertEqual(full[-1], "900001")
        self.assertEqual(len(clipped), 40)
        self.assertNotIn("600000", full)
        self.assertNotIn("900001", clipped)

    def test_samples_prefer_distinct_titles_and_labels(self):
        from core.news_archive import _sample_headlines, ingest_title_rows

        fetched = "2026-11-04T11:30:00"
        ingest_title_rows(
            "600000",
            [{"title": "午后大涨", "time": "2026-11-04 11:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        ingest_title_rows(
            "600001",
            [{"title": "午后大涨", "time": "2026-11-04 11:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        ingest_title_rows(
            "600002",
            [{"title": "股东增持", "time": "2026-11-04 10:00:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        ingest_title_rows(
            "600003",
            [{"title": "股东减持", "time": "2026-11-04 09:40:00"}],
            fetched_at=fetched,
            store_dir=self.store,
        )
        samples = _sample_headlines(
            self.store,
            fill_clock="09:30",
            holidays=_HOLIDAYS,
            limit=5,
        )
        titles = [row["title"] for row in samples]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(titles.count("午后大涨"), 1)
        labels = {row["title"]: row["label"] for row in samples}
        self.assertEqual(labels["股东增持"], "bullish")
        self.assertEqual(labels["股东减持"], "bearish")

    def test_sample_list_filters_by_date_and_code(self):
        from core.news_archive import archive_status, ingest_title_rows

        ingest_title_rows(
            "600000",
            [{"title": "股东减持", "time": "2026-11-03 10:00:00"}],
            fetched_at="2026-11-03T11:30:00",
            store_dir=self.store,
        )
        ingest_title_rows(
            "600001",
            [{"title": "股东增持", "time": "2026-11-04 10:00:00"}],
            fetched_at="2026-11-04T11:30:00",
            store_dir=self.store,
        )
        both = archive_status(
            now=datetime(2026, 11, 4, 16, 0),
            store_dir=self.store,
            holidays=_HOLIDAYS,
            codes=["600000"],
            fill_clock="09:30",
            sample_date="2026-11-04",
            sample_code="600001",
        )
        self.assertEqual([row["code"] for row in both["samples"]], ["600001"])
        self.assertEqual(both["samples"][0]["published_date"], "2026-11-04")
        self.assertEqual(both["sample_date"], "2026-11-04")
        self.assertEqual(both["sample_code"], "600001")
        self.assertEqual(both["sample_dates"], ["2026-11-04", "2026-11-03"])
        self.assertCountEqual(
            [item["code"] for item in both["sample_stocks"]],
            ["600000", "600001"],
        )
        day_only = archive_status(
            now=datetime(2026, 11, 4, 16, 0),
            store_dir=self.store,
            holidays=_HOLIDAYS,
            codes=["600000"],
            fill_clock="09:30",
            sample_date="2026-11-03",
        )
        self.assertTrue(day_only["samples"])
        self.assertTrue(all(row["published_date"] == "2026-11-03" for row in day_only["samples"]))

    def test_poll_stops_after_block_signals(self):
        from core.news_archive import poll_news_archive

        called = []

        class Ban(Exception):
            def __init__(self):
                super().__init__("429 too many requests")
                self.response = type("R", (), {"status_code": 429})()

        def fetcher(code):
            called.append(code)
            raise Ban()

        out = poll_news_archive(
            now=datetime(2026, 11, 4, 9, 20),
            store_dir=self.store,
            codes=["600000", "600001", "600002"],
            fetcher=fetcher,
            holidays=_HOLIDAYS,
        )
        self.assertEqual(called, ["600000", "600001"])
        self.assertEqual(out["n_fail"], 2)
        self.assertEqual(out["n_skipped"], 1)
        self.assertEqual(out["stop_reason"], "rate_limit")
        self.assertEqual(out["failed_codes"], ["600000", "600001"])
        self.assertFalse(out["progress"]["running"])
        self.assertEqual(out["progress"]["n_done"], 2)
        self.assertEqual(out["progress"]["n_skipped"], 1)

    def test_poll_progress_counts_and_rejects_a_second_run(self):
        import threading

        from core.news_archive import poll_news_archive, poll_progress

        seen = []

        def fetcher(code):
            seen.append(poll_progress()["n_done"])
            return [{"title": "股东增持", "time": "2026-11-03 10:00:00"}]

        out = poll_news_archive(
            now=datetime(2026, 11, 4, 9, 20),
            store_dir=self.store,
            codes=["600000", "600001"],
            fetcher=fetcher,
            holidays=_HOLIDAYS,
        )
        self.assertEqual(seen, [0, 1])
        self.assertFalse(out["progress"]["running"])
        self.assertEqual(out["progress"]["n_ok"], 2)
        self.assertFalse(poll_progress()["running"])

        started = threading.Event()
        release = threading.Event()

        def blocking(code):
            started.set()
            release.wait(2)
            return []

        holder = threading.Thread(
            target=lambda: poll_news_archive(
                now=datetime(2026, 11, 4, 9, 20),
                store_dir=self.store,
                codes=["600000"],
                fetcher=blocking,
                holidays=_HOLIDAYS,
            )
        )
        holder.start()
        self.assertTrue(started.wait(2))
        busy = poll_news_archive(
            now=datetime(2026, 11, 4, 9, 20),
            store_dir=self.store,
            codes=["600001"],
            fetcher=lambda code: [],
            holidays=_HOLIDAYS,
        )
        self.assertEqual(busy["reason"], "busy")
        self.assertTrue(busy["progress"]["running"])
        release.set()
        holder.join(3)
        self.assertFalse(poll_progress()["running"])

    def test_archive_status_coverage_and_failures(self):
        from datetime import datetime
        from unittest.mock import patch

        from core.news_archive import archive_status, poll_news_archive

        def fetcher(code):
            if code == "600001":
                raise RuntimeError("timeout")
            return [{"title": "股东减持", "time": "2026-11-03 10:00:00"}]

        poll_news_archive(
            now=datetime(2026, 11, 4, 9, 20),
            store_dir=self.store,
            codes=["600000", "600001"],
            fetcher=fetcher,
            holidays=_HOLIDAYS,
        )
        with patch("core.market.calendar.resolve_session_date", return_value="2026-11-04"):
            st = archive_status(
                now=datetime(2026, 11, 4, 10, 0),
                store_dir=self.store,
                holidays=_HOLIDAYS,
                codes=["600000", "600001", "600003"],
                fill_clock="09:30",
            )
        self.assertEqual(st["coverage"]["ok"], 1)
        self.assertEqual(st["coverage"]["degraded"], 1)
        self.assertEqual(st["coverage"]["gap"], 1)
        self.assertEqual(st["next_slot"], "2026-11-04T11:30")
        self.assertEqual(st["last"]["n_ok"], 1)
        self.assertEqual(st["last"]["n_fail"], 1)
        self.assertEqual(st["failed_codes"], ["600001"])
        self.assertIn("不回补", st["note"])
        portrait = st["portrait"]
        self.assertEqual(portrait["n_titles"], 1)
        self.assertEqual(portrait["n_codes"], 1)
        self.assertEqual(portrait["n_timestamp"], 1)
        self.assertEqual(portrait["n_date_only"], 0)
        self.assertEqual(portrait["published_min"], "2026-11-03")
        self.assertEqual(portrait["titles_median"], 1)
        self.assertEqual(portrait["titles_max"], 1)
        self.assertEqual(portrait["n_rounds"], 1)
        self.assertEqual(portrait["n_inserted"], 1)
        self.assertEqual(portrait["labels"]["bearish"], 1)
        self.assertEqual(portrait["labels"]["bullish"], 0)
        self.assertEqual(portrait["n_titles_today"], 1)
        self.assertEqual(portrait["n_hit_today"], 1)
        samples = st["samples"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["code"], "600000")
        self.assertEqual(samples[0]["title"], "股东减持")
        self.assertEqual(samples[0]["label"], "bearish")
        self.assertEqual(samples[0]["decision_date"], "2026-11-04")
        self.assertEqual(samples[0]["clock_quality"], "timestamp")
        missing = archive_status(
            now=datetime(2026, 11, 4, 10, 0),
            store_dir=os.path.join(self.store, "absent"),
            holidays=_HOLIDAYS,
            codes=["600000"],
            fill_clock="09:30",
        )
        self.assertEqual(missing["portrait"]["n_titles"], 0)
        self.assertEqual(missing["portrait"]["labels"]["bearish"], 0)
        self.assertEqual(missing["samples"], [])
        self.assertFalse(os.path.isfile(os.path.join(self.store, "absent", "news.db")))

    def test_watch_table_reads_local_then_eastmoney(self):
        import sqlite3

        from core.news_archive import ingest_title_rows, news_db_path, serve_watch_headlines

        now = datetime(2026, 11, 4, 16, 0, 0)
        ingest_title_rows(
            "600183",
            [{"title": "生益科技回购", "time": "2026-11-04 10:00:00"}],
            fetched_at="2026-11-04T15:50:00",
            store_dir=self.store,
        )
        ingest_title_rows(
            "600000",
            [{"title": "旧标题", "time": "2026-11-04 09:00:00"}],
            fetched_at="2026-11-04T14:00:00",
            store_dir=self.store,
        )
        calls = []

        def fetcher(code):
            calls.append(code)
            if code == "600002":
                raise RuntimeError("down")
            return [
                {
                    "新闻标题": f"{code} 新标题",
                    "发布时间": "2026-11-04 10:30:00",
                    "文章来源": "东财",
                    "新闻链接": "http://example.test/a",
                }
            ]

        with patch("core.sentiment.load_lexicon", return_value=_LEX):
            out = serve_watch_headlines(
                ["600183", "600000", "600001", "600002"],
                limit=3,
                store_dir=self.store,
                now=now,
                fetcher=fetcher,
                lexicon=_LEX,
            )
            again = serve_watch_headlines(
                ["600001"],
                limit=3,
                store_dir=self.store,
                now=now,
                fetcher=fetcher,
                lexicon=_LEX,
            )
        by = {row["stock_code"]: row for row in out["items"]}
        self.assertEqual(calls, ["600000", "600001", "600002"])
        self.assertTrue(by["600183"]["from_cache"])
        self.assertEqual(by["600183"]["items"][0]["title"], "生益科技回购")
        self.assertFalse(by["600000"]["from_cache"])
        self.assertEqual(by["600000"]["items"][0]["title"], "600000 新标题")
        self.assertEqual(by["600001"]["items"][0]["title"], "600001 新标题")
        self.assertEqual(by["600002"]["sentiment"]["label"], "missing")
        self.assertTrue(again["items"][0]["from_cache"])
        self.assertEqual(calls, ["600000", "600001", "600002"])
        conn = sqlite3.connect(news_db_path(self.store))
        try:
            n_log = conn.execute(
                "SELECT COUNT(*) FROM fetch_log WHERE code = '600001'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n_log, 1)

    def test_watch_table_stops_after_block_signals(self):
        import sqlite3

        from core.news_archive import news_db_path, serve_watch_headlines

        class Ban(Exception):
            def __init__(self):
                super().__init__("429")
                self.response = type("R", (), {"status_code": 429})()

        calls = []

        def fetcher(code):
            calls.append(code)
            raise Ban()

        out = serve_watch_headlines(
            ["600000", "600001", "600002"],
            limit=3,
            store_dir=self.store,
            now=datetime(2026, 11, 4, 16, 0, 0),
            fetcher=fetcher,
            lexicon=_LEX,
        )
        self.assertEqual(calls, ["600000", "600001"])
        self.assertEqual(out["stop_reason"], "rate_limit")
        self.assertEqual(out["items"][2]["sentiment"]["label"], "missing")
        conn = sqlite3.connect(news_db_path(self.store))
        try:
            skipped = conn.execute(
                "SELECT COUNT(*) FROM fetch_log WHERE code = '600002'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(skipped, 0)

    def test_empty_payload_not_fresh_for_watch(self):
        """空成功写库后不当 TTL 命中，下一次仍会打网。"""
        from core.news_archive import ingest_title_rows, serve_watch_headlines

        ingest_title_rows(
            "600000",
            [],
            fetched_at="2026-11-04T15:55:00",
            store_dir=self.store,
            ok=True,
        )
        calls = []

        def fetcher(code):
            calls.append(code)
            return [{"title": "公司增持", "time": "2026-11-04 10:00:00"}]

        out = serve_watch_headlines(
            ["600000"],
            limit=3,
            store_dir=self.store,
            now=datetime(2026, 11, 4, 16, 0, 0),
            fetcher=fetcher,
            lexicon=_LEX,
        )
        self.assertEqual(calls, ["600000"])
        self.assertFalse(out["items"][0]["from_cache"])
        self.assertEqual(out["items"][0]["items"][0]["title"], "公司增持")

    def test_failed_fetch_log_not_fresh_for_watch(self):
        from core.news_archive import ingest_title_rows, serve_watch_headlines

        ingest_title_rows(
            "600000",
            [{"title": "旧标题", "time": "2026-11-04 09:00:00"}],
            fetched_at="2026-11-04T15:50:00",
            store_dir=self.store,
            ok=False,
            degraded=True,
            error="timeout",
        )
        calls = []

        def fetcher(code):
            calls.append(code)
            return [{"title": "新标题", "time": "2026-11-04 10:00:00"}]

        out = serve_watch_headlines(
            ["600000"],
            limit=3,
            store_dir=self.store,
            now=datetime(2026, 11, 4, 16, 0, 0),
            fetcher=fetcher,
            lexicon=_LEX,
        )
        self.assertEqual(calls, ["600000"])
        self.assertEqual(out["items"][0]["items"][0]["title"], "新标题")

    def test_poll_claim_blocks_concurrent_second(self):
        import sqlite3

        from core.news_archive import (
            _try_claim_poll_slot,
            maybe_poll_news_archive,
            news_db_path,
        )

        slot = "2026-11-04T09:20"
        self.assertTrue(
            _try_claim_poll_slot(
                slot,
                store_dir=self.store,
                ran_at="2026-11-04T09:20:00",
                now=datetime(2026, 11, 4, 9, 20),
            )
        )
        with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_POLL": "1"}):
            second = maybe_poll_news_archive(
                now=datetime(2026, 11, 4, 9, 21),
                store_dir=self.store,
                codes=["600519"],
                fetcher=lambda code: [{"title": "x", "time": "2026-11-04 08:00:00"}],
                holidays=_HOLIDAYS,
            )
        self.assertFalse(second["ran"])
        self.assertEqual(second["reason"], "already")
        conn = sqlite3.connect(news_db_path(self.store))
        try:
            reason = conn.execute(
                "SELECT stop_reason FROM poll_slot WHERE slot_key = ?",
                (slot,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(reason, "running")

    def test_stale_poll_claim_can_reclaim(self):
        from core.news_archive import _POLL_CLAIM_STALE_SEC, _try_claim_poll_slot

        slot = "2026-11-04T09:20"
        self.assertTrue(
            _try_claim_poll_slot(
                slot,
                store_dir=self.store,
                ran_at="2026-11-04T09:20:00",
                now=datetime(2026, 11, 4, 9, 20),
            )
        )
        from datetime import timedelta

        later = datetime(2026, 11, 4, 9, 20) + timedelta(
            seconds=_POLL_CLAIM_STALE_SEC + 1
        )
        self.assertTrue(
            _try_claim_poll_slot(
                slot,
                store_dir=self.store,
                ran_at=later.strftime("%Y-%m-%dT%H:%M:%S"),
                now=later,
            )
        )

    def test_corrupt_db_quarantined_on_create(self):
        from core.news_archive import ingest_title_rows, news_db_path

        path = news_db_path(self.store)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"not a sqlite database")
        out = ingest_title_rows(
            "600000",
            [{"title": "公司增持", "time": "2026-11-04 10:00:00"}],
            fetched_at="2026-11-04T16:00:00",
            store_dir=self.store,
        )
        self.assertEqual(out.get("n_inserted"), 1)
        self.assertTrue(os.path.isfile(path))
        corrupt = [
            name
            for name in os.listdir(self.store)
            if name.startswith("news.db.corrupt.")
        ]
        self.assertEqual(len(corrupt), 1)


class NewsArchiveScheduleTests(unittest.TestCase):
    def test_schedule_kind_disabled_in_tests(self):
        from core.platform.schedule_jobs import run_schedule

        with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_POLL": "0"}):
            out = run_schedule("news_archive_poll", codes=["600519"])
        self.assertFalse(out.get("ran"))
        self.assertEqual(out.get("reason"), "disabled")

    def test_watching_panel_has_news_archive_fold(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(
            os.path.join(root, "web", "static", "partials", "watching_panel.html"),
            encoding="utf-8",
        ) as f:
            html = f.read()
        with open(
            os.path.join(root, "web", "static", "js", "quant", "news_archive_ui.js"),
            encoding="utf-8",
        ) as f:
            js = f.read()
        self.assertIn("watching-news-archive-fold", html)
        self.assertIn("更新舆情", html)
        self.assertIn("与观察池同一本地库", html)
        news = html[html.find('id="watching-section-news-archive"') : html.find('id="watching-section-build-log"')]
        self.assertIn("watching-section-head", news)
        self.assertNotIn("<details", news)
        self.assertIn("历史回测按发布时间", js)
        self.assertLess(html.find(">新闻舆情<"), html.find(">建仓记录<"))
        self.assertLess(html.find(">建仓记录<"), html.find(">数据质量<"))
        self.assertNotIn("watching-news-detail", html[html.find("watching-news-archive-fold") :])
        self.assertIn("watching-news-archive-chip", html)
        self.assertIn("watching-news-archive-ran", html)
        self.assertIn("watching-news-archive-progress", html)
        self.assertIn("/api/news-archive/status", js)
        self.assertIn("/api/news-archive/poll", js)
        self.assertIn("/api/news-archive/poll/progress", js)
        self.assertIn("newsArchiveProgressHtml", js)
        self.assertIn("上次", js)
        self.assertIn("统计画像", js)
        self.assertNotIn("标题示例", js)
        self.assertNotIn("最多 40", js)
        self.assertIn("watching-news-samples-head", js)
        self.assertIn("watching-news-title", js)
        self.assertIn(">股票</span><span>舆情<", js)
        self.assertIn(">发布时间<", js)
        self.assertIn(">计入<", js)
        self.assertIn("watching-news-sample-date", js)
        self.assertIn("watching-news-sample-name", js)
        self.assertIn('type="search"', js)
        self.assertIn("watching-news-sample-suggest", js)
        self.assertIn('placeholder="股票名 / 代码"', js)
        self.assertNotIn("全部股票", js)
        self.assertNotIn("sampleOptions", js)
        self.assertNotIn("is-search", js)
        with open(
            os.path.join(root, "web", "static", "css", "watching.css"),
            encoding="utf-8",
        ) as f:
            css = f.read()
        self.assertNotIn("watching-news-status", css)
        self.assertNotIn("watching-news-portrait-title", css)
        self.assertIn(
            "body[data-page=\"watching\"] .watching-news-sample-field-control.is-date,\n"
            "body[data-page=\"watching\"] .watching-news-sample-field-control.is-name {\n"
            "  width: 11rem;\n"
            "}",
            css,
        )
        self.assertNotIn("watching-news-sample-wrap", css)

class TestNewsStorageUnify(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.store = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_headlines_as_of_and_sentiment(self):
        from core.news_archive import ingest_title_rows, load_headlines_as_of
        from core.sentiment import sentiment_as_of

        ingest_title_rows(
            "600000",
            [
                {"title": "业绩大增利好", "time": "2024-05-30"},
                {"title": "涉嫌违规处罚", "time": "2024-06-02"},
            ],
            fetched_at="2024-06-01T12:00:00",
            store_dir=self.store,
        )
        early = load_headlines_as_of("600000", "2024-06-01", store_dir=self.store)
        self.assertEqual(len(early), 1)
        self.assertIn("业绩", early[0]["title"])
        late = load_headlines_as_of("600000", "2024-06-03", store_dir=self.store)
        self.assertEqual(len(late), 2)
        with patch.dict(os.environ, {"QUANTLAB_NEWS_ARCHIVE_DIR": self.store}):
            asof = sentiment_as_of("600000", "2024-06-01")
        self.assertTrue(asof.get("ok"))
        self.assertEqual((asof.get("sentiment") or {}).get("n_titles"), 1)

    def test_migrate_jsonl_dry_run_and_apply(self):
        from scripts.migrate_news_jsonl_to_sqlite import migrate
        import json
        import sqlite3
        from core.news_archive import news_db_path

        hist = os.path.join(self.store, "history")
        os.makedirs(hist)
        path = os.path.join(hist, "600519.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "code": "600519",
                        "fetched_at": "2024-06-02T10:00:00",
                        "ok": True,
                        "titles": [
                            {"title": "茅台增持", "time": "2024-06-01", "source": "em"}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        with patch("scripts.migrate_news_jsonl_to_sqlite.NEWS_HISTORY_DIR", hist), patch(
            "scripts.migrate_news_jsonl_to_sqlite.NEWS_STORE_DIR", self.store
        ):
            dry = migrate(dry_run=True, store_dir=self.store)
            self.assertTrue(dry["ok"])
            self.assertEqual(dry["n_codes"], 1)
            self.assertEqual(dry["n_inserted"], 0)
            out = migrate(dry_run=False, store_dir=self.store)
        self.assertGreaterEqual(out["n_inserted"], 1)
        conn = sqlite3.connect(news_db_path(self.store))
        n = conn.execute("SELECT COUNT(*) FROM headline WHERE code=?", ("600519",)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)
