"""参数网格：OOS 过门选优、不落盘北极星、跳过重附件。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_service_replay import (
    mark_equivalent_param_grid_cells,
    param_grid_cell_eligible,
    select_param_grid_best,
)


def _cell(
    *,
    lookback=120,
    top_k=20,
    success=True,
    total_return_pct=10.0,
    max_drawdown_pct=8.0,
    oos_ok=True,
    oos_failed=False,
    oos_return_pct=1.0,
    is_return_pct=12.0,
    trade_count=8,
):
    return {
        "lookback": lookback,
        "top_k": top_k,
        "success": success,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "oos_ok": oos_ok,
        "oos_failed": oos_failed,
        "oos_return_pct": oos_return_pct,
        "is_return_pct": is_return_pct,
        "trade_count": trade_count,
    }


class TestParamGridSelect(unittest.TestCase):
    def test_high_is_negative_oos_not_eligible(self):
        c = _cell(total_return_pct=40.0, oos_return_pct=-2.0, max_drawdown_pct=8.0)
        ok, reason = param_grid_cell_eligible(c)
        self.assertFalse(ok)
        self.assertEqual(reason, "oos_negative")

    def test_oos_ok_but_dd_over_not_eligible(self):
        c = _cell(oos_return_pct=1.0, max_drawdown_pct=18.0)
        ok, reason = param_grid_cell_eligible(c)
        self.assertFalse(ok)
        self.assertEqual(reason, "dd_over")

    def test_picks_higher_oos_not_higher_is(self):
        high_is = _cell(top_k=5, total_return_pct=30.0, oos_return_pct=0.2, max_drawdown_pct=9.0)
        high_oos = _cell(top_k=20, total_return_pct=12.0, oos_return_pct=2.5, max_drawdown_pct=9.0)
        best = select_param_grid_best([high_is, high_oos], canonical_lookback=120)
        self.assertIsNotNone(best)
        self.assertEqual(best["top_k"], 20)
        self.assertEqual(best["oos_return_pct"], 2.5)

    def test_ignores_short_lookback_even_if_oos_better(self):
        lucky60 = _cell(lookback=60, top_k=10, oos_return_pct=8.0, max_drawdown_pct=5.0)
        lb120 = _cell(lookback=120, top_k=20, oos_return_pct=0.4, max_drawdown_pct=10.0)
        best = select_param_grid_best([lucky60, lb120], canonical_lookback=120)
        self.assertEqual(best["lookback"], 120)
        self.assertEqual(best["top_k"], 20)

    def test_none_when_canonical_fails_even_if_short_window_passes(self):
        lucky60 = _cell(lookback=60, top_k=10, oos_return_pct=8.0, max_drawdown_pct=5.0)
        fail120 = _cell(lookback=120, top_k=20, oos_return_pct=-1.0, max_drawdown_pct=8.0)
        best = select_param_grid_best([lucky60, fail120], canonical_lookback=120)
        self.assertIsNone(best)

    def test_prefers_not_oos_failed_then_smaller_k_on_tie(self):
        gap_fail = _cell(top_k=10, oos_return_pct=2.0, oos_failed=True, max_drawdown_pct=8.0)
        clean = _cell(top_k=15, oos_return_pct=2.0, oos_failed=False, max_drawdown_pct=8.0)
        best = select_param_grid_best([gap_fail, clean], canonical_lookback=120)
        self.assertEqual(best["top_k"], 15)
        twin10 = _cell(top_k=10, oos_return_pct=2.0, oos_failed=False, max_drawdown_pct=8.0)
        twin20 = _cell(top_k=20, oos_return_pct=2.0, oos_failed=False, max_drawdown_pct=8.0)
        tied = select_param_grid_best([twin20, twin10], canonical_lookback=120)
        self.assertEqual(tied["top_k"], 10)

    def test_marks_equivalent_k_when_metrics_identical(self):
        a = _cell(top_k=15, oos_return_pct=1.0, total_return_pct=12.0, trade_count=9)
        b = _cell(top_k=20, oos_return_pct=1.0, total_return_pct=12.0, trade_count=9)
        mark_equivalent_param_grid_cells([a, b])
        self.assertEqual(a["equivalent_ks"], [15, 20])
        self.assertIn("填不满", a["equivalent_note"])
        self.assertEqual(b["equivalent_ks"], [15, 20])


class TestParamGrid(unittest.TestCase):
    def test_run_param_grid_picks_oos_gate_not_is_return(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        calls = []

        def fake_bt(**kwargs):
            calls.append(kwargs)
            tk = int(kwargs.get("top_k") or 0)
            # K=10：样本内最高，OOS 为负 → 不过门
            # K=15：OOS 过门但较低
            # K=20：OOS 最高且回撤合格
            by_k = {
                10: (40.0, 8.0, -3.0, False),
                15: (18.0, 9.0, 0.5, False),
                20: (16.0, 7.0, 1.2, False),
            }
            is_ret, dd, oos, failed = by_k[tk]
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": is_ret,
                    "max_drawdown_pct": dd,
                    "win_rate_pct": 50.0,
                    "trade_count": 8,
                },
                "oos_summary": {
                    "ok": True,
                    "failed": failed,
                    "is_return_pct": is_ret,
                    "oos_return_pct": oos,
                },
            }

        with patch.object(svc, "run_portfolio_backtest", side_effect=fake_bt):
            out = svc.run_param_grid(
                top_k_values=[10, 15, 20],
                lookback_values=[120],
                max_cells=4,
                horizon_days=3,
                min_predicted_score=0.4,
                weight_mode="score_budget",
            )
        self.assertTrue(out["success"])
        self.assertEqual(out["cell_count"], 3)
        self.assertEqual(out["eligible_count"], 2)
        self.assertEqual(len(calls), 3)
        self.assertFalse(calls[0].get("include_wf_slices"))
        self.assertFalse(calls[0].get("include_cost_compare"))
        self.assertFalse(calls[0].get("include_score_ic"))
        self.assertFalse(calls[0].get("include_quantile"))
        self.assertFalse(calls[0].get("include_benchmark"))
        self.assertFalse(calls[0].get("persist_curve"))
        self.assertEqual(calls[0].get("min_predicted_score"), 0.4)
        self.assertEqual(calls[0].get("horizon_days"), 3)
        best = out["best"]
        self.assertIsNotNone(best)
        self.assertEqual(best["lookback"], 120)
        self.assertEqual(best["top_k"], 20)
        self.assertEqual(best["oos_return_pct"], 1.2)
        self.assertEqual(out["heat"]["metric"], "oos_return_pct")
        self.assertTrue(out["apply_best_gate"]["allowed"])
        self.assertFalse(out.get("persist_curve"))
        self.assertTrue(calls[0].get("exclude_st"))

    def test_run_param_grid_no_best_when_all_fail_gate(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()

        def fake_bt(**kwargs):
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": 25.0,
                    "max_drawdown_pct": 8.0,
                    "trade_count": 4,
                },
                "oos_summary": {
                    "ok": True,
                    "failed": True,
                    "oos_return_pct": -2.0,
                    "is_return_pct": 30.0,
                },
            }

        with patch.object(svc, "run_portfolio_backtest", side_effect=fake_bt):
            out = svc.run_param_grid(
                top_k_values=[10, 20],
                lookback_values=[120],
                max_cells=2,
            )
        self.assertIsNone(out["best"])
        self.assertEqual(out["eligible_count"], 0)
        self.assertIn("无格满足", out["apply_best_gate"]["reason"])

    def test_apply_best_blocked_when_oos_gap_flag(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()

        def fake_bt(**kwargs):
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": 20.0,
                    "max_drawdown_pct": 8.0,
                    "trade_count": 6,
                },
                "oos_summary": {
                    "ok": True,
                    "failed": True,
                    "fail_reason": "oos_underperform_gap_-12.0pp",
                    "oos_return_pct": 0.4,
                    "is_return_pct": 18.0,
                },
            }

        with patch.object(svc, "run_portfolio_backtest", side_effect=fake_bt):
            out = svc.run_param_grid(
                top_k_values=[20],
                lookback_values=[120],
                max_cells=1,
            )
        self.assertIsNotNone(out["best"])
        self.assertTrue(out["best"]["eligible"])
        self.assertTrue(out["best"]["oos_failed"])
        self.assertFalse(out["apply_best_gate"]["allowed"])
        self.assertIn("缺口", out["apply_best_gate"]["reason"])

    def test_persist_curve_false_skips_north_star_save(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake_bt = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 1, "max_drawdown_pct": 2.0},
            "equity_curve": [{"date": "2026-01-01", "equity": 1.0}],
            "dropped_stocks": [],
        }
        bars = {"A": [{"date": "2026-01-01", "close": 10}] * 10, "B": [{"date": "2026-01-01", "close": 10}] * 10}
        with patch(
            "quant.services.quant_service_replay.resolve_replay_candidates",
            return_value={"ok": True, "codes": ["A", "B"], "source": "explicit", "excluded": []},
        ), patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(bars, [], {}),
        ), patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            return_value=dict(fake_bt),
        ), patch(
            "core.north_star.save_last_backtest_curve",
        ) as save, patch(
            "core.north_star.append_ttm_event",
        ), patch(
            "core.data_service.summarize_data_quality",
            return_value={},
        ), patch(
            "core.data_consistency.attach_source_audit",
            side_effect=lambda result, **kwargs: result,
        ):
            out = svc.run_portfolio_backtest(
                persist_curve=False,
                include_score_ic=False,
                include_quantile=False,
                include_benchmark=False,
                exclude_st=False,
            )
            self.assertTrue(out.get("success"))
            save.assert_not_called()
            out2 = svc.run_portfolio_backtest(
                persist_curve=True,
                include_score_ic=False,
                include_quantile=False,
                include_benchmark=False,
                exclude_st=False,
            )
            self.assertTrue(out2.get("success"))
            save.assert_called()

    def test_param_grid_route(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")
        client = TestClient(app)
        with patch(
            "web.deps.quant.run_param_grid",
            return_value={
                "success": True,
                "cells": [],
                "best": None,
                "cell_count": 0,
                "eligible_count": 0,
                "axes": {"lookback": [120], "top_k": [10]},
            },
        ):
            res = client.post("/api/quant/param-grid", json={"max_cells": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json().get("success"))


if __name__ == "__main__":
    unittest.main()
