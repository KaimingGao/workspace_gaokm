"""Unit tests for paper rebalance orchestrator (日循环 holding_rules)。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper.rebalance.orchestrator import (  # noqa: E402
    resolve_rebalance_mode,
    run_paper_rebalance,
)


class TestResolveRebalanceMode(unittest.TestCase):
    def test_always_holding_rules(self):
        paper = {"rules": {}}
        self.assertEqual(resolve_rebalance_mode(paper), "holding_rules")


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
                strategy="short_conservative",
            )
        mock_cycle.assert_called_once()
        self.assertEqual(out["mode"], "holding_rules")
        self.assertTrue(out["ok"])

    def test_retired_modes_rejected(self):
        paper = {"holdings": [], "rules": {}}
        with self.assertRaises(ValueError) as ctx:
            run_paper_rebalance(paper, mode="cluster_book")  # type: ignore[arg-type]
        self.assertIn("rank_lots", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
