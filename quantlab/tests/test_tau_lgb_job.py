"""τ→close（tau-lgb）拟合任务槽。"""

from __future__ import annotations

import time
import unittest

from tests.job_slot_isolation import isolated_live_slot


class TestTauLgbJob(unittest.TestCase):
    def test_start_tau_lgb_job_returns_background(self):
        from core.job_progress import tau_lgb_job
        from quant.services.quant_service_factors import QuantFactorMixin

        # 勿 force_fail 真实运行中的拟合槽（会冲掉 UI 进度）
        if tau_lgb_job.is_running():
            self.skipTest("tau_lgb_job already running")
        svc = QuantFactorMixin()
        started = {"n": 0, "kwargs": None}

        def _fake(**kwargs):
            started["n"] += 1
            started["kwargs"] = kwargs
            time.sleep(1.0)
            return {"success": True, "return_model": {"intercept": 0.0}}

        svc.run_tau_lgb_experiment = _fake
        with isolated_live_slot(tau_lgb_job):
            t0 = time.time()
            out = svc.start_tau_lgb_job(watching_limit=2)
            self.assertLess(time.time() - t0, 0.5)
            self.assertTrue(out.get("background"))
            self.assertTrue(out.get("success"))
            job_id = (out.get("job") or {}).get("id")
            self.assertTrue(job_id)
            deadline = time.time() + 6
            while time.time() < deadline and tau_lgb_job.is_running():
                time.sleep(0.05)
            snap = tau_lgb_job.get()
            self.assertEqual(snap.get("status"), "done")
            self.assertEqual(started["n"], 1)
            self.assertEqual(snap.get("id"), job_id)
            self.assertTrue((snap.get("result") or {}).get("success"))
            kw = started["kwargs"] or {}
            self.assertTrue(callable(kw.get("progress_cb")))
            self.assertTrue(callable(kw.get("job_alive_cb")))

    def test_tau_orphan_skips_last_report(self):
        from unittest.mock import patch

        from quant.services.quant_service_factors import QuantFactorMixin

        svc = QuantFactorMixin()
        with patch(
            "core.research.tc_model.save_tau_last_report"
        ) as save_last, patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={"ok": True, "codes": ["A", "B"], "source": "watching"},
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            return_value=({}, [], {}),
        ), patch(
            "core.signal.dual_score.get_dual_score_cfg",
            return_value={"enable_minute_tau": False},
        ), patch(
            "core.signal.minute_tau_grid.resolve_tau_train_prefix",
            return_value={
                "use_minute": False,
                "tau_hm": "open",
                "tau_end_hm": "09:30",
                "zero_prefix": True,
            },
        ), patch(
            "core.research.tc_model.fit_tau_model_report",
            return_value={"success": True, "task": "tc_lgb"},
        ):
            out = svc.run_tau_lgb_experiment(
                watching_limit=2,
                job_alive_cb=lambda: False,
            )
        self.assertFalse(out.get("success"))
        self.assertTrue(out.get("orphaned"))
        save_last.assert_not_called()

if __name__ == "__main__":
    unittest.main()
