"""实验追踪器测试。"""

import json
import os
import shutil
import tempfile
import unittest

from core import experiment_tracker as et


class TestExperimentTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = et.EXPERIMENTS_DIR
        et.EXPERIMENTS_DIR = self.tmp
        et._active.clear()

    def tearDown(self):
        et.EXPERIMENTS_DIR = self._old
        et._active.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_log_experiment_one_shot(self):
        eid = et.log_experiment(
            "tc_lgb",
            {"backend": "lightgbm", "n_estimators": 200},
            {"ic": 0.052, "ir": 1.15},
            tags={"dataset": "csi300"},
        )
        rec = et.get_experiment(eid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["model_type"], "tc_lgb")
        self.assertTrue(eid.startswith("tc_lgb_"))
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["config"]["backend"], "lightgbm")
        self.assertEqual(rec["metrics"]["ic"], 0.052)
        self.assertEqual(rec["tags"]["dataset"], "csi300")

    def test_step_by_step(self):
        eid = et.start_experiment("ridge", {"lambda": 1.0})
        self.assertEqual(et.get_experiment(eid)["status"], "running")
        et.log_metrics(eid, {"ic": 0.04})
        et.log_artifact(eid, "model", "/tmp/m.pkl")
        et.finish_experiment(eid, metrics={"oos": 0.08})
        rec = et.get_experiment(eid)
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["metrics"]["ic"], 0.04)
        self.assertEqual(rec["metrics"]["oos"], 0.08)
        self.assertEqual(rec["artifacts"]["model"], "/tmp/m.pkl")

    def test_list_and_filter(self):
        et.log_experiment("tc_lgb", {"b": 1}, {"ic": 0.05})
        et.log_experiment("tc_lgb", {"b": 2}, {"ic": 0.06})
        et.log_experiment("ridge", {"b": 1}, {"ic": 0.03})
        self.assertEqual(len(et.list_experiments("tc_lgb")), 2)
        self.assertEqual(len(et.list_experiments("tau_lgb")), 2)
        self.assertEqual(et.list_experiments("tc_lgb")[0]["model_type"], "tc_lgb")
        self.assertEqual(len(et.list_experiments("ridge")), 1)
        self.assertEqual(len(et.list_experiments()), 3)

    def test_compare(self):
        e1 = et.log_experiment("tc_lgb", {"b": 1}, {"ic": 0.05, "ir": 1.0})
        e2 = et.log_experiment("tc_lgb", {"b": 2}, {"ic": 0.06, "ir": 1.2})
        rows = et.compare_experiments([e1, e2], ["ic", "ir"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["metric.ic"], 0.05)
        self.assertEqual(rows[1]["metric.ir"], 1.2)

    def test_best(self):
        et.log_experiment("tc_lgb", {"b": 1}, {"ic": 0.05})
        et.log_experiment("tc_lgb", {"b": 2}, {"ic": 0.08})
        et.log_experiment("tc_lgb", {"b": 3}, {"ic": 0.03})
        best = et.best_experiment("tc_lgb", "ic", higher_is_better=True)
        self.assertEqual(best["metrics"]["ic"], 0.08)

    def test_failed_experiment(self):
        eid = et.log_experiment("tc_lgb", {}, {}, status="failed", error="样本不足")
        rec = et.get_experiment(eid)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("样本不足", rec["error"])

    def test_track_fit_report(self):
        report = {
            "success": True,
            "ic": 0.055,
            "rank_ic": 0.050,
            "oos_return": 0.12,
            "ir": 1.1,
            "sample_count": 500,
        }
        eid = et.track_fit_report("tau_lgb", {"backend": "lightgbm"}, report)
        rec = et.get_experiment(eid)
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["metrics"]["ic"], 0.055)
        self.assertEqual(rec["metrics"]["sample_count"], 500)
        self.assertEqual(rec["model_type"], "tc_lgb")

    def test_tau_lgb_alias_lists_as_canonical(self):
        """服务方法名 tau_lgb 与登记名 tc_lgb 互扫。"""
        eid = et.log_experiment("tau_lgb", {"b": 1}, {"ic": 0.02})
        listed = et.list_experiments("tc_lgb")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["model_type"], "tc_lgb")
        self.assertEqual(listed[0]["experiment_id"], eid)
        self.assertEqual(len(et.list_experiments("tau_lgb")), 1)
        # 退役旧名不再映射
        self.assertEqual(et.canonical_model_type("tau_ridge"), "tau_ridge")
        self.assertEqual(et.canonical_model_type("tc_ridge"), "tc_ridge")


if __name__ == "__main__":
    unittest.main()
