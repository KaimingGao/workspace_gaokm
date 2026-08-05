"""Unit tests for paper rebalance orchestrator mode routing (C3)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper_rebalance_orchestrator import (  # noqa: E402
    resolve_rebalance_mode,
    run_paper_rebalance,
)


class TestResolveRebalanceMode(unittest.TestCase):
    def test_defaults_to_cross_section(self):
        paper = {"rules": {}}
        with patch(
            "core.signal.cluster_live.get_cluster_scoring_cfg",
            return_value={"mode": "off"},
        ):
            self.assertEqual(resolve_rebalance_mode(paper), "cross_section")

    def test_cluster_scoring_active(self):
        paper = {"rules": {}}
        with patch(
            "core.signal.cluster_live.get_cluster_scoring_cfg",
            return_value={"mode": "active"},
        ):
            self.assertEqual(resolve_rebalance_mode(paper), "cluster_book")

    def test_cluster_mode_flag(self):
        paper = {"rules": {"cluster_mode": True}}
        with patch(
            "core.signal.cluster_live.get_cluster_scoring_cfg",
            return_value={"mode": "off"},
        ):
            self.assertEqual(
                resolve_rebalance_mode(paper, cluster_mode=False), "cluster_book"
            )


class TestRunPaperRebalanceRouting(unittest.TestCase):
    def test_holding_rules_delegates_to_daily_cycle(self):
        paper = {"holdings": [], "rules": {}}
        with patch(
            "core.paper_cycle.run_daily_cycle",
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
        with patch(
            "core.signal.cross_section.rank_cross_section",
            return_value={"success": True, "ranking": ranking},
        ), patch(
            "core.paper_rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ):
            out = run_paper_rebalance(paper, mode="cross_section", top_k=3)
        mock_sim.assert_called_once()
        self.assertEqual(out["mode"], "cross_section")
        self.assertFalse(out["cluster_mode"])
        self.assertEqual(out["ranking"], ranking)

    def test_cluster_book_delegates_to_cluster_rank(self):
        paper = {"holdings": [], "rules": {"max_positions": 5}, "strategy_id": "short"}
        ranking = [{"stock_code": "000001", "score": 0.02}]
        with patch(
            "core.signal.cluster_rank.rank_cluster_pools",
            return_value={
                "success": True,
                "book": ranking,
                "scored_all": ranking,
            },
        ), patch(
            "core.signal.cluster_live.assess_cluster_live_health",
            return_value={"alerts": []},
        ), patch(
            "core.paper_rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ), patch(
            "core.signal.score_display.selection_min_score",
            return_value=-0.01,
        ):
            out = run_paper_rebalance(paper, mode="cluster_book")
        mock_sim.assert_called_once()
        call_kw = mock_sim.call_args[1]
        self.assertFalse(call_kw["respect_max_positions"])
        self.assertEqual(out["mode"], "cluster_book")
        self.assertTrue(out["cluster_mode"])


if __name__ == "__main__":
    unittest.main()
