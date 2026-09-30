"""A2：Job stale / cancel 契约与落盘槽。"""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.job_progress import JobProgress, JobRegistry


class TestA2JobRuntime(unittest.TestCase):
    def test_cancel_and_force(self):
        slot = JobProgress(name="test-cancel")
        jid = slot.start(kind="demo", total=10, message="跑")
        self.assertTrue(slot.request_cancel())
        self.assertTrue(slot.is_cancel_requested())
        self.assertTrue(slot.is_running())
        self.assertTrue(slot.force_fail("stop"))
        self.assertFalse(slot.is_running())
        snap = slot.get()
        self.assertEqual(snap["status"], "failed")
        self.assertIn("stale_policy", snap)
        self.assertEqual(snap["id"], jid)

    def test_reclaim_if_stale(self):
        slot = JobProgress(name="test-stale")
        slot.start(kind="demo", message="启动中")
        with slot._lock:
            slot._job["updated_at"] = time.time() - 9999
        self.assertTrue(slot.reclaim_if_stale(stale_sec=1.0, stuck_start_sec=1.0))
        self.assertEqual(slot.get()["status"], "failed")

    def test_registry_reclaim_and_policies(self):
        reg = JobRegistry()
        path = os.path.join(tempfile.mkdtemp(), "job.json")
        s = reg.slot("paper", persist_path=path)
        s.start(kind="x", message="排队…")
        with s._lock:
            s._job["updated_at"] = time.time() - 9999
        reclaimed = reg.reclaim_all_stale()
        self.assertIn("paper", reclaimed)
        policies = reg.list_policies()
        names = {p["slot"] for p in policies}
        self.assertIn("paper", names)
        self.assertIn("quant-ols-clusters", names)
        self.assertIn("t30-ridge", names)
        self.assertIn("t60-ridge", names)
        self.assertIn("t90-ridge", names)
        self.assertNotIn("tc-ridge", names)
        self.assertIn("t0-backtest", names)
        t30_p = next(p for p in policies if p["slot"] == "t30-ridge")
        self.assertGreaterEqual(float(t30_p.get("stale_sec") or 0), 1800.0)
        t60_p = next(p for p in policies if p["slot"] == "t60-ridge")
        self.assertGreaterEqual(float(t60_p.get("stale_sec") or 0), 1800.0)
        t90_p = next(p for p in policies if p["slot"] == "t90-ridge")
        self.assertGreaterEqual(float(t90_p.get("stale_sec") or 0), 1800.0)
        t0_p = next(p for p in policies if p["slot"] == "t0-backtest")
        self.assertGreaterEqual(float(t0_p.get("stale_sec") or 0), 1800.0)
        paper_p = next(p for p in policies if p["slot"] == "paper")
        self.assertTrue(paper_p.get("persisted"))


if __name__ == "__main__":
    unittest.main()
