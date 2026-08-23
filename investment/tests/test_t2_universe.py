"""T2：验证宇宙接入回测候选。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestResolveReplayCandidates(unittest.TestCase):
    def test_explicit_codes(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        out = resolve_replay_candidates(["600519", " 600036 "])
        self.assertEqual(out["codes"], ["600519", "600036"])
        self.assertEqual(out["source"], "explicit")

    def test_watching_minus_exclude(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        with patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["A", "B", "C"]},
        ):
            with patch(
                "core.validation_universe.load_validation_universe",
                return_value={
                    "exclude_codes": ["B"],
                    "include_only": [],
                    "version": 1,
                    "note": "",
                },
            ):
                out = resolve_replay_candidates(None)
        self.assertEqual(out["codes"], ["A", "C"])
        self.assertEqual(out["source"], "watching_minus_exclude")
        self.assertEqual(out["watching_count"], 3)

    def test_param_grid_has_apply_gate(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(
                {
                    "600519": [{"date": "2026-01-01", "close": 10}] * 20,
                    "600036": [{"date": "2026-01-01", "close": 10}] * 20,
                },
                [],
                {},
            ),
        ), patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            return_value={
                "success": True,
                "metrics": {"total_return_pct": 1.0, "max_drawdown_pct": 1.0},
                "trade_count": 1,
            },
        ):
            out = svc.run_param_grid(
                codes=["600519", "600036"],
                top_k_values=[2],
                lookback_values=[60],
                max_cells=1,
                exclude_st=False,
            )
        self.assertTrue(out["success"])
        gate = out.get("apply_best_gate") or {}
        self.assertFalse(gate.get("allowed"))
        self.assertIn("OOS", gate.get("reason") or "")


if __name__ == "__main__":
    unittest.main()
