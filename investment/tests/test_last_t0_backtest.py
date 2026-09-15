"""Last T0 backtest snapshot: save/load for /follow restore-on-refresh."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import web.deps as deps


class TestLastT0BacktestStore(unittest.TestCase):
    def test_save_load_roundtrip_drops_per_stock_results(self):
        from core.backtest_result_store import (
            load_last_t0_backtest,
            save_last_t0_backtest,
        )

        raw = {
            "success": True,
            "task": "t0_backtest",
            "from_holdings": True,
            "ok_count": 2,
            "t0_pnl_total": 12.5,
            "t0_trade_days": 4,
            "scope_label": "模拟持仓 2 只",
            "trade_days_sample": [{"date": "2026-09-01", "pnl": 1.0, "sold_qty": 100}],
            "days": [{"date": "2026-08-01", "skipped": True}] * 40,
            "viz": {"cumulative_pnl": [{"date": "2026-09-01", "pnl": 1.0}]},
            "results": [{"stock_code": "600519", "days": [{"x": i} for i in range(80)]}],
            "request": {"lookback": 30, "from_paper": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "last_t0.json")
            save_last_t0_backtest(raw, path=path)
            pack = load_last_t0_backtest(path)
        self.assertTrue(pack["ok"])
        self.assertFalse(pack["empty"])
        self.assertTrue(pack.get("saved_at"))
        result = pack["result"]
        self.assertEqual(result["t0_pnl_total"], 12.5)
        self.assertEqual(result["ok_count"], 2)
        self.assertEqual(len(result["trade_days_sample"]), 1)
        self.assertEqual(result["days"], result["trade_days_sample"])
        self.assertNotIn("results", result)
        self.assertEqual(result["request"]["lookback"], 30)
        self.assertEqual(len(result["viz"]["cumulative_pnl"]), 1)

    def test_missing_file_is_empty(self):
        from core.backtest_result_store import load_last_t0_backtest

        pack = load_last_t0_backtest(
            path=os.path.join(tempfile.gettempdir(), "no-such-last-t0-bt.json")
        )
        self.assertTrue(pack["empty"])
        self.assertFalse(pack["ok"])
        self.assertIsNone(pack["result"])


class TestLastT0BacktestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            cls.client = None
            return
        cls.client = TestClient(app)

    def test_get_empty(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        with patch.object(
            deps.quant,
            "load_last_t0_backtest",
            return_value={"ok": False, "empty": True, "result": None},
        ):
            res = self.client.get("/api/quant/last-t0-backtest")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["empty"])
        self.assertIsNone(data["result"])

    def test_get_saved(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        pack = {
            "ok": True,
            "empty": False,
            "saved_at": "2026-09-09T12:00:00",
            "result": {
                "success": True,
                "t0_pnl_total": 8.0,
                "t0_trade_days": 3,
            },
        }
        with patch.object(deps.quant, "load_last_t0_backtest", return_value=pack):
            res = self.client.get("/api/quant/last-t0-backtest")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["empty"])
        self.assertTrue(data["result"]["success"])
        self.assertEqual(data["result"]["t0_pnl_total"], 8.0)


class TestPersistLastT0Backtest(unittest.TestCase):
    def test_success_saves_snapshot(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake = {
            "success": True,
            "t0_pnl_total": 1.0,
            "from_holdings": True,
            "ok_count": 1,
            "results": [{"days": [1] * 10}],
        }
        with patch.object(
            svc,
            "_t0_holdings_for_backtest",
            return_value=([{"stock_code": "600519"}], {}),
        ), patch(
            "quant.research.t0_backtest.run_t0_backtest_for_holdings",
            return_value=dict(fake),
        ), patch(
            "core.backtest_result_store.save_last_t0_backtest",
        ) as save_ui:
            out = svc.run_t0_backtest(from_paper=True, lookback=20)
            self.assertTrue(out.get("success"))
            save_ui.assert_called()
            saved = save_ui.call_args[0][0]
            self.assertEqual(saved["request"]["lookback"], 20)
            self.assertTrue(saved["request"]["from_paper"])

    def test_failure_skips_snapshot(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake = {"success": False, "error": "无持仓可回测"}
        with patch.object(
            svc,
            "_t0_holdings_for_backtest",
            return_value=([], {}),
        ), patch(
            "quant.research.t0_backtest.run_t0_backtest_for_holdings",
            return_value=dict(fake),
        ), patch(
            "core.backtest_result_store.save_last_t0_backtest",
        ) as save_ui:
            out = svc.run_t0_backtest(from_paper=True)
            self.assertFalse(out.get("success"))
            save_ui.assert_not_called()


class TestStartT0BacktestJob(unittest.TestCase):
    def test_start_job_returns_background_and_finishes(self):
        import time

        from core.job_progress import t0_backtest_job
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake = {
            "success": True,
            "t0_pnl_total": 1.0,
            "from_holdings": True,
            "ok_count": 1,
        }
        if t0_backtest_job.is_running():
            t0_backtest_job.force_fail("test-reset")
        with patch.object(svc, "run_t0_backtest", return_value=dict(fake)):
            out = svc.start_t0_backtest_job(from_paper=True, lookback=10)
            self.assertTrue(out.get("background"))
            self.assertTrue(out.get("job", {}).get("id"))
            snap = None
            for _ in range(80):
                snap = t0_backtest_job.get()
                if snap.get("status") in ("done", "failed"):
                    break
                time.sleep(0.05)
            self.assertEqual((snap or {}).get("status"), "done")
            self.assertTrue((snap.get("result") or {}).get("success"))


class TestFillT0VizStockNames(unittest.TestCase):
    def test_fills_empty_point_names(self):
        from quant.research.t0_backtest import _fill_t0_viz_stock_names

        viz = {
            "cumulative_pnl": [{"date": "2026-09-01", "pnl": 1.0, "stock_code": "", "stock_name": ""}],
            "y_tau_scatter": [{"date": "2026-09-01", "y_tau": -0.5, "stock_code": "600584"}],
            "stock_contrib": [{"stock_code": "600584", "stock_name": ""}],
        }
        _fill_t0_viz_stock_names(viz, stock_code="600584", stock_name="长电科技")
        self.assertEqual(viz["cumulative_pnl"][0]["stock_code"], "600584")
        self.assertEqual(viz["cumulative_pnl"][0]["stock_name"], "长电科技")
        self.assertEqual(viz["y_tau_scatter"][0]["stock_name"], "长电科技")
        self.assertEqual(viz["stock_contrib"][0]["stock_name"], "长电科技")


if __name__ == "__main__":
    unittest.main()
