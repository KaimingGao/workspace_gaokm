"""稳定性落地：文件锁 / trades 裁剪 / HTTP 重试 / Chat TTL。"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest


class TestFileLockAndPaperTrim(unittest.TestCase):
    def test_path_lock_serializes_writers(self):
        from core.file_lock import path_lock

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            seq = []
            lock_event = threading.Event()

            def w1():
                with path_lock(path):
                    seq.append("1in")
                    lock_event.set()
                    threading.Event().wait(0.05)
                    seq.append("1out")

            def w2():
                lock_event.wait(timeout=2)
                with path_lock(path):
                    seq.append("2in")
                    seq.append("2out")

            ta = threading.Thread(target=w1)
            tb = threading.Thread(target=w2)
            ta.start()
            tb.start()
            ta.join(timeout=5)
            tb.join(timeout=5)
            self.assertEqual(seq, ["1in", "1out", "2in", "2out"])

    def test_path_lock_flock_timeout(self):
        from core.file_lock import path_lock

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            held = threading.Event()
            release = threading.Event()

            def holder():
                with path_lock(path, timeout_sec=None):
                    held.set()
                    release.wait(timeout=5)

            t = threading.Thread(target=holder)
            t.start()
            self.assertTrue(held.wait(timeout=2))
            with self.assertRaises(TimeoutError):
                with path_lock(path, timeout_sec=0.2):
                    pass
            release.set()
            t.join(timeout=5)

    def test_path_lock_reentrant_same_thread(self):
        """save_paper 套在 paper_write_lock 内时不得二次 flock 自锁。"""
        from core.file_lock import path_lock
        from core.io_atomic import atomic_write_json

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            with path_lock(path, timeout_sec=2.0):
                with path_lock(path, timeout_sec=2.0):
                    atomic_write_json(path, {"ok": True})
            with open(path, encoding="utf-8") as f:
                self.assertIn("ok", f.read())

    def test_trim_trades_on_save(self):
        from core.paper import MAX_TRADES, save_paper, load_paper

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "trades": [{"i": i} for i in range(MAX_TRADES + 80)],
                "snapshots": [],
                "signal_log": [],
                "operation_log": [],
            }
            save_paper(paper, path)
            loaded = load_paper(path)
            self.assertEqual(len(loaded["trades"]), MAX_TRADES)
            self.assertEqual(loaded["trades"][0]["i"], 80)


class TestHttpRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        from core.http_retry import call_with_retry

        n = {"c": 0}

        def flaky():
            n["c"] += 1
            if n["c"] < 3:
                raise TimeoutError("timed out")
            return "ok"

        self.assertEqual(call_with_retry(flaky, retries=3, base_delay_sec=0.01), "ok")
        self.assertEqual(n["c"], 3)

    def test_non_retryable_raises(self):
        from core.http_retry import call_with_retry

        with self.assertRaises(ValueError):
            call_with_retry(lambda: (_ for _ in ()).throw(ValueError("bad")), retries=3)


class TestChatServiceTtl(unittest.TestCase):
    def test_message_trim(self):
        from services.chat_service import ChatService

        svc = ChatService()
        sid, agent = svc.get_or_create("ttl-test")
        agent.messages = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": str(i)} for i in range(80)
        ]
        svc._trim_messages(agent)
        self.assertLessEqual(len(agent.messages), 40)
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(sid, "ttl-test")


if __name__ == "__main__":
    unittest.main()
