"""M1 数据收集：DataService / coverage / snapshot cache / incremental bars。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestMergeBars(unittest.TestCase):
    def test_merge_by_date(self):
        from core.store import merge_bars_by_date

        a = [{"date": "2024-01-01", "close": 1}, {"date": "2024-01-02", "close": 2}]
        b = [{"date": "2024-01-02", "close": 2.5}, {"date": "2024-01-03", "close": 3}]
        m = merge_bars_by_date(a, b)
        self.assertEqual([x["date"] for x in m], ["2024-01-01", "2024-01-02", "2024-01-03"])
        self.assertEqual(m[1]["close"], 2.5)


class TestSnapshotCache(unittest.TestCase):
    def test_roundtrip_ttl(self):
        from core import store as st

        with tempfile.TemporaryDirectory() as td:
            path = st.save_snapshot_cache(
                "fundamentals",
                "600519",
                {"success": True, "metrics": {"pe": 20}},
                data_source="test",
                store_dir=td,
            )
            self.assertTrue(os.path.isfile(path))
            hit = st.load_snapshot_cache(
                "fundamentals", "600519", max_age_hours=24, store_dir=td
            )
            self.assertIsNotNone(hit)
            data, meta = hit
            self.assertTrue(data.get("success"))
            self.assertTrue(meta.get("non_pit"))

    def test_missing_data_key_is_miss(self):
        from core import store as st

        with tempfile.TemporaryDirectory() as td:
            path = st.snapshot_cache_path("news", "300750", td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ok": True,
                        "stock_code": "300750",
                        "items": [],
                        "fetched_at": 1,
                    },
                    f,
                )
            self.assertIsNone(
                st.load_snapshot_cache("news", "300750", max_age_hours=24, store_dir=td)
            )


class TestDataServiceWrappers(unittest.TestCase):
    def test_get_bars_shape(self):
        from core.data.facade import get_bars

        fake_bars = [
            {"date": f"2024-01-{i:02d}", "open": 10, "high": 11, "low": 9, "close": 10 + i * 0.1, "volume": 1}
            for i in range(1, 21)
        ]
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(fake_bars, "mock"),
        ):
            pack = get_bars("600519", limit=20, as_of="2024-01-10")
        self.assertEqual(pack["stock_code"], "600519")
        self.assertTrue(pack["pit"]["bars_pit"])
        self.assertEqual(len(pack["bars"]), 10)
        self.assertIn("production_ok", pack)

    def test_get_fundamentals_cache(self):
        from core.data import facade as ds

        with patch(
            "core.store.load_snapshot_cache",
            return_value=(
                {"success": True, "metrics": {"pe": 12}},
                {"fetched_at": "2024-01-01T00:00:00", "data_source": "mock"},
            ),
        ), patch("core.ports.market.build_fundamentals") as live:
            pack = ds.get_fundamentals("600519", use_cache=True)
            self.assertTrue(pack.get("cache_hit"))
            self.assertTrue(pack.get("non_pit"))
            self.assertFalse(pack.get("fundamentals_pit"))
            live.assert_not_called()

    def test_get_news_skips_sentiment_ttl_file(self):
        from core.data.facade import get_news

        with tempfile.TemporaryDirectory() as td:
            news_ttl = os.path.join(td, "news", "300750.json")
            os.makedirs(os.path.dirname(news_ttl), exist_ok=True)
            with open(news_ttl, "w", encoding="utf-8") as f:
                json.dump({"ok": True, "items": [], "fetched_at": 1}, f)
            live = {
                "success": True,
                "stock_code": "300750",
                "stock_name": "宁德时代",
                "items": [
                    {"title": "宁德时代公告", "time": "t", "source": "s", "url": ""},
                ],
            }
            with patch("core.store.get_store_dir", return_value=td), patch(
                "core.ports.market.build_news", return_value=live
            ):
                out = get_news("300750", limit=5)
            self.assertTrue(out.get("success"))
            self.assertFalse(out.get("cache_hit"))
            self.assertEqual(len(out.get("items") or []), 1)
            snap = os.path.join(td, "news_snap", "300750.json")
            self.assertTrue(os.path.isfile(snap))


class TestDataCoverage(unittest.TestCase):
    def test_empty_universe(self):
        from core.data.coverage import build_data_coverage

        with patch("core.data.coverage.universe_codes", return_value=[]):
            cov = build_data_coverage([])
        self.assertEqual(cov["total"], 0)
        self.assertEqual(cov["coverage"], None)
        self.assertTrue(cov.get("empty_universe"))

    def test_missing_codes_alert(self):
        from core.data.coverage import build_data_coverage

        with patch("core.data.coverage.universe_codes", return_value=["600519", "300750"]), patch(
            "core.ports.market.resolve_market_code",
            side_effect=lambda c: ("CN", c),
        ), patch("core.store.peek_daily_cache_meta", return_value=None):
            cov = build_data_coverage(["600519", "300750"])
        self.assertEqual(cov["missing"], 2)
        self.assertTrue(any(a["code"] == "bars_coverage_thin" for a in cov["alerts"]))


class TestIncrementalFetch(unittest.TestCase):
    def test_merge_on_fetch(self):
        from adapters.market import history as hist

        existing = [
            {"date": "2024-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"date": "2024-01-02", "open": 1, "high": 1, "low": 1, "close": 1.1, "volume": 1},
        ]
        incoming = [
            {"date": "2024-01-02", "open": 1, "high": 1, "low": 1, "close": 1.2, "volume": 1},
            {"date": "2024-01-03", "open": 1, "high": 1, "low": 1, "close": 1.3, "volume": 1},
        ]
        with tempfile.TemporaryDirectory() as td, patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.market.history.load_daily_cache",
            side_effect=[
                None,  # fresh miss
                (existing, {"data_source": "akshare_cn_daily"}),  # ignore_age
            ],
        ), patch(
            "adapters.market.history.fetch_a_daily_bars", return_value=incoming
        ) as fetch, patch(
            "adapters.market.history.merge_save_daily_cache"
        ) as save, patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch.dict(os.environ, {"INVESTMENT_DISABLE_CACHE": ""}):
            def _ms(market, code, bars, **kwargs):
                from core.store import merge_bars_by_date

                merged = merge_bars_by_date(existing, bars)
                return "/tmp/x", merged

            save.side_effect = _ms
            bars, src = hist.fetch_daily_bars(
                "600519",
                limit=10,
                use_cache=True,
                cache_max_age_hours=24,
                incremental=True,
            )
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[-1]["date"], "2024-01-03")
        self.assertTrue(save.called)
        # thin local history → full window (start_date=None)
        fetch.assert_called()
        kwargs = fetch.call_args.kwargs
        self.assertIsNone(kwargs.get("start_date"))

    def test_gap_window_when_history_enough(self):
        from adapters.market import history as hist

        existing = [
            {
                "date": f"2024-01-{i:02d}",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": float(i),
                "volume": 1,
            }
            for i in range(1, 26)
        ]
        # last local day well before "now"; patch plan via real dates on bars
        existing[-1]["date"] = "2026-08-08"
        incoming = [
            {"date": "2026-08-08", "open": 1, "high": 1, "low": 1, "close": 25.5, "volume": 1},
            {"date": "2026-08-11", "open": 1, "high": 1, "low": 1, "close": 26.0, "volume": 1},
        ]
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.market.history.load_daily_cache",
            side_effect=[
                None,
                (existing, {"data_source": "akshare_cn_daily"}),
            ],
        ), patch(
            "adapters.market.history.fetch_a_daily_bars", return_value=incoming
        ) as fetch, patch(
            "adapters.market.history.merge_save_daily_cache",
            side_effect=lambda market, code, bars, **kw: ("/tmp/x", bars),
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch.dict(os.environ, {"INVESTMENT_DISABLE_CACHE": ""}):
            bars, _src = hist.fetch_daily_bars(
                "600519",
                limit=20,
                use_cache=True,
                cache_max_age_hours=24,
                incremental=True,
            )
        self.assertEqual(bars[-1]["date"], "2026-08-11")
        kwargs = fetch.call_args.kwargs
        self.assertEqual(kwargs.get("start_date"), "20260808")
        self.assertLessEqual(kwargs.get("limit", 99), 20)

    def test_skip_remote_when_already_today(self):
        from adapters.market import history as hist
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        existing = [
            {
                "date": f"2024-01-{i:02d}",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": float(i),
                "volume": 1,
            }
            for i in range(1, 26)
        ]
        existing[-1]["date"] = today
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.market.history.load_daily_cache",
            side_effect=[
                None,
                (existing, {"data_source": "akshare_cn_daily"}),
            ],
        ), patch(
            "adapters.market.history.fetch_a_daily_bars"
        ) as fetch, patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch.dict(os.environ, {"INVESTMENT_DISABLE_CACHE": ""}):
            bars, src = hist.fetch_daily_bars(
                "600519",
                limit=20,
                use_cache=True,
                cache_max_age_hours=24,
                incremental=True,
            )
        fetch.assert_not_called()
        self.assertEqual(bars[-1]["date"], today)
        self.assertIn("cache", src)

    def test_plan_gap_vs_full(self):
        from adapters.market.history import _incremental_remote_plan

        # 25 根 + 缺口约 3 天，补上后够 limit=20 → 缺口窗
        fat = [{"date": f"2026-07-{i:02d}", "close": 1} for i in range(1, 26)]
        fat[-1]["date"] = "2026-08-08"
        start, lim, skip = _incremental_remote_plan(fat, limit=20, adjust="qfq")
        self.assertEqual(start, "20260808")
        self.assertFalse(skip)
        self.assertLessEqual(lim, 20)

        thin = [{"date": "2026-08-01", "close": 1}]
        start2, _lim2, skip2 = _incremental_remote_plan(thin, limit=30, adjust="qfq")
        self.assertIsNone(start2)
        self.assertFalse(skip2)

    def test_plan_skip_when_last_meets_asof_on_weekend(self):
        """周日增量：末根已是周五完整 as-of → 不打远端。"""
        from datetime import datetime

        from adapters.market.history import _incremental_remote_plan

        fat = [{"date": f"2026-08-{i:02d}", "close": 1} for i in range(1, 26)]
        fat[-1]["date"] = "2026-09-04"
        sunday = datetime(2026, 9, 6, 1, 30, 0)
        with patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-09-04",
        ):
            start, lim, skip = _incremental_remote_plan(
                fat, limit=20, adjust="qfq", now=sunday
            )
        self.assertTrue(skip)
        self.assertIsNone(start)
        self.assertEqual(lim, 0)

    def test_plan_fetch_when_last_before_asof(self):
        from datetime import datetime

        from adapters.market.history import _incremental_remote_plan

        fat = [{"date": f"2026-08-{i:02d}", "close": 1} for i in range(1, 26)]
        fat[-1]["date"] = "2026-09-03"
        sunday = datetime(2026, 9, 6, 1, 30, 0)
        with patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-09-04",
        ):
            start, lim, skip = _incremental_remote_plan(
                fat, limit=20, adjust="qfq", now=sunday
            )
        self.assertFalse(skip)
        self.assertEqual(start, "20260903")

    def test_force_refresh_skips_remote_when_asof_aligned(self):
        """cache_max_age<=0 的增量补齐：已齐 as-of 不得整池拉网。"""
        from adapters.market import history as hist

        existing = [
            {
                "date": f"2026-08-{i:02d}",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": float(i),
                "volume": 1,
            }
            for i in range(1, 26)
        ]
        existing[-1]["date"] = "2026-09-04"
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "adapters.market.history.load_daily_cache",
            return_value=(existing, {"data_source": "akshare_cn_daily:qfq"}),
        ), patch(
            "adapters.market.history.fetch_a_daily_bars"
        ) as fetch, patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-09-04",
        ), patch.dict(os.environ, {"INVESTMENT_DISABLE_CACHE": ""}):
            bars, src = hist.fetch_daily_bars(
                "600519",
                limit=20,
                use_cache=True,
                cache_max_age_hours=0,
                incremental=True,
            )
        fetch.assert_not_called()
        self.assertEqual(bars[-1]["date"], "2026-09-04")
        self.assertIn("cache", src)


if __name__ == "__main__":
    unittest.main()
