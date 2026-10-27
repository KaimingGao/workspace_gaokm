"""分钟线：新浪拉取失败时回退本地过期缓存。"""

from __future__ import annotations

import multiprocessing as mp
import time
import unittest
from unittest.mock import patch


def _put_oversized_minute_result(out_queue) -> None:
    """大于管道缓冲的结果。先 join 再读会把子进程卡死。"""
    out_queue.put(("ok", [{"i": i, "close": 1.0} for i in range(4000)], {"ok": True}))


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

    def test_minute_fetch_hits_kline_not_daily_qfq(self):
        from adapters.market import minute_history as mh

        body = (
            '/*<script>location.href=\'//sina.com\';</script>*/ '
            '=(['
            '{"day":"2026-08-28 09:35:00","open":"10","high":"11",'
            '"low":"9","close":"10.5","volume":"100","amount":"1000"}'
            ']);'
        )

        class _Resp:
            text = body

            def raise_for_status(self):
                return None

        with patch("adapters.market.minute_history.requests.get", return_value=_Resp()) as get, patch(
            "core.data.ak_lock.import_akshare"
        ) as ak:
            bars, meta = mh._fetch_sina_minute_bars("600519")
        ak.assert_not_called()
        self.assertEqual(get.call_count, 1)
        self.assertIn("getKLineData", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["params"]["symbol"], "sh600519")
        self.assertEqual(get.call_args.kwargs["params"]["scale"], "5")
        self.assertEqual(get.call_args.kwargs["params"]["datalen"], "1970")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 10.5)
        self.assertEqual(meta.get("data_source"), "sina:getKLineData")
        self.assertEqual(meta.get("adjust"), "")

    def test_minute_fetch_uses_fallback_url(self):
        from adapters.market import minute_history as mh

        body = (
            '=(['
            '{"day":"2026-08-28 09:35:00","open":"10","high":"11",'
            '"low":"9","close":"10.5","volume":"100"}'
            ']);'
        )

        class _Ok:
            text = body

            def raise_for_status(self):
                return None

        calls = {"n": 0}

        def _get(url, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("primary down")
            return _Ok()

        with patch("adapters.market.minute_history.requests.get", side_effect=_get) as get:
            bars, meta = mh._fetch_sina_minute_bars("000001")
        self.assertEqual(calls["n"], 2)
        self.assertIn("var%20_sz000001_5_", get.call_args.args[0])
        self.assertEqual(len(bars), 1)
        self.assertEqual(meta.get("symbol"), "sz000001")
        self.assertEqual(meta.get("data_source"), "sina:getKLineData")

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
            return_value=([bar], {"data_source": "sina:getKLineData"}),
        ), patch.object(mh, "_throttle_minute_remote_fetch"), patch.object(
            mh,
            "_merge_save_minute_bars",
            return_value=([bar], {"data_source": "sina:getKLineData", "bar_count": 1}),
        ):
            bars, meta = mh.fetch_a_minute_bars("600519", use_cache=False, max_age_hours=0)
        self.assertEqual(len(bars), 1)
        self.assertEqual(meta.get("data_source"), "sina:getKLineData")
        self.assertEqual((meta.get("sina") or {}).get("data_source"), "sina:getKLineData")

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

class TestIsolatedAckSkipsBars(unittest.TestCase):
    def test_ack_carries_count_not_bars(self):
        from adapters.market.minute_history import _minute_isolated_ack

        bars = [{"datetime": "2026-08-28 09:35:00", "close": 1.0} for _ in range(2000)]
        status, count, meta = _minute_isolated_ack(
            bars, {"ok": True, "bar_count": 2000, "sina": {"raw": "x" * 1000}}
        )
        self.assertEqual(status, "ok")
        self.assertEqual(count, 2000)
        self.assertEqual(meta.get("bar_count"), 2000)
        self.assertNotIn("sina", meta)
        self.assertNotIn("bars", meta)


class TestIsolatedQueueDoesNotDeadlock(unittest.TestCase):
    def test_large_payload_returns_before_timeout(self):
        from adapters.market.minute_history import _recv_child_queue

        ctx = mp.get_context("spawn")
        out_queue = ctx.Queue()
        proc = ctx.Process(target=_put_oversized_minute_result, args=(out_queue,))
        proc.start()
        t0 = time.perf_counter()
        item, timed_out = _recv_child_queue(proc, out_queue, 8.0)
        elapsed = time.perf_counter() - t0
        self.assertFalse(timed_out)
        self.assertIsInstance(item, tuple)
        self.assertEqual(item[0], "ok")
        self.assertEqual(len(item[1]), 4000)
        self.assertLess(elapsed, 8.0)
        self.assertFalse(proc.is_alive())


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
