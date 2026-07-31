"""R0 · 产品北极星二级指标单测。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


class TestNorthStarMetrics(unittest.TestCase):
    def test_rolling_sharpe_and_calmar_on_synthetic_curve(self):
        from core.north_star import compute_paper_risk_metrics

        base = datetime(2026, 1, 1)
        equity = 1_000_000.0
        snaps = []
        # 上涨后回撤再恢复，保证夏普与卡玛均可算
        rets = [0.01] * 10 + [-0.02] * 5 + [0.015] * 15
        for i, r in enumerate(rets):
            equity *= 1.0 + r
            snaps.append(
                {
                    "ts": (base + timedelta(days=i)).isoformat(timespec="seconds"),
                    "equity": round(equity, 2),
                }
            )
        out = compute_paper_risk_metrics(snaps, window=60)
        self.assertEqual(out["status"], "ok")
        self.assertIsNotNone(out["rolling_sharpe"])
        self.assertIsNotNone(out["calmar"])
        self.assertGreater(out["sample_count"], 10)

    def test_short_snapshots_unavailable(self):
        from core.north_star import compute_paper_risk_metrics

        out = compute_paper_risk_metrics(
            [{"ts": "2026-01-01", "equity": 100}, {"ts": "2026-01-02", "equity": 101}],
            window=60,
        )
        self.assertEqual(out["status"], "unavailable")
        self.assertIsNone(out["rolling_sharpe"])

    def test_realization_corr_perfect_align(self):
        from core.north_star import compute_realization

        base = datetime(2026, 1, 1)
        paper = []
        bt = []
        eq = 100.0
        rets = [0.01, -0.005, 0.008, 0.002, -0.01, 0.012, 0.003, -0.004, 0.007, 0.001] * 2
        for i, r in enumerate(rets):
            eq *= 1.0 + r
            d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            paper.append({"ts": f"{d}T15:00:00", "equity": eq})
            bt.append({"date": d, "equity": eq})
        out = compute_realization(paper, bt, window=60)
        self.assertEqual(out["status"], "ok")
        self.assertIsNotNone(out["corr"])
        self.assertGreaterEqual(out["corr"], 0.99)
        self.assertIsNotNone(out["tracking_error_pct"])
        self.assertLess(out["tracking_error_pct"], 0.5)

    def test_realization_no_overlap(self):
        from core.north_star import compute_realization

        paper = [
            {"ts": f"2026-01-{i+1:02d}T12:00:00", "equity": 100 + i} for i in range(10)
        ]
        bt = [{"date": f"2025-01-{i+1:02d}", "equity": 100 + i} for i in range(10)]
        out = compute_realization(paper, bt)
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["reason"], "no_date_overlap")

    def test_ttm_median_pairs(self):
        from core.north_star import (
            TTM_EVENT_BACKTEST,
            TTM_EVENT_IDEA,
            TTM_EVENT_PAPER,
            compute_ttm_metrics,
        )

        t0 = datetime(2026, 1, 1, 10, 0, 0)
        events = [
            {"ts": t0.isoformat(), "event": TTM_EVENT_IDEA},
            {
                "ts": (t0 + timedelta(hours=2)).isoformat(),
                "event": TTM_EVENT_BACKTEST,
            },
            {
                "ts": (t0 + timedelta(hours=5)).isoformat(),
                "event": TTM_EVENT_PAPER,
            },
        ]
        out = compute_ttm_metrics(events)
        self.assertTrue(out["ok"])
        self.assertEqual(out["median_idea_to_backtest_hours"], 2.0)
        self.assertEqual(out["median_backtest_to_paper_hours"], 3.0)
        self.assertEqual(out["median_idea_to_paper_hours"], 5.0)

    def test_risk_block_summary(self):
        from core.north_star import summarize_risk_blocks

        logs = [
            {"type": "buy", "detail": "x"},
            {"type": "risk_block", "detail": "超限", "meta": {"code": "max_position"}},
            {"type": "risk_block", "detail": "超限", "meta": {"code": "max_position"}},
            {"type": "risk_block", "detail": "回撤", "meta": {"code": "drawdown"}},
        ]
        out = summarize_risk_blocks(logs)
        self.assertEqual(out["block_count"], 3)
        self.assertEqual(out["by_reason"]["max_position"], 2)
        self.assertIsNone(out["effectiveness_rate"])

    def test_save_load_backtest_curve(self):
        from core.north_star import load_last_backtest_curve, save_last_backtest_curve

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "curve.json")
            save_last_backtest_curve(
                [{"date": "2026-01-01", "equity": 1.0}, {"date": "2026-01-02", "equity": 1.01}],
                path=path,
                meta={"top_k": 3},
            )
            pack = load_last_backtest_curve(path=path)
            self.assertTrue(pack["ok"])
            self.assertEqual(len(pack["curve"]), 2)
            self.assertEqual(pack["meta"]["top_k"], 3)

    def test_build_north_star_report_shape(self):
        from core.north_star import build_north_star_report

        base = datetime(2026, 1, 1)
        equity = 1_000_000.0
        snaps = []
        for i in range(25):
            equity *= 1.001
            snaps.append(
                {
                    "ts": (base + timedelta(days=i)).isoformat(timespec="seconds"),
                    "equity": round(equity, 2),
                }
            )
        paper = {
            "snapshots": snaps,
            "operation_log": [{"type": "risk_block", "detail": "x", "meta": {"code": "x"}}],
        }
        report = build_north_star_report(paper, backtest_curve=[])
        self.assertTrue(report["ok"])
        self.assertIn("paper_risk", report)
        self.assertIn("realization", report)
        self.assertIn("ttm", report)
        self.assertIn("risk_blocks", report)
        self.assertEqual(report["risk_blocks"]["block_count"], 1)

    def test_append_ttm_event_file(self):
        from core.north_star import TTM_EVENT_IDEA, append_ttm_event, load_ttm_events

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ttm.jsonl")
            append_ttm_event(TTM_EVENT_IDEA, ref="test", path=path)
            rows = load_ttm_events(path=path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event"], TTM_EVENT_IDEA)

    def test_north_star_api_route(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")
        client = TestClient(app)
        fake = {
            "ok": True,
            "cached": False,
            "north_star": {
                "ok": True,
                "paper_risk": {"status": "unavailable", "rolling_sharpe": None},
                "realization": {"status": "unavailable", "corr": None},
                "ttm": {"status": "unavailable"},
                "risk_blocks": {"block_count": 0},
            },
        }
        with patch(
            "services.platform_service.PlatformService.get_north_star",
            return_value=fake,
        ):
            # deps.platform is already constructed — patch instance method
            import web.deps as deps

            with patch.object(deps.platform, "get_north_star", return_value=fake):
                res = client.get("/api/north-star")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("north_star", data)


if __name__ == "__main__":
    unittest.main()
