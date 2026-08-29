"""调仓卖出 · 未平做 T 腿保护。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper.rebalance.sell import run_sell_leg
from core.paper.rebalance.state import RebalanceState
from core.t0.intraday import (
    PHASE_AFTER_LEG1,
    PHASE_DONE,
    PHASE_IDLE,
    REBALANCE_T0_BLOCK_LONG,
    REBALANCE_T0_BLOCK_REVERSE,
    open_t0_leg_rebalance_block,
)


class TestHoldingT0IntradayStatus(unittest.TestCase):
    def test_after_leg1_reverse(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status(
            {"phase": "after_leg1", "direction": "reverse_t", "legs_written": 1}
        )
        self.assertEqual(st["badge"], "正T·一腿")
        self.assertIn("已买待卖旧仓", st["title"])

    def test_after_leg1_long(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status(
            {"phase": "after_leg1", "direction": "long_t", "legs_written": 1}
        )
        self.assertEqual(st["badge"], "反T·一腿")
        self.assertIn("已卖待回补", st["title"])

    def test_idle_with_direction(self):
        from core.t0.intraday import holding_t0_intraday_status

        st = holding_t0_intraday_status({"phase": "idle", "direction": "long_t"})
        self.assertEqual(st["badge"], "反T·盯")

    def test_idle_without_direction_hidden(self):
        from core.t0.intraday import holding_t0_intraday_status

        self.assertIsNone(holding_t0_intraday_status({"phase": "idle"}))


class TestOpenT0LegRebalanceBlock(unittest.TestCase):
    def test_reverse_t_after_leg1_blocks(self):
        st = {
            "phase": PHASE_AFTER_LEG1,
            "direction": "reverse_t",
            "legs_written": 1,
        }
        self.assertEqual(open_t0_leg_rebalance_block(st), REBALANCE_T0_BLOCK_REVERSE)

    def test_long_t_after_leg1_blocks(self):
        st = {
            "phase": PHASE_AFTER_LEG1,
            "direction": "long_t",
            "legs_written": 1,
        }
        self.assertEqual(open_t0_leg_rebalance_block(st), REBALANCE_T0_BLOCK_LONG)

    def test_idle_before_first_leg_allows_rebalance(self):
        st = {"phase": PHASE_IDLE, "direction": "reverse_t", "legs_written": 0}
        self.assertIsNone(open_t0_leg_rebalance_block(st))

    def test_done_allows_rebalance(self):
        st = {"phase": PHASE_DONE, "direction": "long_t", "legs_written": 2}
        self.assertIsNone(open_t0_leg_rebalance_block(st))


class TestRunSellLegT0Guard(unittest.TestCase):
    def _minimal_state(self, paper):
        from core.paper.rebalance.state import ScoreIndexes

        codes = set()
        indexes = ScoreIndexes(
            top_items=[],
            top_codes=set(),
            item_by_code={},
            trade_score_by_code={"600000": 30.0},
            eod_score_by_code={},
            tau_by_code={},
            hard_reject_by_code={"600000": "测试硬拒绝"},
        )
        return RebalanceState(
            paper=paper,
            ranking=[],
            rules={},
            cost_model="zero",
            fee_params={},
            top_k=10,
            min_score=55.0,
            min_hold_score=55.0,
            max_positions=10,
            position_pct=0.15,
            max_turnover_pct=100.0,
            respect_max_positions=True,
            skip_sentiment_prior=True,
            skip_market_prior=True,
            tracks_cfg={},
            indexes=indexes,
            holdings=list(paper.get("holdings") or []),
            cash=float(paper.get("cash") or 0),
            equity_before=100000.0,
            prior_by_code={},
            prior_cfg_live={"mode": "off"},
            quote_cache={
                "600000": {
                    "success": True,
                    "stock_code": "600000",
                    "price_raw": 10.0,
                    "prev_close": 10.0,
                }
            },
            sector_breadth_by_code={},
        )

    @patch("core.paper.rebalance.sell.load_rebalance_t0_sell_blocks")
    def test_main_sell_skips_reverse_t_open_leg(self, load_blocks):
        load_blocks.return_value = {"600000": REBALANCE_T0_BLOCK_REVERSE}
        paper = {
            "holdings": [
                {
                    "stock_code": "600000",
                    "stock_name": "浦发银行",
                    "shares": 1000,
                    "cost": 9.0,
                    "lots": [{"shares": 1000, "bought_date": "2026-08-20"}],
                }
            ],
            "cash": 50000.0,
            "trades": [],
        }
        state = self._minimal_state(paper)
        run_sell_leg(state)
        self.assertEqual(len(state.sell_trades), 0)
        self.assertEqual(len(state.kept), 1)
        self.assertTrue(
            any(
                s.get("stock_code") == "600000" and s.get("path") == "t0_open_leg"
                for s in state.sell_match_skips
            )
        )

    @patch("core.paper.rebalance.sell.load_rebalance_t0_sell_blocks")
    def test_main_sell_skips_long_t_after_first_sell(self, load_blocks):
        load_blocks.return_value = {"600000": REBALANCE_T0_BLOCK_LONG}
        paper = {
            "holdings": [
                {
                    "stock_code": "600000",
                    "stock_name": "浦发银行",
                    "shares": 500,
                    "cost": 9.0,
                    "lots": [{"shares": 500, "bought_date": "2026-08-20"}],
                }
            ],
            "cash": 50000.0,
            "trades": [],
        }
        state = self._minimal_state(paper)
        run_sell_leg(state)
        self.assertEqual(len(state.sell_trades), 0)
        self.assertEqual(len(state.kept), 1)
        self.assertTrue(
            any(
                s.get("stock_code") == "600000" and s.get("path") == "t0_open_leg"
                for s in state.sell_match_skips
            )
        )


if __name__ == "__main__":
    unittest.main()
