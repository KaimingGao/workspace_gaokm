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

    def test_try_start_atomic(self):
        slot = JobProgress(name="test-try-start")
        jid, snap = slot.try_start(kind="a", total=2, message="排队中…")
        self.assertIsNotNone(jid)
        self.assertEqual(snap["status"], "running")
        jid2, running = slot.try_start(kind="b", total=2, message="再开")
        self.assertIsNone(jid2)
        self.assertEqual(running["id"], jid)
        self.assertEqual(running["kind"], "a")

    def test_materialize_allow_zero_progress(self):
        """物化宏步 current=0 但心跳新鲜时勿按零进展回收。"""
        slot = JobProgress(name="materialize-l1l2")
        slot.start(kind="materialize_open", total=2, message="L1 assemble…")
        now = time.time()
        with slot._lock:
            slot._job["started_at"] = now - 6000
            slot._job["updated_at"] = now  # 心跳新鲜
            slot._job["current"] = 0
        self.assertFalse(slot.reclaim_if_stale())
        self.assertEqual(slot.get()["status"], "running")
        # 心跳也停了才按 stale 回收
        with slot._lock:
            slot._job["updated_at"] = now - 6000
        self.assertTrue(slot.reclaim_if_stale())
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
        self.assertIn("co-ridge", names)
        self.assertIn("tau-ridge", names)
        self.assertNotIn("tc-ridge", names)
        self.assertIn("t0-backtest", names)
        self.assertIn("materialize-l1l2", names)
        # 满池 Ridge / 回测 / 物化：墙钟长，stuck_start 须够长（90min）以免误杀
        for slot_name in (
            "t30-ridge",
            "t60-ridge",
            "t90-ridge",
            "co-ridge",
            "tau-ridge",
            "t0-backtest",
            "materialize-l1l2",
        ):
            pol = next(p for p in policies if p["slot"] == slot_name)
            self.assertGreaterEqual(float(pol.get("stale_sec") or 0), 5400.0, slot_name)
            self.assertGreaterEqual(
                float(pol.get("stuck_start_sec") or 0), 5400.0, slot_name
            )
        mat_p = next(p for p in policies if p["slot"] == "materialize-l1l2")
        self.assertTrue(mat_p.get("allow_zero_progress"))
        tau_p = next(p for p in policies if p["slot"] == "tau-ridge")
        self.assertTrue(tau_p.get("allow_zero_progress"))
        paper_p = next(p for p in policies if p["slot"] == "paper")
        self.assertTrue(paper_p.get("persisted"))

    def test_ridge_large_result_persists_ridge_stub_not_rebalance(self):
        """大 ŷ_* 报告落盘摘要须保留 success/oos，勿裁成调仓 stub。"""
        path = os.path.join(tempfile.mkdtemp(), "t30_ridge.json")
        slot = JobProgress(name="t30-ridge", persist_path=path)
        jid = slot.start(kind="t30_ridge", total=10, message="拟合")
        fat = {
            "success": True,
            "schema": "t30_ridge_v4",
            "recommended_backend": "ridge",
            "oos": {"auc": 0.55, "brier": 0.24, "n_test": 100},
            "return_model": {"w": list(range(20_000))},
        }
        self.assertTrue(slot.finish(result=fat, job_id=jid))
        with open(path, encoding="utf-8") as f:
            on_disk = __import__("json").load(f)
        res = on_disk.get("result") or {}
        self.assertTrue(res.get("persisted_truncated"))
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("schema"), "t30_ridge_v4")
        self.assertNotIn("rebalance_report", res)
        self.assertEqual((res.get("oos") or {}).get("auc"), 0.55)

    def test_minute_refresh_large_result_keeps_warmup_not_rebalance(self):
        path = os.path.join(tempfile.mkdtemp(), "minute_refresh.json")
        slot = JobProgress(name="minute-refresh", persist_path=path)
        jid = slot.start(kind="minute_refresh", total=10, message="强更")
        fat = {
            "success": True,
            "task": "minute_refresh",
            "mode": "full",
            "watching_limit": 160,
            "universe_count": 160,
            "lookback_days": 20,
            "period": "5",
            "minute_warmup": {
                "ok": True,
                "mode": "full",
                "warmed": 160,
                "total": 160,
                "timed_out": 0,
            },
            "status": {"codes": ["x"] * 20_000},
        }
        self.assertTrue(slot.finish(result=fat, job_id=jid))
        with open(path, encoding="utf-8") as f:
            on_disk = __import__("json").load(f)
        res = on_disk.get("result") or {}
        self.assertTrue(res.get("persisted_truncated"))
        self.assertTrue(res.get("success"))
        self.assertEqual((res.get("minute_warmup") or {}).get("warmed"), 160)
        self.assertNotIn("status", res)
        self.assertNotIn("rebalance_report", res)

    def test_bars_refresh_large_result_keeps_bars_refresh(self):
        path = os.path.join(tempfile.mkdtemp(), "bars_refresh.json")
        slot = JobProgress(name="bars-refresh", persist_path=path)
        jid = slot.start(kind="bars_refresh", total=10, message="日线")
        fat = {
            "success": True,
            "task": "bars_refresh",
            "mode": "topup",
            "watching_limit": 160,
            "universe_count": 160,
            "lookback": 600,
            "bars_refresh": {
                "mode": "topup",
                "total": 160,
                "cache_count": 150,
                "gap_count": 10,
            },
            "status": {"rows": list(range(20_000))},
        }
        self.assertTrue(slot.finish(result=fat, job_id=jid))
        with open(path, encoding="utf-8") as f:
            on_disk = __import__("json").load(f)
        res = on_disk.get("result") or {}
        self.assertTrue(res.get("persisted_truncated"))
        self.assertEqual((res.get("bars_refresh") or {}).get("gap_count"), 10)
        self.assertNotIn("status", res)
        self.assertNotIn("rebalance_report", res)


if __name__ == "__main__":
    unittest.main()
