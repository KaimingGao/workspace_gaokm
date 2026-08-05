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


if __name__ == "__main__":
    unittest.main()
