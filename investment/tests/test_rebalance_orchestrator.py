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


if __name__ == "__main__":
    unittest.main()
