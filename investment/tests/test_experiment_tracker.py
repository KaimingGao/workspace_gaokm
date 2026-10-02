"""实验追踪器测试。"""

import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import experiment_tracker as et


class TestExperimentTracker(unittest.TestCase):
    def setUp(self):
        if os.path.isdir(et.EXPERIMENTS_DIR):
            shutil.rmtree(et.EXPERIMENTS_DIR)

    def tearDown(self):
        if os.path.isdir(et.EXPERIMENTS_DIR):
            shutil.rmtree(et.EXPERIMENTS_DIR)

    def test_log_experiment_one_shot(self):
        eid = et.log_experiment(
            "tau_tree",
            {"backend": "lightgbm", "n_estimators": 200},
            {"ic": 0.052, "ir": 1.15},
            tags={"dataset": "csi300"},
        )
        rec = et.get_experiment(eid)
        self.assertIsNotNone(rec)
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
        et.log_experiment("tau_tree", {"b": 1}, {"ic": 0.05})
        et.log_experiment("tau_tree", {"b": 2}, {"ic": 0.06})
        et.log_experiment("ridge", {"b": 1}, {"ic": 0.03})
        self.assertEqual(len(et.list_experiments("tau_tree")), 2)
        self.assertEqual(len(et.list_experiments("ridge")), 1)
        self.assertEqual(len(et.list_experiments()), 3)

    def test_compare(self):
        e1 = et.log_experiment("tau_tree", {"b": 1}, {"ic": 0.05, "ir": 1.0})
        e2 = et.log_experiment("tau_tree", {"b": 2}, {"ic": 0.06, "ir": 1.2})
        rows = et.compare_experiments([e1, e2], ["ic", "ir"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["metric.ic"], 0.05)
        self.assertEqual(rows[1]["metric.ir"], 1.2)

    def test_best(self):
        et.log_experiment("tau_tree", {"b": 1}, {"ic": 0.05})
        et.log_experiment("tau_tree", {"b": 2}, {"ic": 0.08})
        et.log_experiment("tau_tree", {"b": 3}, {"ic": 0.03})
        best = et.best_experiment("tau_tree", "ic", higher_is_better=True)
        self.assertEqual(best["metrics"]["ic"], 0.08)

    def test_failed_experiment(self):
        eid = et.log_experiment("tau_tree", {}, {}, status="failed", error="样本不足")
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
        eid = et.track_fit_report("tau_tree", {"backend": "xgb"}, report)
        rec = et.get_experiment(eid)
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["metrics"]["ic"], 0.055)
        self.assertEqual(rec["metrics"]["sample_count"], 500)


if __name__ == "__main__":
    unittest.main()
