"""Unit tests for paper rebalance orchestrator mode routing (C3)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper.rebalance.orchestrator import (  # noqa: E402
    resolve_rebalance_mode,
    run_paper_rebalance,
)


class TestResolveRebalanceMode(unittest.TestCase):
    def test_defaults_to_cross_section(self):
        paper = {"rules": {}}
        self.assertEqual(resolve_rebalance_mode(paper), "cross_section")

    def test_cluster_scoring_active_no_longer_routes_book(self):
        paper = {"rules": {}}
        with patch(
            "core.signal.cluster.live.get_cluster_scoring_cfg",
            return_value={"mode": "active"},
        ):
            self.assertEqual(resolve_rebalance_mode(paper), "cross_section")

    def test_cluster_mode_flag_ignored(self):
        paper = {"rules": {"cluster_mode": True}}
        self.assertEqual(
            resolve_rebalance_mode(paper, cluster_mode=True), "cross_section"
        )


class TestRunPaperRebalanceRouting(unittest.TestCase):
    def test_holding_rules_delegates_to_daily_cycle(self):
        paper = {"holdings": [], "rules": {}}
        with patch(
            "core.paper.cycle.run_daily_cycle",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_cycle:
            out = run_paper_rebalance(
                paper,
                mode="holding_rules",
                simulate_buy=True,
                strategy="short",
            )
        mock_cycle.assert_called_once()
        self.assertEqual(out["mode"], "holding_rules")
        self.assertTrue(out["ok"])

    def test_cross_section_delegates_to_simulator(self):
        paper = {"holdings": [], "rules": {"max_positions": 5}, "strategy_id": "short"}
        ranking = [{"stock_code": "600519", "score": 80}]

        class _Book:
            def as_dict(self):
                return {"success": True, "ranking": ranking}

        mock_svc = MagicMock()
        mock_svc.rank_cross_section.return_value = _Book()
        with patch(
            "core.signal.service.get_default_signal_service",
            return_value=mock_svc,
        ), patch(
            "core.paper.rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ):
            out = run_paper_rebalance(paper, mode="cross_section", top_k=3)
        mock_sim.assert_called_once()
        mock_svc.rank_cross_section.assert_called_once()
        self.assertEqual(out["mode"], "cross_section")
        self.assertFalse(out["cluster_mode"])
        self.assertEqual(out["ranking"], ranking)

    def test_cluster_book_mode_rejected(self):
        paper = {"holdings": [], "rules": {}, "strategy_id": "short"}
        out = run_paper_rebalance(paper, mode="cluster_book", dry_run=True)
        self.assertFalse(out.get("success") or out.get("ok"))
        self.assertEqual(out.get("mode"), "cluster_book")
        self.assertIn("停用", str(out.get("error") or ""))


class TestSupplementHoldingScores(unittest.TestCase):
    def test_noop_when_all_present(self):
        from core.paper.rebalance.orchestrator import _supplement_holding_scores

        rows = [{"stock_code": "000001", "score": 1.0}]
        paper = {"holdings": [{"stock_code": "000001", "shares": 100}]}
        out = _supplement_holding_scores(paper, rows)
        self.assertEqual(len(out), 1)

    def test_scores_missing_holding(self):
        from core.paper.rebalance.orchestrator import _supplement_holding_scores

        paper = {
            "holdings": [
                {"stock_code": "000063", "stock_name": "中兴", "shares": 100},
            ]
        }
        with patch(
            "core.signal.score_stock.score_stock",
            return_value={
                "success": True,
                "signal_item": {
                    "stock_code": "000063",
                    "stock_name": "中兴通讯",
                    "score": 1.48,
                    "predicted_score": 1.48,
                },
            },
        ):
            out = _supplement_holding_scores(paper, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["stock_code"], "000063")
        self.assertEqual(out[0]["score"], 1.48)
        self.assertTrue(out[0].get("holding_supplement"))


if __name__ == "__main__":
    unittest.main()
