"""分钟线：远端失败时回退本地过期缓存。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
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
        stale_meta = {"data_source": "akshare:stock_zh_a_hist_min_em:5", "from_cache": True}

        def _load(market, code, period, **kw):
            if kw.get("ignore_age"):
                return list(stale_bars), dict(stale_meta)
            return None

        with patch.object(mh, "resolve_market_code", return_value=("CN", "600519")), patch.object(
            mh, "load_minute_cache", side_effect=_load
        ), patch.object(
            mh, "_fetch_em_minute_bars", return_value=([], {}, "em down")
        ), patch.object(
            mh, "_maybe_fetch_baostock_minute_bars", return_value=([], {})
        ), patch.object(
            mh, "_maybe_fetch_sina_minute_bars", return_value=([], {})
        ), patch.object(mh, "_throttle_minute_remote_fetch"):
            bars, meta = mh.fetch_a_minute_bars("600519", period="5", use_cache=True)
            self.assertEqual(len(bars), 12)
            self.assertTrue(meta.get("cache_stale"))
            self.assertIn("remote_error", meta)
            self.assertTrue(str(meta.get("data_source") or "").startswith("cache:stale"))

    def test_minute_em_lookback_policy(self):
        from core.data.policy import minute_em_lookback_days

        with patch.dict(os.environ):
            os.environ.pop("INVESTMENT_MINUTE_EM_LOOKBACK_DAYS", None)
            self.assertEqual(minute_em_lookback_days(), 120)
        with patch.dict(os.environ, {"INVESTMENT_MINUTE_EM_LOOKBACK_DAYS": "15"}):
            self.assertEqual(minute_em_lookback_days(), 15)
        with patch.dict(os.environ, {"INVESTMENT_MINUTE_EM_LOOKBACK_DAYS": "999"}):
            self.assertEqual(minute_em_lookback_days(), 120)

    def test_em_fetch_caps_at_120_calendar_days(self):
        from adapters.market import minute_history as mh

        captured: dict = {}

        class _Frame:
            def to_dict(self, orient="records"):
                return []

        class _Ak:
            def stock_zh_a_hist_min_em(self, **kw):
                captured.update(kw)
                return _Frame()

        with patch(
            "core.data.ak_lock.import_akshare", return_value=_Ak()
        ), patch(
            "core.http_retry.call_with_retry", side_effect=lambda fn, **_k: fn()
        ), patch.dict(os.environ):
            os.environ.pop("INVESTMENT_MINUTE_EM_LOOKBACK_DAYS", None)
            mh._fetch_em_minute_bars("601988", period="5", lookback_days=200, adjust="qfq")
        expected_day = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        self.assertEqual(captured.get("symbol"), "601988")
        self.assertEqual(captured.get("period"), "5")
        self.assertEqual(captured.get("adjust"), "qfq")
        self.assertTrue(str(captured.get("start_date") or "").startswith(expected_day))
        self.assertTrue(str(captured.get("end_date") or "").startswith(datetime.now().strftime("%Y-%m-%d")))

    def test_em_retry_waits_fetch_gap(self):
        from adapters.market import minute_history as mh

        captured: dict = {}

        def _capture(fn, **kw):
            captured.update(kw)
            return []

        with patch(
            "core.http_retry.call_with_retry", side_effect=_capture
        ), patch.dict(os.environ, {"INVESTMENT_MINUTE_FETCH_DELAY_SEC": "10"}):
            mh._fetch_em_minute_bars("601988", period="5", lookback_days=30, adjust="qfq")
        self.assertEqual(captured.get("retries"), 1)
        self.assertGreaterEqual(float(captured.get("base_delay_sec") or 0), 10.0)
        self.assertGreaterEqual(
            float(captured.get("max_delay_sec") or 0),
            float(captured.get("base_delay_sec") or 0),
        )

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

        with patch("core.data.ak_lock.import_akshare", return_value=_Ak()), patch(
            "adapters.market.sina_minute.fetch_sina_tx_minute_bars"
        ) as old:
            bars, meta = mh._maybe_fetch_sina_minute_bars(
                "600519", period="5", lookback_days=30
            )
        old.assert_not_called()
        self.assertEqual(calls.get("symbol"), "sh600519")
        self.assertEqual(calls.get("period"), "5")
        self.assertEqual(calls.get("adjust"), "")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 10.5)
        self.assertEqual(meta.get("data_source"), "akshare:stock_zh_a_minute")


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
                adjust_policy="qfq",
            )
        self.assertEqual(len(out), 1)
        self.assertTrue(meta.get("cached"))
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
