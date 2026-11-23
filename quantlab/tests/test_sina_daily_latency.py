"""新浪日线单次耗时样本：接口时间和排队分开统计。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from adapters.market.sina_latency import (
    record_sina_daily_sample,
    sina_daily_latency_snapshot,
)


class TestSinaDailyLatency(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "latency.json")
        self.env = patch.dict(
            os.environ, {"QUANTLAB_SINA_DAILY_LATENCY_PATH": self.path}
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_snapshot_splits_http_and_queue(self) -> None:
        record_sina_daily_sample(http_s=2.0, wait_s=0.2, outcome="ok")
        record_sina_daily_sample(http_s=4.0, wait_s=1.0, outcome="ok")
        record_sina_daily_sample(http_s=12.0, wait_s=0.4, outcome="timeout")
        record_sina_daily_sample(http_s=None, wait_s=120.0, outcome="slot_wait")
        snap = sina_daily_latency_snapshot()
        self.assertEqual(snap["n"], 4)
        self.assertEqual(snap["ok"], 2)
        self.assertEqual(snap["timeout"], 1)
        self.assertEqual(snap["slot_wait"], 1)
        self.assertEqual(snap["http_p50_s"], 4.0)
        self.assertEqual(snap["http_max_s"], 12.0)
        self.assertEqual(snap["last"]["outcome"], "slot_wait")
        self.assertGreater(snap["wait_p95_s"], 10.0)

    def test_fetch_records_http_timeout(self) -> None:
        from types import ModuleType
        from contextlib import nullcontext

        from adapters.market.history import fetch_a_daily_bars

        def _daily(symbol=None, adjust="", **kwargs):
            raise TimeoutError("connect timed out")

        fake = ModuleType("akshare")
        fake.stock_zh_a_daily = _daily
        with patch(
            "adapters.market.history.sina_daily_slot",
            return_value=nullcontext(),
        ), patch.dict(__import__("sys").modules, {"akshare": fake}):
            bars, got = fetch_a_daily_bars("300750", limit=10, adjust="qfq")
        self.assertEqual(bars, [])
        self.assertEqual(got, "")
        snap = sina_daily_latency_snapshot()
        self.assertEqual(snap["n"], 1)
        self.assertEqual(snap["timeout"], 1)
        self.assertEqual(snap["last"]["outcome"], "timeout")
