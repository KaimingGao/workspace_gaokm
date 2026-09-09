"""QuantService 动作拆分与 action_map（策略 · 历史/前瞻验证 · 对比）。"""

from __future__ import annotations

import unittest

from quant.services.action_map import action_map
from quant.services.quant_service import QuantService
from quant.services.quant_service_portfolio import QuantPortfolioMixin
from quant.services.quant_service_replay import QuantReplayMixin


class TestQuantActions(unittest.TestCase):
    def test_action_map_order(self):
        m = action_map()
        labels = [a["label"] for a in m["actions"]]
        self.assertEqual(labels, ["策略", "回溯", "模拟", "联动"])
        self.assertIn("score_bars", " ".join(m["shared_spine"]))
        self.assertIn("信号", m.get("thesis") or "")
        self.assertEqual(
            m.get("flow"),
            ["观察/纸面", "策略", "回溯|模拟"],
        )
        self.assertIn("pages", (m.get("ui_layout") or {}))

    def test_service_exposes_follow_replay_compare(self):
        qs = QuantService()
        self.assertTrue(callable(qs.run_t0_backtest))
        self.assertTrue(callable(qs.run_portfolio_backtest))
        self.assertTrue(callable(qs.load_last_portfolio_backtest))
        self.assertTrue(callable(qs.load_last_t0_backtest))
        self.assertTrue(callable(qs.build_portfolio_bridge))
        self.assertFalse(hasattr(qs, "run_paper_vs_portfolio"))
        self.assertEqual(qs.action_map()["actions"][0]["id"], "strategy")

    def test_portfolio_mixin_alias(self):
        self.assertIs(QuantPortfolioMixin, QuantReplayMixin)


if __name__ == "__main__":
    unittest.main()
