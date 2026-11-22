"""Unit tests for paper rebalance orchestrator (持仓规则日循环已下线)。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.paper.rebalance.orchestrator import (  # noqa: E402
    resolve_rebalance_mode,
    run_paper_rebalance,
)


class TestResolveRebalanceMode(unittest.TestCase):
    def test_always_holding_rules(self):
        paper = {"rules": {}}
        self.assertEqual(resolve_rebalance_mode(paper), "holding_rules")

class TestRunPaperRebalanceRouting(unittest.TestCase):
    def test_holding_rules_retired_does_not_trade(self):
        paper = {"holdings": [], "rules": {}}
        with patch("core.paper.cycle.run_daily_cycle") as mock_cycle:
            out = run_paper_rebalance(
                paper,
                mode="holding_rules",
                simulate_buy=True,
                strategy="short_conservative",
            )
        mock_cycle.assert_not_called()
        self.assertEqual(out["mode"], "holding_rules")
        self.assertFalse(out["ok"])
        self.assertTrue(out["retired"])

    def test_retired_modes_rejected(self):
        paper = {"holdings": [], "rules": {}}
        with self.assertRaises(ValueError) as ctx:
            run_paper_rebalance(paper, mode="cluster_book")  # type: ignore[arg-type]
        self.assertIn("rank_lots", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
