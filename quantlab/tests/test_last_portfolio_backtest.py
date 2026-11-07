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
            "sim_trades": [
                {
                    "stock_code": "600519",
                    "action": "buy",
                    "features_tau": {"raw_atc_YKMID": 1.0},
                    "factor_coefficients": {"momentum": 0.2},
                    "formula_terms": {
                        "total": 0.01,
                        "terms": [
                            {
                                "key": f"f{i}",
                                "beta": 0.1,
                                "z": 0.2,
                                "contrib": float(i),
                            }
                            for i in range(40)
                        ],
                    },
                }
            ],
            "stock_contrib": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "pnl": 1200.0,
                    "contrib_pct": 0.6,
                    "hold_days": 8,
                }
            ],
            "request": {"lookback": 30, "fuse_enter": 0.012, "fusion_w_trade": 0.6},
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
        self.assertEqual(result["sim_trades"][0]["stock_code"], "600519")
        self.assertNotIn("features_tau", result["sim_trades"][0])
        self.assertNotIn("factor_coefficients", result["sim_trades"][0])
        # 成交 tip 因子表：保留 capped 组成（与 tip_explain_fields 同帽）
        ft = result["sim_trades"][0].get("formula_terms") or {}
        self.assertEqual(len(ft.get("terms") or []), 24)
        self.assertEqual((ft.get("terms") or [])[0].get("key"), "f39")
        self.assertEqual(result["sim_trades"][0]["action"], "buy")
        self.assertEqual(result["stock_contrib"][0]["stock_code"], "600519")
        self.assertEqual(result["stock_contrib"][0]["pnl"], 1200.0)
        self.assertEqual(result["request"]["lookback"], 30)
        self.assertNotIn("trades", result)
        self.assertNotIn("score_ic", result)
        self.assertNotIn("filter_dropped", result["universe"])
        self.assertEqual(result["universe"]["loaded_count"], 2)
        self.assertEqual((result.get("skip_summary") or {}).get("total"), 0)

    def test_save_samples_skips_drops_formula(self):
        from core.backtest_result_store import (
            load_last_portfolio_backtest,
            save_last_portfolio_backtest,
        )

        skips = []
        for i in range(120):
            skips.append(
                {
                    "stock_code": f"{i:06d}",
                    "status": "skipped",
                    "action": "skip",
                    "reason": "分档外·非A" if i < 80 else "现金不足",
                    "formula_terms": {
                        "total": 0.01,
                        "terms": [{"key": f"f{j}", "contrib": float(j)} for j in range(30)],
                    },
                }
            )
        raw = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 1, "skip_count": 120},
            "equity_curve": [{"date": "2026-01-10", "equity": 100.0}],
            "sim_trades": [
                {
                    "stock_code": "600519",
                    "status": "filled",
                    "action": "buy",
                    "formula_terms": {
                        "total": 0.02,
                        "terms": [{"key": f"g{j}", "contrib": 1.0} for j in range(10)],
                    },
                },
                *skips,
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "last.json")
            save_last_portfolio_backtest(raw, path=path)
            pack = load_last_portfolio_backtest(path)
        result = pack["result"]
        ss = result["skip_summary"]
        self.assertEqual(ss["total"], 120)
        self.assertTrue(ss["truncated"])
        self.assertEqual(ss["by_reason"].get("分档外"), 80)
        self.assertEqual(ss["by_reason"].get("现金/手数"), 40)
        self.assertLessEqual(ss["kept"], 400)
        self.assertEqual(result["metrics"]["skip_count"], 120)
        filled = [r for r in result["sim_trades"] if r.get("status") == "filled"]
        skipped = [r for r in result["sim_trades"] if r.get("status") == "skipped"]
        self.assertEqual(len(filled), 1)
        self.assertTrue(filled[0].get("formula_terms"))
        self.assertTrue(skipped)
        self.assertTrue(all("formula_terms" not in r for r in skipped))
        # 现金类优先于分档外
        self.assertGreaterEqual(
            sum(1 for r in skipped if "现金" in str(r.get("reason") or "")),
            1,
        )

    def test_double_slim_preserves_skip_summary_total(self):
        from core.backtest_result_store import slim_portfolio_backtest_result

        skips = [
            {
                "stock_code": f"{i:06d}",
                "status": "skipped",
                "action": "skip",
                "reason": "分档外·非A" if i < 60 else "fuse=0.01%<0.2% 未过入场",
            }
            for i in range(90)
        ]
        raw = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 1, "skip_count": 90},
            "sim_trades": [
                {"stock_code": "600519", "status": "filled", "action": "buy"},
                *skips,
            ],
        }
        once = slim_portfolio_backtest_result(raw)
        self.assertEqual(once["skip_summary"]["total"], 90)
        twice = slim_portfolio_backtest_result(once)
        self.assertEqual(twice["skip_summary"]["total"], 90)
        self.assertEqual(twice["metrics"]["skip_count"], 90)
        self.assertEqual(twice["skip_summary"]["by_reason"].get("分档外"), 60)

    def test_classify_replay_skip_reason_matches_js(self):
        from core.persistence.result_snapshot import classify_replay_skip_reason

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js_path = os.path.join(root, "web", "static", "js", "quant", "bt_result.js")
        js = open(js_path, encoding="utf-8").read()
        self.assertIn("export function classifyReplaySkipReason", js)
        self.assertIn('r.includes("可预测性")', js)
        cases = [
            ("", "分档外"),
            ("hard_reject", "分档外"),
            ("可预测性非A档", "分档外"),
            ("分档外·非A", "分档外"),
            ("fuse=0.1%<0.2% 未过入场", "fuse未过"),
            ("fuse 缺失", "fuse未过"),
            ("y_oo≤0", "y_oo≤0"),
            ("y_τc≤0", "y_τc≤0"),
            ("y_tc≤0", "y_τc≤0"),
            ("rank>100", "名次"),
            ("现金不足", "现金/手数"),
            ("T+1 不可卖", "T+1"),
            ("无有效报价", "报价"),
            ("其它原因xyz", "其他"),
        ]
        for reason, want in cases:
            self.assertEqual(
                classify_replay_skip_reason(reason),
                want,
                msg=f"reason={reason!r}",
            )

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
        # 勿 force_fail 真实运行中的槽（会冲掉 UI 进度）
        if portfolio_backtest_job.is_running():
            self.skipTest("portfolio_backtest_job already running")
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
