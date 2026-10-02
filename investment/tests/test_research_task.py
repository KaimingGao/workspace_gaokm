"""研究任务装配：记录步骤、训练窗标记、头注册表。"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import experiment_tracker as et
from core.research.factor_ols_fit import _zscore_complete_panel
from core.research.task import (
    describe_processor_windows,
    dispatch_research_task,
    finish_research_run,
    records_experiment,
    research_head_method,
    research_zscore_leaks,
)


class _Probe:
    @records_experiment("task_probe")
    def run_probe(self, *, lookback: int = 5):
        return {
            "success": True,
            "fit_end": "2024-06-01",
            "eval_start": "2024-06-02",
            "oos": {"ic": 0.08, "spearman": 0.07, "model_role": "research"},
            "return_model_research": {"model_role": "research", "zscore_means": {"f": 1.0}},
            "return_model": {"model_role": "live"},
            "pit_fundamentals": True,
        }

    @records_experiment("task_fail")
    def run_fail(self):
        return {"success": False, "error": "样本不足"}


class TestResearchTask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = et.EXPERIMENTS_DIR
        et.EXPERIMENTS_DIR = self.tmp

    def tearDown(self):
        et.EXPERIMENTS_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_step_writes_oos_and_windows(self):
        report = _Probe().run_probe(lookback=9)
        eid = report.get("experiment_id")
        self.assertTrue(eid)
        rec = et.get_experiment(eid)
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["model_type"], "task_probe")
        self.assertEqual(rec["config"]["lookback"], 9)
        self.assertEqual(rec["metrics"]["oos_ic"], 0.08)
        self.assertEqual(rec["metrics"]["oos_spearman"], 0.07)
        self.assertEqual(rec["metrics"]["fit_end"], "2024-06-01")
        self.assertEqual(rec["metrics"]["research_window"], "train")
        self.assertEqual(rec["metrics"]["live_window"], "full")
        self.assertTrue(rec["metrics"]["oos_guard_ok"])
        self.assertEqual(rec["tags"]["fundamentals_pit"], "true")
        finish_research_run("task_probe", {"lookback": 9}, report)
        self.assertEqual(len(et.list_experiments("task_probe")), 1)
        self.assertEqual(et.get_experiment(report["experiment_id"])["experiment_id"], eid)

    def test_failed_fit_is_recorded(self):
        report = _Probe().run_fail()
        rec = et.get_experiment(report["experiment_id"])
        self.assertEqual(rec["status"], "failed")
        self.assertIn("样本不足", rec["error"])

    def test_oos_live_role_is_rejected(self):
        windows = describe_processor_windows(
            {
                "oos": {"model_role": "live", "ic": 0.1},
                "return_model_research": {"model_role": "research"},
                "return_model": {"model_role": "live"},
            }
        )
        self.assertFalse(windows["ok"])
        self.assertEqual(windows["error"], "oos_used_live_model")

    def test_dispatch_unknown_head(self):
        out = dispatch_research_task(object(), "not_a_head")
        self.assertFalse(out["success"])
        self.assertIn("task_probe", out["heads"])

    def test_registered_service_heads(self):
        from quant.services.quant_service import QuantService

        self.assertEqual(research_head_method("tc_ridge"), "run_tau_ridge_experiment")
        self.assertEqual(research_head_method("tau_ridge"), "run_tau_ridge_experiment")
        self.assertEqual(research_head_method("tc_tree"), "run_tau_tree_experiment")
        self.assertEqual(research_head_method("tau_tree"), "run_tau_tree_experiment")
        self.assertEqual(research_head_method("oo_rank"), "run_oo_rank_experiment")
        self.assertTrue(hasattr(QuantService, "run_research_task"))

    def test_research_zscore_matches_train_window_only(self):
        train = [{"f": float(v)} for v in (0, 0, 10, 10)]
        _, train_means, _ = _zscore_complete_panel(train, ["f"])
        self.assertAlmostEqual(train_means["f"], 5.0, places=4)
        self.assertFalse(research_zscore_leaks(5.0, train_means["f"]))
        mixed = train + [{"f": 100.0}] * 4
        _, mixed_means, _ = _zscore_complete_panel(mixed, ["f"])
        self.assertTrue(research_zscore_leaks(train_means["f"], mixed_means["f"]))


if __name__ == "__main__":
    unittest.main()
