"""纸面 next_open：盘中现价成交；收盘后挂次日开盘单。"""

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
        from core.paper.open_fill import paper_fill_phase

        self.assertEqual(paper_fill_phase(_dt(9, 30)), "open")
        self.assertEqual(paper_fill_phase(_dt(10, 30)), "session")
        self.assertEqual(paper_fill_phase(_dt(15, 30)), "closed")
        self.assertEqual(paper_fill_phase(_dt(16, 40)), "closed")
        self.assertEqual(paper_fill_phase(_dt(8, 0)), "closed")

    def test_target_fill_date_preopen_is_today(self):
        """交易日盘前应挂今日开盘，不能 next_trading_day 跳到明天。"""
        from core.paper.open_fill import _target_fill_date, pending_from_trades

        # 2026-08-21 周五
        self.assertEqual(_target_fill_date(_dt(8, 0, "2026-08-21")), "2026-08-21")
        self.assertEqual(_target_fill_date(_dt(9, 30, "2026-08-21")), "2026-08-21")
        self.assertEqual(_target_fill_date(_dt(10, 30, "2026-08-21")), "2026-08-24")
        self.assertEqual(_target_fill_date(_dt(16, 40, "2026-08-21")), "2026-08-24")
        po = pending_from_trades(
            [{"side": "sell", "stock_code": "600519", "shares": 100, "price": 1.0}],
            [],
            now=_dt(8, 30, "2026-08-21"),
        )
        self.assertEqual(po["as_of"], "2026-08-21")
        self.assertEqual(po["target_fill_date"], "2026-08-21")

    def test_heal_preopen_wrong_target_in_open_window(self):
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
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
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-25",  # 盘前误标次日
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 1500.0,
                    }
                ],
            },
        )
        quotes = {"600519": {"success": True, "open_raw": 1410.0, "stock_name": "茅台"}}
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertTrue(out.get("filled"))
        self.assertFalse(paper.get("pending_orders"))


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
        from core.paper.open_fill import apply_next_open_commit

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
        from core.paper.open_fill import apply_next_open_commit

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

    def test_session_fills_immediately(self):
        from core.paper.open_fill import apply_next_open_commit

        original = self._paper()
        mutated = {**self._paper(), "cash": 1.0, "holdings": []}
        result = {"ok": True, "sell_trades": [], "buy_trades": []}
        paper, out = apply_next_open_commit(
            original, mutated, result, now=_dt(10, 30), dry_run=False
        )
        self.assertEqual(out["fill_action"], "immediate")
        self.assertEqual(paper.get("cash"), 1.0)
        self.assertEqual(paper.get("holdings"), [])

    def test_open_window_without_pending_uses_quote(self):
        from core.paper.open_fill import apply_next_open_commit

        original = self._paper()
        mutated = {**self._paper(), "cash": 1.0, "holdings": []}
        result = {"ok": True, "sell_trades": [], "buy_trades": []}
        paper, out = apply_next_open_commit(
            original, mutated, result, now=_dt(9, 30), dry_run=False
        )
        self.assertEqual(out["fill_action"], "immediate")
        self.assertEqual(paper.get("cash"), 1.0)
        self.assertEqual(paper.get("holdings"), [])

    def test_fill_pending_at_open(self):
        from core.paper.open_fill import fill_pending_at_open, pending_from_trades, stage_pending

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
        from core.paper.open_fill import require_open_fill

        msg = require_open_fill(self._paper(), now=_dt(16, 0), action="做 T")
        self.assertIsNotNone(msg)
        self.assertIn("next_open", str(msg))
        self.assertIn("收盘", str(msg))
        self.assertIsNone(require_open_fill(self._paper(), now=_dt(9, 30)))
        self.assertIsNone(require_open_fill(self._paper(), now=_dt(10, 30)))

    def test_manual_sell_stages_pending_after_close(self):
        """收盘后手动清仓 → 挂次日开盘单，不改持仓。"""
        import json
        import tempfile
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000.0,
                "initial_cash": 100000.0,
                "holdings": [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 200,
                        "cost": 100.0,
                        "lots": [{"shares": 200, "buy_date": "2026-08-20"}],
                    }
                ],
                "trades": [],
                "operation_log": [],
                "rules": {"execution_mode": "next_open"},
                "pending_orders": None,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            svc = PaperService(path)
            with patch(
                "core.paper.open_fill.paper_fill_phase", return_value="closed"
            ), patch(
                "core.paper.open_fill.is_next_open_mode", return_value=True
            ), patch(
                "core.paper.open_fill._target_fill_date", return_value="2026-08-24"
            ):
                out = svc.sell(stock_code="600519")

            self.assertTrue(out.get("staged"), out)
            self.assertEqual(out.get("fill_action"), "staged")
            po = out.get("pending_orders") or {}
            legs = po.get("legs") or []
            self.assertEqual(len(legs), 1, po)
            self.assertEqual(legs[0].get("side"), "sell")
            self.assertEqual(legs[0].get("stock_code"), "600519")
            self.assertEqual(float(legs[0].get("shares") or 0), 200.0)
            with open(path, encoding="utf-8") as f:
                reloaded = json.load(f)
            self.assertEqual(len(reloaded.get("holdings") or []), 1)
            self.assertEqual(
                float((reloaded["holdings"][0]).get("shares") or 0), 200.0
            )
            self.assertIn("挂", str(out.get("message") or ""))

    def test_manual_sell_stages_same_day_buy_for_next_open(self):
        """收盘后挂次日开盘卖：今日买入不受 T+1 拦挂（成交日已过 T+1）。"""
        import json
        import tempfile
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000.0,
                "initial_cash": 100000.0,
                "holdings": [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 200,
                        "cost": 100.0,
                        "lots": [
                            {
                                "shares": 200,
                                "bought_date": "2026-08-28",
                                "bought_at": "2026-08-28T09:40:00",
                            }
                        ],
                    }
                ],
                "trades": [],
                "operation_log": [],
                "rules": {"execution_mode": "next_open"},
                "pending_orders": None,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            svc = PaperService(path)
            with patch(
                "core.paper.open_fill.paper_fill_phase", return_value="closed"
            ), patch(
                "core.paper.open_fill.is_next_open_mode", return_value=True
            ), patch(
                "core.paper.open_fill._target_fill_date", return_value="2026-08-31"
            ):
                out = svc.sell(stock_code="600519")

            self.assertTrue(out.get("staged"), out)
            legs = (out.get("pending_orders") or {}).get("legs") or []
            self.assertEqual(len(legs), 1, out)
            self.assertEqual(float(legs[0].get("shares") or 0), 200.0)


class TestExecutionTiming(unittest.TestCase):
    def test_default_next_open(self):
        from core.execution import resolve_effective_execution

        bundle = resolve_effective_execution(strategy="short", channel="paper")
        timing = bundle.get("rebalance_timing") or {}
        self.assertEqual(timing.get("execution_mode"), "next_open")
        self.assertIn("次日开盘", bundle.get("summary") or "")
