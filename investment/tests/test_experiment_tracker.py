"""实验追踪器测试。"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
            "tau_tree",
            {"backend": "lightgbm", "n_estimators": 200},
            {"ic": 0.052, "ir": 1.15},
            tags={"dataset": "csi300"},
        )
        rec = et.get_experiment(eid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["model_type"], "tc_tree")
        self.assertTrue(eid.startswith("tc_tree_"))
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
        self.assertEqual(len(et.list_experiments("tc_tree")), 2)
        self.assertEqual(et.list_experiments("tc_tree")[0]["model_type"], "tc_tree")
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
        self.assertEqual(rec["model_type"], "tc_tree")

    def test_legacy_dir_lists_as_canonical(self):
        legacy = os.path.join(self.tmp, "tau_ridge")
        os.makedirs(legacy)
        eid = "tau_ridge_20260101_000000_abcd1234"
        rec = {
            "experiment_id": eid,
            "model_type": "tau_ridge",
            "status": "completed",
            "config": {},
            "metrics": {"oos_ic": 0.01},
            "created_at": "2026-01-01T00:00:00",
        }
        with open(os.path.join(legacy, f"{eid}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        listed = et.list_experiments("tc_ridge")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["model_type"], "tc_ridge")
        self.assertEqual(listed[0]["experiment_id"], eid)
        self.assertEqual(len(et.list_experiments("tau_ridge")), 1)


if __name__ == "__main__":
    unittest.main()
