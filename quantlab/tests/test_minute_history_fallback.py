"""分钟线：新浪拉取失败时回退本地过期缓存。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestMinuteStaleFallback(unittest.TestCase):
    def test_remote_fail_uses_stale_cache(self):
        from adapters.market import minute_history as mh

        stale_bars = [
            {
                "datetime": "2026-08-20 09:35:00",
                "date": "2026-08-20",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
            }
        ] * 12
        stale_meta = {"data_source": "akshare:stock_zh_a_minute", "from_cache": True}

        def _load(market, code, period, **kw):
            if kw.get("ignore_age"):
                return list(stale_bars), dict(stale_meta)
            return None

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", side_effect=_load
        ), patch.object(
            mh, "_fetch_sina_minute_bars", return_value=([], {"error": "sina down"})
        ), patch.object(mh, "_throttle_minute_remote_fetch"):
            bars, meta = mh.fetch_a_minute_bars("600519", period="5", use_cache=True)
            self.assertEqual(len(bars), 12)
            self.assertTrue(meta.get("cache_stale"))
            self.assertIn("remote_error", meta)
            self.assertTrue(str(meta.get("data_source") or "").startswith("cache:stale"))

    def test_minute_lookback_policy(self):
        from core.data.policy import minute_lookback_days

        with patch.dict(os.environ):
            os.environ.pop("QUANTLAB_MINUTE_LOOKBACK_DAYS", None)
            self.assertEqual(minute_lookback_days(), 45)
        with patch.dict(os.environ, {"QUANTLAB_MINUTE_LOOKBACK_DAYS": "15"}):
            self.assertEqual(minute_lookback_days(), 15)
        with patch.dict(os.environ, {"QUANTLAB_MINUTE_LOOKBACK_DAYS": "999"}):
            self.assertEqual(minute_lookback_days(), 60)

    def test_minute_update_uses_stock_zh_a_minute(self):
        from adapters.market import minute_history as mh

        calls: dict = {}

        class _Ak:
            def stock_zh_a_minute(self, symbol, period, adjust):
                calls["symbol"] = symbol
                calls["period"] = period
                calls["adjust"] = adjust

                class _Frame:
                    def to_dict(self, orient="records"):
                        return [
                            {
                                "day": "2026-08-28 09:35:00",
                                "open": "10",
                                "high": "11",
                                "low": "9",
                                "close": "10.5",
                                "volume": "100",
                            }
                        ]

                return _Frame()

        with patch("core.data.ak_lock.import_akshare", return_value=_Ak()):
            bars, meta = mh._fetch_sina_minute_bars("600519")
        self.assertEqual(calls.get("symbol"), "sh600519")
        self.assertEqual(calls.get("period"), "5")
        self.assertEqual(calls.get("adjust"), "")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 10.5)
        self.assertEqual(meta.get("data_source"), "akshare:stock_zh_a_minute")

    def test_fetch_uses_sina_only(self):
        from adapters.market import minute_history as mh

        bar = {
            "datetime": "2026-08-28 09:35:00",
            "date": "2026-08-28",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 100,
        }
        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(
            mh,
            "_fetch_sina_minute_bars",
            return_value=([bar], {"data_source": "akshare:stock_zh_a_minute"}),
        ), patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh,
            "_merge_save_minute_bars",
            return_value=([bar], {"data_source": "akshare:stock_zh_a_minute", "bar_count": 1}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        self.assertEqual(len(bars), 1)
        self.assertIn("stock_zh_a_minute", meta.get("data_source") or "")
        self.assertEqual(
            (meta.get("sina") or {}).get("data_source"), "akshare:stock_zh_a_minute"
        )

    def test_period_forced_to_five(self):
        from adapters.market import minute_history as mh

        calls = []

        def _sina(bare):
            calls.append(bare)
            return [], {"error": "empty", "period": "5"}

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", return_value=None
        ), patch.object(mh, "_fetch_sina_minute_bars", side_effect=_sina), patch.object(
            mh, "_throttle_minute_remote_fetch"
        ), patch.object(mh, "_load_stale_minute", return_value=None):
            _, meta = mh.fetch_a_minute_bars(
                "600519", period="15", use_cache=False, max_age_hours=0
            )
        self.assertEqual(calls, ["600519"])
        self.assertEqual(meta.get("period"), "5")


class TestMergeSaveAlwaysCaches(unittest.TestCase):
    def test_merge_save_writes_even_without_use_cache_arg(self):
        """远端成功后始终落盘（入口 use_cache=False 也写）。"""
        from adapters.market import minute_history as mh

        bars = [
            {
                "datetime": "2026-09-01 10:00:00",
                "date": "2026-09-01",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
            }
        ]
        with patch("adapters.market.minute_history.load_minute_cache", return_value=None), patch(
            "adapters.market.minute_history.save_minute_cache"
        ) as save:
            out, meta = mh._merge_save_minute_bars(
                "CN",
                "002415",
                bars,
                period="5",
                data_source="test",
                adjust_policy=None,
            )
        self.assertEqual(len(out), 1)
        self.assertTrue(meta.get("cached"))
        save.assert_called_once()
