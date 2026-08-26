"""A 股纸面 T+1：批次 FIFO 与卖出锁定。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper.tplus1 import (
    TPLUS1_LOCK_REASON,
    add_buy_lot,
    apply_t0_trades,
    clip_sell_shares,
    consume_sell_lots,
    locked_shares,
    sellable_shares,
    stamp_new_holding,
)


class TestTplus1Lots(unittest.TestCase):
    def test_same_day_buy_locked(self):
        h = {"shares": 100, "bought_at": "2026-08-21T10:00:00"}
        stamp_new_holding(h, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        self.assertEqual(sellable_shares(h, as_of="2026-08-21"), 0)
        self.assertEqual(locked_shares(h, as_of="2026-08-21"), 100)
        qty, meta = clip_sell_shares(h, 100, as_of="2026-08-21")
        self.assertEqual(qty, 0)
        self.assertEqual(meta["reason"], TPLUS1_LOCK_REASON)

    def test_next_trading_day_unlocks(self):
        h = {"shares": 100, "bought_at": "2026-08-21T10:00:00"}
        stamp_new_holding(h, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        self.assertEqual(sellable_shares(h, as_of="2026-08-24"), 100)
        qty, _ = clip_sell_shares(h, 100, as_of="2026-08-24")
        self.assertEqual(qty, 100)

    def test_weekend_still_locked_after_friday_buy(self):
        h = {"shares": 100, "bought_at": "2026-08-21T10:00:00"}
        stamp_new_holding(h, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        self.assertEqual(sellable_shares(h, as_of="2026-08-22"), 0)
        self.assertEqual(sellable_shares(h, as_of="2026-08-23"), 0)

    def test_mixed_lots_only_old_sellable(self):
        h = {"shares": 0, "lots": []}
        add_buy_lot(h, 500, ts="2026-08-20T10:00:00", as_of="2026-08-20")
        add_buy_lot(h, 200, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        self.assertEqual(h["shares"], 700)
        self.assertEqual(sellable_shares(h, as_of="2026-08-21"), 500)
        sold = consume_sell_lots(h, 500, as_of="2026-08-21")
        self.assertEqual(sold, 500)
        self.assertEqual(h["shares"], 200)
        self.assertEqual(sellable_shares(h, as_of="2026-08-21"), 0)

    def test_grandfather_missing_bought_at_is_sellable(self):
        h = {"shares": 100}
        self.assertEqual(sellable_shares(h, as_of="2026-08-21"), 100)

    def test_t0_buyback_locks_new_shares(self):
        h = {"shares": 0, "lots": []}
        add_buy_lot(h, 1000, ts="2026-08-20T10:00:00", as_of="2026-08-20")
        apply_t0_trades(
            h,
            [
                {"side": "t0_sell", "shares": 400},
                {"side": "t0_buy", "shares": 400},
            ],
            as_of="2026-08-21",
            ts="2026-08-21T14:00:00",
        )
        self.assertEqual(h["shares"], 1000)
        self.assertEqual(sellable_shares(h, as_of="2026-08-21"), 600)
        self.assertEqual(locked_shares(h, as_of="2026-08-21"), 400)


class TestTplus1ManualSell(unittest.TestCase):
    def test_manual_sell_blocks_same_day_buy(self):
        from core.paper.exec import manual_sell

        paper = {
            "cash": 10000.0,
            "cost_model": "zero",
            "holdings": [],
            "trades": [],
            "operation_log": [],
        }
        h = {
            "stock_code": "600519",
            "stock_name": "茅台",
            "shares": 100,
            "cost": 10.0,
        }
        stamp_new_holding(h, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        paper["holdings"].append(h)

        def _quote(_code):
            return {"success": True, "stock_name": "茅台"}

        with patch("core.paper.tplus1.session_date", return_value="2026-08-21"):
            with patch("core.paper.exec._query_quote", side_effect=_quote):
                with patch("core.paper.exec._quote_price", return_value=12.0):
                    with patch(
                        "core.paper.rebalance._sell_match_block_reason",
                        return_value=None,
                    ):
                        with self.assertRaises(ValueError) as ctx:
                            manual_sell(paper, stock_code="600519", shares=100)
        self.assertIn("T+1", str(ctx.exception))
        self.assertEqual(paper["holdings"][0]["shares"], 100)

    def test_manual_sell_allows_next_session(self):
        from core.paper.exec import manual_sell

        paper = {
            "cash": 10000.0,
            "cost_model": "zero",
            "holdings": [],
            "trades": [],
            "operation_log": [],
        }
        h = {
            "stock_code": "600519",
            "stock_name": "茅台",
            "shares": 100,
            "cost": 10.0,
        }
        stamp_new_holding(h, ts="2026-08-21T10:00:00", as_of="2026-08-21")
        paper["holdings"].append(h)

        def _quote(_code):
            return {"success": True, "stock_name": "茅台"}

        with patch("core.paper.tplus1.session_date", return_value="2026-08-24"):
            with patch("core.paper.exec._query_quote", side_effect=_quote):
                with patch("core.paper.exec._quote_price", return_value=12.0):
                    with patch(
                        "core.paper.rebalance._sell_match_block_reason",
                        return_value=None,
                    ):
                        trades = manual_sell(paper, stock_code="600519", shares=100)
        self.assertEqual(len(trades), 1)
        self.assertEqual(paper["holdings"], [])

    def test_add_buy_does_not_unlock_new_shares(self):
        h = {"shares": 0, "lots": []}
        add_buy_lot(h, 100, ts="2026-08-20T10:00:00", as_of="2026-08-20")
        add_buy_lot(h, 100, ts="2026-08-21T11:00:00", as_of="2026-08-21")
        qty, meta = clip_sell_shares(h, 200, as_of="2026-08-21")
        self.assertEqual(qty, 100)
        self.assertTrue(meta["clipped"])
        self.assertEqual(meta["locked"], 100)

    def test_restore_and_consume_lots_for_void(self):
        from core.paper.tplus1 import consume_lots_bought_on, restore_sellable_lot

        h = {"shares": 0, "lots": []}
        # 当日买回 100（T+1 锁）
        add_buy_lot(h, 100, ts="2026-08-26T14:30:00", as_of="2026-08-26")
        self.assertEqual(sellable_shares(h, as_of="2026-08-26"), 0)
        # 冲正买：扣当日批次
        consumed = consume_lots_bought_on(h, 100, bought_date="2026-08-26")
        self.assertEqual(consumed, 100)
        self.assertEqual(float(h.get("shares") or 0), 0)
        # 冲正卖：加回可卖旧仓
        restore_sellable_lot(
            h, 100, bought_date="2026-08-25", ts="2026-08-26T09:35:00"
        )
        self.assertEqual(float(h.get("shares") or 0), 100)
        self.assertEqual(sellable_shares(h, as_of="2026-08-26"), 100)


if __name__ == "__main__":
    unittest.main()
