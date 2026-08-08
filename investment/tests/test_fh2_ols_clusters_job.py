"""FH2：factor-ols-clusters Job 入队 / 完成 / 取消。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestFh2OlsClustersJob(unittest.TestCase):
    def test_start_job_finishes_with_result(self):
        from core.job_progress import JobProgress
        from quant.services.quant_service import QuantService

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "quant_ols_clusters.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            svc = QuantService()
            fake = {
                "success": True,
                "n_clusters": 2,
                "lookahead_flags": {"pit_fundamentals": True, "fundamentals": "none"},
            }
            with patch(
                "core.job_progress.quant_ols_clusters_job", slot
            ), patch.object(
                svc, "run_factor_ols_cluster_experiment", return_value=fake
            ):
                out = svc.start_factor_ols_cluster_job(lookback=80)
                self.assertTrue(out.get("background"))
                job_id = out["job"]["id"]
                for _ in range(50):
                    snap = slot.get()
                    if snap.get("status") in ("done", "failed"):
                        break
                    time.sleep(0.05)
                snap = slot.get()
                self.assertEqual(snap["id"], job_id)
                self.assertEqual(snap["status"], "done")
                self.assertTrue((snap.get("result") or {}).get("success"))

    def test_cancel_marks_request(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "j.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            slot.start(kind="factor_ols_clusters", total=3, message="run")
            self.assertTrue(slot.request_cancel())
            self.assertTrue(slot.is_cancel_requested())

    def test_force_fail_releases_slot(self):
        from core.job_progress import JobProgress
        from quant.services.quant_service import QuantService

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "quant_ols_clusters.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            slot.start(kind="factor_ols_clusters", total=10, message="拉日线 0/100")
            # 伪造陈旧 updated_at（须超过 stuck_start 240s）
            with slot._lock:
                slot._job["updated_at"] = time.time() - 260
                slot._save_unlocked()
            svc = QuantService()
            fake = {"success": True, "n_clusters": 2}
            with patch("core.job_progress.quant_ols_clusters_job", slot), patch.object(
                svc, "run_factor_ols_cluster_experiment", return_value=fake
            ):
                out = svc.start_factor_ols_cluster_job(lookback=80)
            self.assertTrue(out.get("background"), out)
            for _ in range(50):
                if slot.get().get("status") in ("done", "failed"):
                    break
                time.sleep(0.05)
            self.assertEqual(slot.get().get("status"), "done")

    def test_start_while_running_reuses_job(self):
        from core.job_progress import JobProgress
        from quant.services.quant_service import QuantService

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "quant_ols_clusters.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            job_id = slot.start(kind="factor_ols_clusters", total=10, message="拉日线 1/50")
            svc = QuantService()
            with patch("core.job_progress.quant_ols_clusters_job", slot), patch.object(
                svc, "run_factor_ols_cluster_experiment"
            ) as run_mock:
                out = svc.start_factor_ols_cluster_job(lookback=80)
            self.assertTrue(out.get("background"), out)
            self.assertTrue(out.get("reused"), out)
            self.assertEqual((out.get("job") or {}).get("id"), job_id)
            run_mock.assert_not_called()
            self.assertEqual(slot.get().get("status"), "running")

    def test_finish_ignores_stale_job_id(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "j.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            old_id = slot.start(kind="factor_ols_clusters", total=3, message="old")
            new_id = slot.start(kind="factor_ols_clusters", total=5, message="new")
            self.assertNotEqual(old_id, new_id)
            ok = slot.finish(result={"ok": True}, job_id=old_id)
            self.assertFalse(ok)
            self.assertEqual(slot.get()["status"], "running")
            self.assertEqual(slot.get()["id"], new_id)
            self.assertTrue(slot.finish(result={"ok": True}, job_id=new_id))
            self.assertEqual(slot.get()["status"], "done")

    def test_reclaim_if_stale_on_poll(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "j.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            slot.start(kind="factor_ols_clusters", total=10, message="拉日线 0/100")
            with slot._lock:
                slot._job["updated_at"] = time.time() - 120
                slot._save_unlocked()
            # 满池起点阈值已放宽到 240s；120s 不应误杀
            self.assertFalse(slot.reclaim_if_stale())
            with slot._lock:
                slot._job["updated_at"] = time.time() - 250
                slot._save_unlocked()
            self.assertTrue(slot.reclaim_if_stale())
            self.assertEqual(slot.get()["status"], "failed")

    def test_touch_refreshes_updated_at(self):
        from core.job_progress import JobProgress

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "j.json")
            slot = JobProgress(name="quant-ols-clusters", persist_path=path)
            jid = slot.start(kind="factor_ols_clusters", total=10, message="拉日线 0/100")
            with slot._lock:
                slot._job["updated_at"] = time.time() - 200
                slot._save_unlocked()
            self.assertTrue(slot.touch(job_id=jid))
            self.assertLess(slot.stale_seconds() or 99, 5)


if __name__ == "__main__":
    unittest.main()
