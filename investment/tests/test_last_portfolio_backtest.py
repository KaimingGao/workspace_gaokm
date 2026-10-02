"""Last product backtest snapshot: save/load for /replay restore-on-refresh."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import web.deps as deps


class TestLastPortfolioBacktestStore(unittest.TestCase):
    def test_save_load_roundtrip_keeps_replay_fields(self):
        from core.backtest_result_store import (
            load_last_portfolio_backtest,
            save_last_portfolio_backtest,
        )

        raw = {
            "success": True,
            "metrics": {
                "total_return_pct": 1.2,
                "win_rate_pct": 55.0,
                "trade_count": 3,
                "day_count": 10,
                "hit_rate_pct": 60.0,
                "hit_n": 5,
                "hit_hits": 3,
            },
            "equity_curve": [{"date": "2026-01-10", "equity": 100.0}],
            "benchmark": {"ok": True, "excess_pct": 0.4, "benchmark_label": "沪深300"},
            "sim_trades": [{"stock_code": "600519", "action": "buy"}],
            "stock_contrib": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "pnl": 1200.0,
                    "contrib_pct": 0.6,
                    "hold_days": 8,
                }
            ],
            "request": {"lookback": 30, "rank_enter": 0.012, "fusion_w_trade": 0.6},
            "loaded_stocks": ["600519", "600036"],
            "universe": {
                "source": "watching",
                "candidate_count": 2,
                "loaded_count": 2,
                "filter_dropped": [{"code": "x"}] * 50,
                "note": "候选=全部观察池",
            },
            "trades": [{"nested": True}],
            "score_ic": {"ok": True, "ic_mean": 0.1},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "last.json")
            save_last_portfolio_backtest(raw, path=path)
            pack = load_last_portfolio_backtest(path)
        self.assertTrue(pack["ok"])
        self.assertFalse(pack["empty"])
        self.assertTrue(pack.get("saved_at"))
        result = pack["result"]
        self.assertEqual(result["metrics"]["total_return_pct"], 1.2)
        self.assertEqual(result["metrics"]["hit_rate_pct"], 60.0)
        self.assertEqual(result["metrics"]["day_count"], 10)
        self.assertEqual(len(result["sim_trades"]), 1)
        self.assertEqual(result["stock_contrib"][0]["stock_code"], "600519")
        self.assertEqual(result["stock_contrib"][0]["pnl"], 1200.0)
        self.assertEqual(result["request"]["lookback"], 30)
        self.assertNotIn("trades", result)
        self.assertNotIn("score_ic", result)
        self.assertNotIn("filter_dropped", result["universe"])
        self.assertEqual(result["universe"]["loaded_count"], 2)

    def test_missing_file_is_empty(self):
        from core.backtest_result_store import load_last_portfolio_backtest

        pack = load_last_portfolio_backtest(path=os.path.join(tempfile.gettempdir(), "no-such-last-bt.json"))
        self.assertTrue(pack["empty"])
        self.assertFalse(pack["ok"])
        self.assertIsNone(pack["result"])


class TestLastPortfolioBacktestApi(unittest.TestCase):
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
            "load_last_portfolio_backtest",
            return_value={"ok": False, "empty": True, "result": None},
        ):
            res = self.client.get("/api/quant/last-portfolio-backtest")
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
            "saved_at": "2026-09-08T12:00:00",
            "result": {
                "success": True,
                "metrics": {"total_return_pct": 2.0},
                "equity_curve": [{"date": "2026-01-10", "equity": 102}],
            },
        }
        with patch.object(deps.quant, "load_last_portfolio_backtest", return_value=pack):
            res = self.client.get("/api/quant/last-portfolio-backtest")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["empty"])
        self.assertTrue(data["result"]["success"])
        self.assertEqual(data["result"]["metrics"]["total_return_pct"], 2.0)


class TestPersistLastPortfolioBacktest(unittest.TestCase):
    def test_persist_curve_false_skips_ui_snapshot(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake_bt = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 1, "max_drawdown_pct": 2.0},
            "equity_curve": [{"date": "2026-01-01", "equity": 1.0}],
            "sim_trades": [],
        }
        bars = {
            "A": [{"date": "2026-01-01", "close": 10}] * 10,
            "B": [{"date": "2026-01-01", "close": 10}] * 10,
        }
        with patch(
            "quant.services.quant_service_replay.resolve_replay_candidates",
            return_value={
                "ok": True,
                "codes": ["A", "B"],
                "source": "explicit",
                "excluded": [],
            },
        ), patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            return_value=dict(fake_bt),
        ), patch(
            "core.backtest.paper_replay.load_replay_minute_bars",
            return_value=({}, {"ok": True, "covered": 0, "missing": []}),
        ), patch(
            "core.north_star.save_last_backtest_curve",
        ), patch(
            "core.north_star.append_ttm_event",
        ), patch(
            "core.backtest_result_store.save_last_portfolio_backtest",
        ) as save_ui, patch(
            "core.data.facade.summarize_data_quality",
            return_value={},
        ), patch(
            "core.data.consistency.attach_source_audit",
            side_effect=lambda result, **kwargs: result,
        ):
            out = svc.run_portfolio_backtest(
                persist_curve=False,
                include_benchmark=False,
                exclude_st=False,
            )
            self.assertTrue(out.get("success"))
            save_ui.assert_not_called()
            out2 = svc.run_portfolio_backtest(
                persist_curve=True,
                include_benchmark=False,
                exclude_st=False,
            )
            self.assertTrue(out2.get("success"))
            save_ui.assert_called()


class TestStartPortfolioBacktestJob(unittest.TestCase):
    def test_start_job_returns_background_and_finishes(self):
        import time

        from core.job_progress import portfolio_backtest_job
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake = {
            "success": True,
            "metrics": {"trade_count": 1, "total_return_pct": 1.0},
            "equity_curve": [{"date": "2026-01-10", "equity": 100.0}],
        }
        if portfolio_backtest_job.is_running():
            portfolio_backtest_job.force_fail("test-reset")
        with patch.object(svc, "run_portfolio_backtest", return_value=dict(fake)):
            out = svc.start_portfolio_backtest_job(lookback=10, include_benchmark=False)
            self.assertTrue(out.get("background"))
            self.assertTrue(out.get("job", {}).get("id"))
            snap = None
            for _ in range(80):
                snap = portfolio_backtest_job.get()
                if snap.get("status") in ("done", "failed"):
                    break
                time.sleep(0.05)
            self.assertEqual((snap or {}).get("status"), "done")
            self.assertTrue((snap.get("result") or {}).get("success"))
