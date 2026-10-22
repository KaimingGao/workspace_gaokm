"""新浪日线开工闸：同时在途上限，以及两次开工的最小间隔。"""

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from adapters.market.sina_pace import sina_daily_slot
from core.data.policy import minute_fetch_delay_sec


class TestSinaDailyPace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "gate.json")
        self.env = patch.dict(
            os.environ,
            {
                "QUANTLAB_SINA_DAILY_MAX_INFLIGHT": "2",
                "QUANTLAB_SINA_DAILY_PACE_SEC": "0",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_caps_inflight_at_two(self) -> None:
        overlap = 0
        max_overlap = 0
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def _run() -> None:
            nonlocal overlap, max_overlap
            barrier.wait()
            with sina_daily_slot(gate_path=self.path):
                with lock:
                    overlap += 1
                    max_overlap = max(max_overlap, overlap)
                time.sleep(0.2)
                with lock:
                    overlap -= 1

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)
        self.assertTrue(all(not th.is_alive() for th in threads))
        self.assertEqual(max_overlap, 2)

    def test_paces_starts(self) -> None:
        os.environ["QUANTLAB_SINA_DAILY_PACE_SEC"] = "0.3"
        starts: list[float] = []
        lock = threading.Lock()

        def _run() -> None:
            with sina_daily_slot(gate_path=self.path):
                with lock:
                    starts.append(time.monotonic())

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)
        self.assertEqual(len(starts), 4)
        ordered = sorted(starts)
        gaps = [b - a for a, b in zip(ordered, ordered[1:])]
        self.assertTrue(all(gap >= 0.25 for gap in gaps), gaps)

    def test_dead_pid_slot_is_reclaimed(self) -> None:
        os.environ["QUANTLAB_SINA_DAILY_MAX_INFLIGHT"] = "1"
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "last_start": time.time(),
                    "holders": [{"id": "stale", "pid": 2**31 - 1, "ts": time.time()}],
                },
                fh,
            )
        t0 = time.monotonic()
        with sina_daily_slot(gate_path=self.path):
            pass
        self.assertLess(time.monotonic() - t0, 0.8)


class TestMinuteFetchDelay(unittest.TestCase):
    def test_default_is_ten_seconds(self) -> None:
        env = os.environ.copy()
        env.pop("QUANTLAB_MINUTE_FETCH_DELAY_SEC", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(minute_fetch_delay_sec(), 10.0)
