"""纸面 next_open：收盘挂单、开盘成交。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _dt(h: int, m: int, day: str = "2026-08-21") -> datetime:
    y, mo, d = [int(x) for x in day.split("-")]
    return datetime(y, mo, d, h, m)


class TestPaperFillPhase(unittest.TestCase):
    def test_open_session_closed(self):
        from core.paper_open_fill import paper_fill_phase

        self.assertEqual(paper_fill_phase(_dt(9, 30)), "open")
        self.assertEqual(paper_fill_phase(_dt(10, 30)), "session")
        self.assertEqual(paper_fill_phase(_dt(15, 30)), "closed")
        self.assertEqual(paper_fill_phase(_dt(16, 40)), "closed")
        self.assertEqual(paper_fill_phase(_dt(8, 0)), "closed")


class TestNextOpenCommit(unittest.TestCase):
    def _paper(self):
        return {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 1400.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
            "strategy_id": "short",
        }

    def test_stage_after_close(self):
        from core.paper_open_fill import apply_next_open_commit

        original = self._paper()
        mutated = {
            **self._paper(),
            "cash": 240000.0,
            "holdings": [],
            "last_optimize": {"ok": True},
        }
        result = {
            "ok": True,
            "sell_trades": [
                {
                    "side": "sell",
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "price": 1500.0,
                }
            ],
            "buy_trades": [],
        }
        paper, out = apply_next_open_commit(
            original, mutated, result, now=_dt(16, 40), dry_run=False
        )
        self.assertEqual(out["fill_action"], "staged")
        self.assertTrue(out.get("staged"))
        self.assertEqual(len(paper.get("holdings") or []), 1)
        self.assertAlmostEqual(float(paper.get("cash") or 0), 100000.0)
        legs = (paper.get("pending_orders") or {}).get("legs") or []
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["side"], "sell")
        self.assertEqual(paper.get("last_optimize"), {"ok": True})

    def test_close_mode_keeps_mutated(self):
        from core.paper_open_fill import apply_next_open_commit

        original = self._paper()
        original["rules"]["execution_mode"] = "close"
        mutated = {**self._paper(), "cash": 1.0, "holdings": []}
        mutated["rules"]["execution_mode"] = "close"
        result = {"ok": True, "sell_trades": [], "buy_trades": []}
        paper, out = apply_next_open_commit(
            original, mutated, result, now=_dt(16, 40), dry_run=False
        )
        self.assertEqual(out["fill_action"], "immediate")
        self.assertEqual(paper.get("cash"), 1.0)
        self.assertEqual(paper.get("holdings"), [])

    def test_fill_pending_at_open(self):
        from core.paper_open_fill import fill_pending_at_open, pending_from_trades, stage_pending

        paper = self._paper()
        pending = pending_from_trades(
            [
                {
                    "side": "sell",
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "price": 1500.0,
                }
            ],
            [],
            now=_dt(16, 40),
        )
        stage_pending(paper, pending)
        quotes = {
            "600519": {"success": True, "open_raw": 1410.0, "stock_name": "茅台"}
        }
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertTrue(out.get("filled"))
        self.assertFalse(paper.get("pending_orders"))
        self.assertEqual(paper.get("holdings") or [], [])
        self.assertGreater(float(paper.get("cash") or 0), 100000.0)

    def test_require_open_fill_blocks_after_close(self):
        from core.paper_open_fill import require_open_fill

        msg = require_open_fill(self._paper(), now=_dt(16, 0), action="做 T")
        self.assertIsNotNone(msg)
        self.assertIn("next_open", str(msg))
        self.assertIsNone(require_open_fill(self._paper(), now=_dt(9, 30)))


class TestExecutionTiming(unittest.TestCase):
    def test_default_next_open(self):
        from core.execution import resolve_effective_execution

        bundle = resolve_effective_execution(strategy="short", channel="paper")
        timing = bundle.get("rebalance_timing") or {}
        self.assertEqual(timing.get("execution_mode"), "next_open")
        self.assertIn("次日开盘", bundle.get("summary") or "")
