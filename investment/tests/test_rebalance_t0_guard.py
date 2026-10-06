"""调仓卖出 · 未平做 T 腿保护。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.t0.intraday import (
    PHASE_AFTER_LEG1,
    PHASE_DONE,
    PHASE_IDLE,
    REBALANCE_T0_BLOCK_SELL_THEN_BUY,
    REBALANCE_T0_BLOCK_BUY_THEN_SELL,
    open_t0_leg_rebalance_block,
)


class TestHoldingT0IntradayStatus(unittest.TestCase):
    def test_after_leg1_buy_then_sell(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status(
            {"phase": "after_leg1", "direction": "buy_then_sell", "legs_written": 1}
        )
        self.assertEqual(st["badge"], "正T·一腿")
        self.assertIn("已买待卖旧仓", st["title"])

    def test_after_leg1_sell_then_buy(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status(
            {"phase": "after_leg1", "direction": "sell_then_buy", "legs_written": 1}
        )
        self.assertEqual(st["badge"], "反T·一腿")
        self.assertIn("已卖待回补", st["title"])

    def test_idle_with_direction(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status({"phase": "idle", "direction": "sell_then_buy"})
        self.assertEqual(st["badge"], "反T·盯")

    def test_idle_without_direction_hidden(self):
        from core.t0.intraday import holding_t0_intraday_status

        self.assertIsNone(holding_t0_intraday_status({"phase": "idle"}))


class TestOpenT0LegRebalanceBlock(unittest.TestCase):
    def test_buy_then_sell_after_leg1_blocks(self):
        st = {
            "phase": PHASE_AFTER_LEG1,
            "direction": "buy_then_sell",
            "legs_written": 1,
        }
        self.assertEqual(open_t0_leg_rebalance_block(st), REBALANCE_T0_BLOCK_BUY_THEN_SELL)

    def test_sell_then_buy_after_leg1_blocks(self):
        st = {
            "phase": PHASE_AFTER_LEG1,
            "direction": "sell_then_buy",
            "legs_written": 1,
        }
        self.assertEqual(open_t0_leg_rebalance_block(st), REBALANCE_T0_BLOCK_SELL_THEN_BUY)

    def test_idle_before_first_leg_allows_rebalance(self):
        st = {"phase": PHASE_IDLE, "direction": "buy_then_sell", "legs_written": 0}
        self.assertIsNone(open_t0_leg_rebalance_block(st))

    def test_done_allows_rebalance(self):
        st = {"phase": PHASE_DONE, "direction": "sell_then_buy", "legs_written": 2}
        self.assertIsNone(open_t0_leg_rebalance_block(st))


if __name__ == "__main__":
    unittest.main()
