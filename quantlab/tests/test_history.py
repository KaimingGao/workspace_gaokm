import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.market.history import fetch_daily_bars, resolve_market_code


class TestHistoryResolve(unittest.TestCase):
    def test_us(self):
        market, code = resolve_market_code("AAPL")
        self.assertEqual(market, "US")
        self.assertEqual(code, "AAPL")

    def test_fetch_us_mocked(self):
        bars = [
            {"date": "2026-01-01", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 10},
            {"date": "2026-01-02", "open": 1.5, "high": 2, "low": 1.4, "close": 1.8, "volume": 12},
        ]
        with patch(
            "adapters.market.history.fetch_us_daily_bars", return_value=bars
        ), patch(
            "adapters.market.history.resolve_market_code", return_value=("US", "AAPL")
        ):
            out, src = fetch_daily_bars("AAPL", limit=10)
        self.assertEqual(src, "akshare_us_daily")
        self.assertEqual(len(out), 2)

    def test_offline_ok_returns_stale_cache_without_network(self):
        stale_bars = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 10,
            }
            for i in range(1, 50)
        ]
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "adapters.market.history.load_daily_cache",
            side_effect=[
                None,  # fresh miss
                (stale_bars, {"data_source": "akshare_cn_daily:qfq"}),  # ignore_age
            ],
        ), patch(
            "adapters.market.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars(
                "600519", limit=40, offline_ok=True, incremental=False
            )
        self.assertEqual(len(out), 40)
        self.assertTrue(str(src).startswith("cache"))

    def test_offline_only_skips_network_on_cache_miss(self):
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "adapters.market.history.load_daily_cache",
            return_value=None,
        ), patch(
            "adapters.market.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars(
                "600519", limit=40, offline_only=True, incremental=False
            )
        self.assertEqual(out, [])
        self.assertEqual(src, "empty")

    def test_offline_only_returns_short_cache_without_network(self):
        short = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 10,
            }
            for i in range(1, 8)
        ]
        with patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "core.store.peek_daily_cache_meta",
            return_value={"adjust_policy": "qfq"},
        ), patch(
            "adapters.market.history.load_daily_cache",
            return_value=(short, {"data_source": "akshare_cn_daily:qfq"}),
        ), patch(
            "adapters.market.history.fetch_a_daily_bars",
            side_effect=AssertionError("should not hit network"),
        ):
            out, src = fetch_daily_bars("600519", limit=40, offline_only=True)
        self.assertEqual(len(out), 7)
        self.assertTrue(str(src).startswith("cache"))

    def test_fetch_a_daily_falls_back_to_sina(self):
        from types import ModuleType

        from adapters.market.history import fetch_a_daily_bars

        class _DF:
            empty = False

            def to_dict(self, orient="records"):
                return [
                    {
                        "date": "2026-01-01",
                        "open": 100,
                        "high": 102,
                        "low": 99,
                        "close": 101,
                        "volume": 1000,
                    },
                    {
                        "date": "2026-01-02",
                        "open": 101,
                        "high": 105,
                        "low": 100,
                        "close": 104,
                        "volume": 1200,
                    },
                ]

        fake = ModuleType("akshare")

        def _hist(**kwargs):
            raise RuntimeError("hist down")

        def _daily(symbol=None, **kwargs):
            return _DF()

        fake.stock_zh_a_hist = _hist
        fake.stock_zh_a_daily = _daily
        with patch.dict(sys.modules, {"akshare": fake}):
            bars = fetch_a_daily_bars("300750", limit=10)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1]["close"], 104.0)

    def test_eval_required_paths_use_stocks(self):
        from evals.extract import check_required

        bundled = {
            "success": True,
            "stocks": [{"stock_code": "1"}, {"stock_code": "2"}],
        }
        self.assertEqual(
            check_required(bundled, ["success", "stocks"], min_items=2),
            [],
        )


class TestQfqReplace(unittest.TestCase):
    def setUp(self):
        import tempfile

        from core.store_bars_sqlite import reset_conn_cache

        self.tmp = tempfile.mkdtemp()
        self._env = patch.dict(
            os.environ,
            {"QUANTLAB_STORE_DIR": self.tmp, "QUANTLAB_BARS_BACKEND": "sqlite"},
        )
        self._env.start()
        reset_conn_cache()
        self.addCleanup(self._env.stop)
        self.addCleanup(reset_conn_cache)

    def _seed(self, n=10, *, adjust="qfq", end_offset=10, close0=10.0):
        from datetime import datetime, timedelta

        from core.store import save_daily_cache

        end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=end_offset
        )
        bars = []
        for i in range(n):
            d = end - timedelta(days=(n - 1 - i))
            px = close0 + i
            bars.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "open": px,
                    "high": px + 0.4,
                    "low": px - 0.4,
                    "close": px,
                    "volume": 1000 + i,
                }
            )
        save_daily_cache(
            "CN",
            "600519",
            bars,
            data_source="akshare_cn_daily",
            store_dir=self.tmp,
            adjust_policy=adjust,
        )
        return bars

    def _load(self):
        from core.store import load_daily_cache

        loaded = load_daily_cache(
            "CN", "600519", min_bars=1, ignore_age=True, store_dir=self.tmp
        )
        self.assertIsNotNone(loaded)
        return loaded[0]

    def test_same_overlap_merges_tail_only(self):
        from datetime import datetime, timedelta

        existing = self._seed()
        overlap = dict(existing[-1])
        overlap["volume"] = 99999
        nxt = datetime.strptime(existing[-1]["date"], "%Y-%m-%d") + timedelta(days=1)
        newer = {
            "date": nxt.strftime("%Y-%m-%d"),
            "open": 30.0,
            "high": 31.0,
            "low": 29.0,
            "close": 30.5,
            "volume": 50,
        }
        calls = []

        def _fake(code, limit=30, adjust="qfq", start_date=None):
            calls.append(start_date)
            return [overlap, newer]

        with patch("adapters.market.history.fetch_a_daily_bars", side_effect=_fake):
            out, src = fetch_daily_bars(
                "600519", limit=10, cache_max_age_hours=0, incremental=True
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0])
        self.assertIn("akshare", src)
        got = self._load()
        self.assertEqual(got[0]["date"], existing[0]["date"])
        self.assertEqual(got[-1]["date"], newer["date"])
        self.assertEqual(len(got), len(existing) + 1)
        self.assertTrue(out)

    def test_rebased_overlap_replaces_series(self):
        existing = self._seed()
        shifted = [dict(b) for b in existing[1:]]
        shifted[-1]["close"] = float(shifted[-1]["close"]) + 5.0
        calls = []

        def _fake(code, limit=30, adjust="qfq", start_date=None):
            calls.append(start_date)
            if len(calls) == 1:
                return [dict(shifted[-1])]
            return shifted

        with patch("adapters.market.history.fetch_a_daily_bars", side_effect=_fake):
            _out, src = fetch_daily_bars(
                "600519", limit=10, cache_max_age_hours=0, incremental=True
            )
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1])
        self.assertIn("akshare", src)
        got = self._load()
        dates = [b["date"] for b in got]
        self.assertEqual(dates[0], shifted[0]["date"])
        self.assertNotIn(existing[0]["date"], dates)

    def test_short_rebase_refetch_does_not_wipe(self):
        existing = self._seed()
        bumped = dict(existing[-1])
        bumped["close"] = float(bumped["close"]) + 5.0
        calls = []

        def _fake(code, limit=30, adjust="qfq", start_date=None):
            calls.append((start_date, limit))
            if len(calls) == 1:
                return [bumped]
            return [bumped]

        with patch("adapters.market.history.fetch_a_daily_bars", side_effect=_fake):
            _out, src = fetch_daily_bars(
                "600519", limit=10, cache_max_age_hours=0, incremental=True
            )
        self.assertEqual(src, "cache:stale")
        self.assertEqual(len(calls), 2)
        got = self._load()
        self.assertEqual(len(got), len(existing))
        self.assertEqual(got[0]["date"], existing[0]["date"])
        self.assertAlmostEqual(got[-1]["close"], existing[-1]["close"])

    def test_raw_overlap_price_change_does_not_replace(self):
        existing = self._seed(adjust="raw")
        bumped = dict(existing[-1])
        bumped["close"] = float(bumped["close"]) + 5.0
        calls = []

        def _fake(code, limit=30, adjust="qfq", start_date=None):
            calls.append(start_date)
            return [bumped]

        with patch("adapters.market.history.fetch_a_daily_bars", side_effect=_fake):
            _out, src = fetch_daily_bars(
                "600519",
                limit=10,
                cache_max_age_hours=0,
                incremental=True,
                adjust="raw",
            )
        self.assertEqual(len(calls), 1)
        self.assertIn("akshare", src)
        got = self._load()
        self.assertEqual(got[0]["date"], existing[0]["date"])
        self.assertAlmostEqual(got[-1]["close"], bumped["close"])


if __name__ == "__main__":
    unittest.main()
