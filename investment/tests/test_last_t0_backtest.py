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
            "days": [{"date": "2026-08-01", "skipped": True}] * 40,
            "viz": {"cumulative_pnl": [{"date": "2026-09-01", "pnl": 1.0}]},
            "results": [{"stock_code": "600519", "days": [{"x": i} for i in range(80)]}],
            "request": {"lookback": 30, "from_paper": True},
            "trade_days_sample": [
                {
                    "date": "2026-09-01",
                    "pnl": 1.0,
                    "sold_qty": 100,
                    "close_band_scan": [
                        {
                            "hm": "09:35",
                            "c": 10.1,
                            "y_τ30": 0.2,
                            "y_t30_realized": 0.4,
                            "formula_terms_t30": {"terms": [{"key": "gap_pct"}]},
                            "features_tau": {"gap_pct": 1.0},
                        },
                        {
                            "hm": "09:40",
                            "c": 10.2,
                            "leg1": True,
                            "y_τ30": 0.3,
                            "formula_terms_t30": {
                                "terms": [{"key": "ret_last_30m", "contrib": 0.2}]
                            },
                            "features_tau": {"ret_last_30m": 1.1},
                        },
                    ],
                }
            ],
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
        self.assertEqual(len(result["days"]), 1)
        self.assertNotIn("trade_days_sample", result)
        self.assertNotIn("results", result)
        scan = (result["days"][0].get("close_band_scan") or [None])[0]
        self.assertEqual(scan["hm"], "09:35")
        self.assertEqual(scan["y_τ30"], 0.2)
        self.assertEqual(scan["formula_terms_t30"]["terms"][0]["key"], "gap_pct")
        self.assertNotIn("features_tau", scan)
        trigger = (result["days"][0].get("close_band_scan") or [None, None])[1]
        self.assertEqual(trigger["hm"], "09:40")
        self.assertTrue(trigger["leg1"])
        self.assertEqual(
            trigger["formula_terms_t30"]["terms"][0]["key"], "ret_last_30m"
        )
        self.assertAlmostEqual(trigger["features_tau"]["ret_last_30m"], 1.1, places=6)
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

    def test_single_code_backtest_passes_paper_for_cs_pool(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        paper = {"holdings": [{"stock_code": "600183"}, {"stock_code": "600875"}]}
        with patch.object(
            svc,
            "_t0_holdings_for_backtest",
            return_value=(paper["holdings"], paper),
        ) as hold_fn, patch(
            "quant.research.t0_backtest.run_t0_backtest_for_code",
            return_value={"success": True, "stock_code": "600183"},
        ) as run, patch(
            "core.backtest_result_store.save_last_t0_backtest",
        ):
            out = svc.run_t0_backtest(code="600183", lookback=10)
        hold_fn.assert_called_with()
        self.assertIs(run.call_args.kwargs.get("paper"), paper)
        self.assertTrue(out.get("success"))


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
