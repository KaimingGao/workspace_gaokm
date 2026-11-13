"""ŷ_oo_rank / ŷ_τc_rank / ŷ_oo 拟合任务槽。

单测勿污染 ``data/jobs/*.json``：临时关掉 persist，结束后还原槽位快照。
"""

from __future__ import annotations

import time
import unittest

from tests.job_slot_isolation import isolated_live_slot


class TestOoRankJob(unittest.TestCase):
    def test_start_oo_rank_job_returns_background(self):
        from core.job_progress import oo_rank_job
        from quant.services.quant_service_factors import QuantFactorMixin

        if oo_rank_job.is_running():
            self.skipTest("oo_rank_job already running")
        svc = QuantFactorMixin()
        started = {"n": 0, "kwargs": None}

        def _fake(**kwargs):
            started["n"] += 1
            started["kwargs"] = kwargs
            time.sleep(0.4)
            return {"success": True, "task": "oo_rank", "return_model": {}}

        svc.run_oo_rank_experiment = _fake
        with isolated_live_slot(oo_rank_job):
            t0 = time.time()
            out = svc.start_oo_rank_job(watching_limit=8)
            self.assertLess(time.time() - t0, 0.5)
            self.assertTrue(out.get("background"))
            self.assertTrue(out.get("success"))
            job_id = (out.get("job") or {}).get("id")
            self.assertTrue(job_id)
            deadline = time.time() + 6
            while time.time() < deadline and oo_rank_job.is_running():
                time.sleep(0.05)
            snap = oo_rank_job.get()
            self.assertEqual(snap.get("status"), "done")
            self.assertEqual(started["n"], 1)
            self.assertEqual(snap.get("id"), job_id)
            kw = started["kwargs"] or {}
            self.assertTrue(callable(kw.get("progress_cb")))
            self.assertTrue(callable(kw.get("job_alive_cb")))


class TestTcRankJob(unittest.TestCase):
    def test_start_tc_rank_job_returns_background(self):
        from core.job_progress import tc_rank_job
        from quant.services.quant_service_factors import QuantFactorMixin

        if tc_rank_job.is_running():
            self.skipTest("tc_rank_job already running")
        svc = QuantFactorMixin()
        started = {"n": 0, "kwargs": None}

        def _fake(**kwargs):
            started["n"] += 1
            started["kwargs"] = kwargs
            time.sleep(0.4)
            return {"success": True, "task": "tc_rank", "return_model": {}}

        svc.run_tc_rank_experiment = _fake
        with isolated_live_slot(tc_rank_job):
            out = svc.start_tc_rank_job(watching_limit=8)
            self.assertTrue(out.get("background"))
            job_id = (out.get("job") or {}).get("id")
            self.assertTrue(job_id)
            deadline = time.time() + 6
            while time.time() < deadline and tc_rank_job.is_running():
                time.sleep(0.05)
            snap = tc_rank_job.get()
            self.assertEqual(snap.get("status"), "done")
            self.assertEqual(started["n"], 1)
            kw = started["kwargs"] or {}
            self.assertTrue(callable(kw.get("progress_cb")))


class TestOoLgbJob(unittest.TestCase):
    def test_start_oo_lgb_job_returns_background(self):
        from core.job_progress import oo_lgb_job
        from quant.services.quant_service_replay import QuantReplayMixin

        if oo_lgb_job.is_running():
            self.skipTest("oo_lgb_job already running")
        svc = QuantReplayMixin()
        started = {"n": 0, "kwargs": None}

        def _fake(**kwargs):
            started["n"] += 1
            started["kwargs"] = kwargs
            time.sleep(0.4)
            return {"success": True, "task": "fit_return_score_model"}

        svc.fit_return_score_model = _fake
        with isolated_live_slot(oo_lgb_job):
            out = svc.start_oo_lgb_job(watching_limit=8, lookback=120)
            self.assertTrue(out.get("background"))
            job_id = (out.get("job") or {}).get("id")
            self.assertTrue(job_id)
            deadline = time.time() + 6
            while time.time() < deadline and oo_lgb_job.is_running():
                time.sleep(0.05)
            snap = oo_lgb_job.get()
            self.assertEqual(snap.get("status"), "done")
            self.assertEqual(started["n"], 1)
            kw = started["kwargs"] or {}
            self.assertTrue(callable(kw.get("progress_cb")))


class TestOoRankOrphan(unittest.TestCase):
    def test_oo_rank_orphan_skips_last_report(self):
        from unittest.mock import patch

        from quant.services.quant_service_factors import QuantFactorMixin

        svc = QuantFactorMixin()
        with patch(
            "core.research.oo_rank_lambdarank.save_oo_rank_last_report"
        ) as save_last, patch(
            "core.research.oo_rank_panel.resolve_rank_fit_plan",
            return_value={
                "success": True,
                "codes": ["A", "B"],
                "cap": 2,
                "watching_pool_size": 2,
                "train_universe": "pool",
                "universe_source": "watching",
            },
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            return_value=(
                {"A": [{"date": "2024-01-01"}], "B": [{"date": "2024-01-01"}]},
                [],
                {},
            ),
        ), patch(
            "core.research.oo_rank_lambdarank.fit_oo_rank_report",
            return_value={"success": True, "task": "oo_rank"},
        ):
            out = svc.run_oo_rank_experiment(
                watching_limit=2,
                job_alive_cb=lambda: False,
            )
        self.assertFalse(out.get("success"))
        self.assertTrue(out.get("orphaned"))
        save_last.assert_not_called()


class TestTcRankOrphan(unittest.TestCase):
    def test_tc_rank_orphan_skips_last_report(self):
        from unittest.mock import patch

        from quant.services.quant_service_factors import QuantFactorMixin

        svc = QuantFactorMixin()
        with patch(
            "core.research.tc_rank.save_tc_rank_last_report"
        ) as save_last, patch(
            "core.research.oo_rank_panel.resolve_rank_fit_plan",
            return_value={
                "success": True,
                "codes": ["A", "B"],
                "cap": 2,
                "watching_pool_size": 2,
                "train_universe": "pool",
                "universe_source": "watching",
            },
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            return_value=(
                {"A": [{"date": "2024-01-01"}], "B": [{"date": "2024-01-01"}]},
                [],
                {},
            ),
        ), patch(
            "core.research.tc_rank.fit_tc_rank_report",
            return_value={"success": True, "task": "tc_rank"},
        ):
            out = svc.run_tc_rank_experiment(
                watching_limit=2,
                job_alive_cb=lambda: False,
            )
        self.assertFalse(out.get("success"))
        self.assertTrue(out.get("orphaned"))
        save_last.assert_not_called()


if __name__ == "__main__":
    unittest.main()
