"""自动调仓后台 worker：开盘窗、幂等、过点不写账、做 T 等待。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CN = timezone(timedelta(hours=8))


def _dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=CN)


class TestAutoRebalanceWindow(unittest.TestCase):
    def test_window_follows_fill_clock(self):
        from core.paper.rebalance.auto_worker import (
            after_auto_rebalance_window,
            in_auto_rebalance_window,
        )

        with patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_fill_clock",
            return_value="09:40",
        ):
            self.assertFalse(in_auto_rebalance_window(_dt(9, 39)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 40)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 59)))
            self.assertFalse(in_auto_rebalance_window(_dt(10, 0)))
            self.assertFalse(after_auto_rebalance_window(_dt(9, 59)))
            self.assertTrue(after_auto_rebalance_window(_dt(10, 0)))

    def test_before_window(self):
        from core.paper.rebalance.auto_worker import before_auto_rebalance_window

        with patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_fill_clock",
            return_value="09:40",
        ):
            self.assertTrue(before_auto_rebalance_window(_dt(9, 30)))
            self.assertTrue(before_auto_rebalance_window(_dt(9, 39)))
            self.assertFalse(before_auto_rebalance_window(_dt(9, 40)))
            self.assertFalse(before_auto_rebalance_window(_dt(10, 1)))

    def test_window_0930_to_1000(self):
        from core.paper.rebalance.auto_worker import (
            after_auto_rebalance_window,
            in_auto_rebalance_window,
        )

        with patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_fill_clock",
            return_value="09:30",
        ):
            self.assertFalse(in_auto_rebalance_window(_dt(9, 29)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 30)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 59)))
            self.assertFalse(in_auto_rebalance_window(_dt(10, 0)))
            self.assertFalse(in_auto_rebalance_window(_dt(10, 30)))
            self.assertFalse(after_auto_rebalance_window(_dt(9, 59)))
            self.assertTrue(after_auto_rebalance_window(_dt(10, 0)))
            self.assertTrue(after_auto_rebalance_window(_dt(10, 30)))

    def test_t0_waits_until_rebalance_or_window_end(self):
        from core.paper.rebalance.auto_worker import t0_wait_for_rebalance

        with patch(
            "core.paper.rebalance.auto_worker.load_enabled_flag",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=False,
        ):
            wait, reason = t0_wait_for_rebalance()
            self.assertTrue(wait)
            self.assertEqual(reason, "等待调仓")

        with patch(
            "core.paper.rebalance.auto_worker.load_enabled_flag",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=False,
        ):
            wait, reason = t0_wait_for_rebalance()
            self.assertFalse(wait)

        with patch(
            "core.paper.rebalance.auto_worker.load_enabled_flag",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=True,
        ):
            wait, reason = t0_wait_for_rebalance()
            self.assertFalse(wait)

        with patch(
            "core.paper.rebalance.auto_worker.load_enabled_flag",
            return_value=False,
        ):
            wait, reason = t0_wait_for_rebalance()
            self.assertFalse(wait)


class TestRebalanceAutoWorkerTick(unittest.TestCase):
    def test_after_window_does_not_commit(self):
        from core.paper.rebalance.auto_worker import RebalanceAutoWorker

        w = RebalanceAutoWorker()
        w._enabled = True
        with patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.in_auto_rebalance_window",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.resolve_session",
            return_value="2026-09-10",
        ), patch("services.paper_service.PaperService") as svc_cls:
            w._tick()
            svc_cls.assert_not_called()
        self.assertIn("错过开盘窗", w.status()["last_tick_message"])

    def test_already_ran_is_idempotent(self):
        from core.paper.rebalance.auto_worker import RebalanceAutoWorker

        w = RebalanceAutoWorker()
        w._enabled = True
        with patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.resolve_session",
            return_value="2026-09-10",
        ), patch("services.paper_service.PaperService") as svc_cls:
            w._tick()
            svc_cls.assert_not_called()
        self.assertIn("今日已调仓", w.status()["last_tick_message"])

    def test_window_tick_commits_immediate_and_marks(self):
        from core.paper.rebalance.auto_worker import RebalanceAutoWorker

        w = RebalanceAutoWorker()
        w._enabled = True
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rebalance_auto_worker.json")
            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ), patch(
                "core.paper.rebalance.auto_worker.already_ran_today",
                return_value=False,
            ), patch(
                "core.paper.rebalance.auto_worker.in_auto_rebalance_window",
                return_value=True,
            ), patch(
                "core.paper.rebalance.auto_worker.resolve_session",
                return_value="2026-09-10",
            ), patch("services.paper_service.PaperService") as svc_cls, patch(
                "core.paper.load_paper",
                return_value={"pending_orders": {}},
            ):
                inst = svc_cls.return_value
                inst.t0_auto_status.return_value = {}
                inst.fill_pending.return_value = {}
                inst.rebalance.return_value = {
                    "success": True,
                    "ok": True,
                    "fill_action": "immediate",
                    "buy_trades": [{"stock_code": "600000"}],
                    "sell_trades": [],
                    "note": "落账 · 卖 0 · 买 1",
                }
                w._tick()
                inst.rebalance.assert_called_once_with(
                    dry_run=False, offline_only=False
                )
            from core.paper.rebalance.auto_worker import last_run_session

            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ):
                self.assertEqual(last_run_session(), "2026-09-10")
                st = w.status()
                self.assertIsNotNone(st.get("last_run_ts"))
                self.assertEqual(st.get("last_buy_count"), 1)
                self.assertEqual(st.get("last_sell_count"), 0)
                self.assertEqual(st.get("last_source"), "auto")

    def test_leftover_pending_does_not_mark(self):
        from core.paper.rebalance.auto_worker import RebalanceAutoWorker

        w = RebalanceAutoWorker()
        w._enabled = True
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rebalance_auto_worker.json")
            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ), patch(
                "core.paper.rebalance.auto_worker.already_ran_today",
                return_value=False,
            ), patch(
                "core.paper.rebalance.auto_worker.in_auto_rebalance_window",
                return_value=True,
            ), patch(
                "core.paper.rebalance.auto_worker.resolve_session",
                return_value="2026-09-10",
            ), patch("services.paper_service.PaperService") as svc_cls, patch(
                "core.paper.load_paper",
                return_value={"pending_orders": {"legs": [{"stock_code": "600000"}]}},
            ):
                inst = svc_cls.return_value
                inst.t0_auto_status.return_value = {}
                inst.fill_pending.return_value = {}
                w._tick()
                inst.rebalance.assert_not_called()
            from core.paper.rebalance.auto_worker import last_run_session

            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ):
                self.assertEqual(last_run_session(), "")


class TestT0WorkerWaitsForRebalance(unittest.TestCase):
    def test_skips_new_legs_but_fills_pending(self):
        from core.t0.auto_worker import T0AutoWorker

        w = T0AutoWorker()
        w._enabled = True
        with patch(
            "core.t0.intraday.session_in_market",
            return_value=(True, "2026-09-10"),
        ), patch(
            "core.t0.auto_worker._session_closed",
            return_value=(False, "2026-09-10"),
        ), patch(
            "core.paper.rebalance.auto_worker.t0_wait_for_rebalance",
            return_value=(True, "等待调仓"),
        ), patch("services.paper_service.PaperService") as svc_cls:
            inst = svc_cls.return_value
            inst.t0_auto_status.return_value = {}
            inst.fill_pending.return_value = {}
            inst.run_t0_intraday_tick.return_value = {"ok": True}
            w._tick()
            inst.fill_pending.assert_called()
            inst.run_t0_intraday_tick.assert_not_called()
        self.assertIn("等待调仓", w.status()["last_tick_message"])


class TestRebalanceDesk(unittest.TestCase):
    def test_rows_from_result_prefers_report(self):
        from core.paper.rebalance.desk import desk_rows_from_result

        rows = desk_rows_from_result(
            {
                "rebalance_report": [
                    {
                        "stock_code": "600000",
                        "stock_name": "浦发",
                        "action": "exit",
                        "shares_change": -200,
                        "ranking_score": -0.01,
                        "reason": "ranking<0 清仓",
                        "old_shares": 200,
                    },
                    {
                        "stock_code": "000001",
                        "stock_name": "平安",
                        "action": "open",
                        "shares": 500,
                        "ranking_score": 0.02,
                        "reason": "过强",
                        "old_shares": 0,
                    },
                    {
                        "stock_code": "600519",
                        "action": "hold",
                        "ranking_score": 0.004,
                        "reason": "ranking≥0% 持有",
                        "old_shares": 100,
                    },
                ]
            }
        )
        by = {r["stock_code"]: r for r in rows}
        self.assertEqual(by["600000"]["action"], "exit")
        self.assertEqual(by["600000"]["phase"], "done")
        self.assertEqual(by["000001"]["action"], "open")
        self.assertEqual(by["600519"]["action"], "hold")
        self.assertEqual(by["600519"]["phase"], "hold")

    def test_holdings_placeholder_watch(self):
        from core.paper.rebalance.desk import desk_rows_from_holdings

        rows = desk_rows_from_holdings(
            {
                "holdings": [
                    {"stock_code": "600519", "stock_name": "茅台", "shares": 100},
                    {"stock_code": "000001", "shares": 0},
                ]
            },
            phase="watch",
            reason="开盘窗内监视",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "600519")
        self.assertEqual(rows[0]["action"], "watch")
        self.assertEqual(rows[0]["phase"], "watch")

    def test_phase_window(self):
        from core.paper.rebalance.desk import (
            PHASE_DONE,
            PHASE_MISSED,
            PHASE_WATCH,
            resolve_desk_phase,
        )

        with patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.in_auto_rebalance_window",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=False,
        ):
            self.assertEqual(resolve_desk_phase(enabled=True), PHASE_WATCH)
        with patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ):
            self.assertEqual(resolve_desk_phase(enabled=True), PHASE_DONE)
        with patch(
            "core.paper.rebalance.auto_worker.already_ran_today",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.in_auto_rebalance_window",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.after_auto_rebalance_window",
            return_value=True,
        ):
            self.assertEqual(resolve_desk_phase(enabled=True), PHASE_MISSED)

    def test_persist_and_build_aligned(self):
        from core.paper.rebalance.desk import (
            build_rebalance_desk_status,
            persist_desk_from_result,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rebalance_auto_worker.json")
            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ), patch(
                "core.paper.rebalance.auto_worker.resolve_session",
                return_value="2026-09-10",
            ), patch(
                "core.paper.rebalance.auto_worker.already_ran_today",
                return_value=True,
            ), patch(
                "core.paper.rebalance.desk.resolve_desk_phase",
                return_value="done",
            ), patch(
                "core.paper.rebalance.desk._load_paper_safe",
                return_value={"holdings": []},
            ):
                persist_desk_from_result(
                    {
                        "note": "落账 · 卖 1 · 买 0",
                        "rebalance_report": [
                            {
                                "stock_code": "600000",
                                "stock_name": "浦发",
                                "action": "exit",
                                "shares": 200,
                                "ranking_score": -0.02,
                                "reason": "ranking<0",
                                "old_shares": 200,
                            }
                        ],
                    },
                    "2026-09-10",
                    source="auto",
                )
                desk = build_rebalance_desk_status(
                    worker={"enabled": True, "inflight": False}
                )
        self.assertTrue(desk["state_aligned"])
        self.assertEqual(desk["phase"], "done")
        self.assertEqual(desk["rows"][0]["stock_code"], "600000")
        self.assertEqual(desk["rows"][0]["action"], "exit")
        self.assertEqual(desk["exit_count"], 1)


class TestFollowPanelWorkerMarkup(unittest.TestCase):
    def test_html_ids(self):
        path = os.path.join(ROOT, "web", "static", "partials", "follow_panel.html")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('id="paper-rebalance-worker-enabled"', text)
        self.assertIn('id="paper-rebalance-worker-panel"', text)
        self.assertIn('id="paper-rebalance-worker-m-lastrun"', text)
        self.assertIn('id="paper-rebalance-worker-ledger"', text)
        self.assertIn('id="paper-rebalance-worker-desk"', text)
        self.assertIn('id="follow-fold-rebalance-run"', text)
        self.assertIn("今日调仓盯盘状态", text)
        self.assertIn("自动调仓后台进程", text)
        run_i = text.find('id="follow-fold-rebalance-run"')
        desk_i = text.find('id="paper-rebalance-worker-desk"')
        rules_i = text.find('id="follow-fold-rebalance">')
        self.assertLess(run_i, desk_i)
        self.assertLess(desk_i, rules_i)
        self.assertEqual(text.count('id="paper-rebalance-worker-bar"'), 1)


if __name__ == "__main__":
    unittest.main()
