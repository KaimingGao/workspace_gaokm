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

    def test_manual_gate_follows_fill_clock(self):
        from core.paper.rebalance.auto_worker import (
            CLOCK_BLOCK_ACTIONS,
            rebalance_window_gate,
        )

        with patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=True,
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_fill_clock",
            return_value="09:40",
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_window_label",
            return_value="09:40–10:00",
        ):
            wait = rebalance_window_gate(_dt(9, 39), dry_run=True)
            self.assertIsNotNone(wait)
            self.assertEqual(wait["fill_action"], "wait_clock")
            self.assertIn(wait["fill_action"], CLOCK_BLOCK_ACTIONS)
            self.assertFalse(wait["confirm_supported"])
            self.assertTrue(wait["dry_run"])
            self.assertIn("09:40", wait["note"])

            self.assertIsNone(rebalance_window_gate(_dt(9, 40)))
            self.assertIsNone(rebalance_window_gate(_dt(9, 59)))

            missed = rebalance_window_gate(_dt(10, 0), dry_run=False)
            self.assertIsNotNone(missed)
            self.assertEqual(missed["fill_action"], "miss_window")
            self.assertFalse(missed["confirm_supported"])
            self.assertIn("过点不补跑", missed["note"])

    def test_manual_gate_holiday(self):
        from core.paper.rebalance.auto_worker import rebalance_window_gate

        with patch(
            "core.paper.rebalance.auto_worker.is_trading_session",
            return_value=False,
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_fill_clock",
            return_value="09:30",
        ), patch(
            "core.paper.rebalance.auto_worker.rebalance_window_label",
            return_value="09:30–10:00",
        ):
            out = rebalance_window_gate(_dt(9, 45), dry_run=True)
            self.assertEqual(out["fill_action"], "holiday")
            self.assertEqual(out["empty_reason"], "holiday")
            self.assertFalse(out["confirm_supported"])

    def test_manual_rebalance_skips_simulate_when_gated(self):
        from services.paper_trades import PaperTradesMixin

        paper = {
            "strategy_id": "short_conservative",
            "cash": 1_000_000,
            "holdings": [],
            "trades": [],
            "snapshots": [],
            "operation_log": [],
            "updated_at": "2026-01-01T00:00:00",
        }
        blocked = {
            "success": True,
            "ok": True,
            "mode": "watching_matrix",
            "dry_run": True,
            "matrix_mode": True,
            "fill_action": "miss_window",
            "fill_clock": "09:30",
            "window_label": "09:30–10:00",
            "confirm_supported": False,
            "sell_trades": [],
            "buy_trades": [],
            "rebalance_report": [],
            "note": "已过调仓窗口 09:30–10:00，过点不补跑、不挂开盘单",
            "empty_reason": "miss_window",
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            import json

            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _T(PaperTradesMixin):
                pass

            svc = _T()
            svc.path = path
            with patch(
                "core.paper.rebalance.auto_worker.rebalance_window_gate",
                return_value=blocked,
            ), patch(
                "core.paper.rebalance.watching_matrix.simulate_watching_matrix_preview",
            ) as sim:
                out = svc.rebalance(dry_run=True)
            self.assertEqual(out.get("fill_action"), "miss_window")
            self.assertFalse(out.get("confirm_supported"))
            sim.assert_not_called()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

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
            self.assertFalse(in_auto_rebalance_window(_dt(9, 24)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 25)))
            self.assertTrue(in_auto_rebalance_window(_dt(9, 29)))
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

    def test_wait_clock_does_not_mark(self):
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
                    "fill_action": "wait_clock",
                    "buy_trades": [],
                    "sell_trades": [],
                    "note": "未到调仓时间 09:40，窗口 09:40–10:00；到点后再预演/落账",
                }
                w._tick()
            from core.paper.rebalance.auto_worker import last_run_session

            with patch(
                "core.paper.rebalance.auto_worker._worker_config_path",
                return_value=path,
            ):
                self.assertEqual(last_run_session(), "")
                self.assertIn("未到调仓时间", w.status()["last_tick_message"])

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

    def test_rows_keep_fill_and_y_heads(self):
        from core.paper.rebalance.desk import desk_rows_from_result

        rows = desk_rows_from_result(
            {
                "rebalance_report": [
                    {
                        "stock_code": "600664",
                        "stock_name": "哈药股份",
                        "action": "open",
                        "shares": 500,
                        "price": 8.25,
                        "day_open": 7.56,
                        "prev_close": 7.50,
                        "amount": 3750,
                        "ranking": 2.885,
                        "ranking_score": 0.02885,
                        "y_oo": 2.16,
                        "y_τc": 6.36,
                        "y_co": -0.54,
                        "reason": "买500股",
                        "old_shares": 0,
                        "new_shares": 500,
                    }
                ],
                "buy_trades": [
                    {
                        "stock_code": "600664",
                        "side": "buy",
                        "price": 7.5,
                        "amount": 3750.0,
                        "ts": "2026-09-21T09:47:44.468",
                        "y_oo": 2.160609,
                    }
                ],
            }
        )
        row = rows[0]
        self.assertEqual(row["action"], "open")
        self.assertAlmostEqual(float(row["price"]), 7.5)
        self.assertAlmostEqual(float(row["day_open"]), 7.56)
        self.assertAlmostEqual(float(row["prev_close"]), 7.5)
        self.assertAlmostEqual(float(row["amount"]), 3750)
        self.assertEqual(row["ts"][:16], "2026-09-21T09:47")
        self.assertAlmostEqual(float(row["y_oo"]), 2.16)
        self.assertAlmostEqual(float(row["y_τc"]), 6.36)

    def test_hydrate_from_today_trades(self):
        from core.paper.rebalance.desk import hydrate_desk_rows_from_paper

        rows = [
            {
                "stock_code": "600664",
                "action": "open",
                "shares": 500.0,
                "ranking_score": 0.02885,
                "reason": "买500股",
            }
        ]
        paper = {
            "trades": [
                {
                    "ts": "2026-09-21T09:47:44.468",
                    "side": "buy",
                    "stock_code": "600664",
                    "price": 7.5,
                    "amount": 3750.0,
                    "origin": "strategy",
                    "y_oo": 2.16,
                    "y_τc": 6.36,
                    "ranking": 2.885,
                }
            ]
        }
        hydrate_desk_rows_from_paper(rows, paper, "2026-09-21")
        self.assertAlmostEqual(float(rows[0]["price"]), 7.5)
        self.assertAlmostEqual(float(rows[0]["amount"]), 3750)
        self.assertAlmostEqual(float(rows[0]["y_oo"]), 2.16)
        self.assertEqual(rows[0]["ts"][:10], "2026-09-21")

    def test_hydrate_overwrites_ranking_from_trade(self):
        from core.paper.rebalance.desk import hydrate_desk_rows_from_paper

        rows = [
            {
                "stock_code": "600664",
                "action": "open",
                "ranking": 2.885,
                "ranking_score": 0.02885,
            }
        ]
        paper = {
            "trades": [
                {
                    "ts": "2026-09-21T09:47:44.468",
                    "side": "buy",
                    "stock_code": "600664",
                    "price": 8.25,
                    "day_open": 7.56,
                    "origin": "strategy",
                    "ranking": -6.2417,
                    "ranking_score": -0.062417,
                }
            ]
        }
        hydrate_desk_rows_from_paper(rows, paper, "2026-09-21")
        self.assertAlmostEqual(float(rows[0]["ranking"]), -6.2417)
        self.assertAlmostEqual(float(rows[0]["ranking_score"]), -0.062417)
        self.assertAlmostEqual(float(rows[0]["day_open"]), 7.56)

    def test_attach_day_open_from_quote(self):
        from core.paper.rebalance.desk import attach_day_open_to_desk_rows

        rows = [{"stock_code": "600664", "price": 8.25}]
        with patch(
            "core.paper.rebalance.match._batch_query_quotes",
            return_value={"600664": {"open_raw": 7.56, "open": 7.56}},
        ), patch("core.data.facade.get_bars", return_value={"bars": []}):
            attach_day_open_to_desk_rows(rows, session="2026-09-21")
        self.assertAlmostEqual(float(rows[0]["day_open"]), 7.56)
        self.assertAlmostEqual(float(rows[0]["price"]), 8.25)

    def test_attach_day_open_from_today_bar(self):
        from core.paper.rebalance.desk import attach_day_open_to_desk_rows

        rows = [{"stock_code": "600664", "price": 8.25}]
        with patch(
            "core.paper.rebalance.match._batch_query_quotes",
            return_value={},
        ), patch(
            "core.data.facade.get_bars",
            return_value={"bars": [{"date": "2026-09-21", "open": 7.56, "close": 8.25}]},
        ):
            attach_day_open_to_desk_rows(rows, session="2026-09-21")
        self.assertAlmostEqual(float(rows[0]["day_open"]), 7.56)
        self.assertAlmostEqual(float(rows[0]["price"]), 8.25)

    def test_attach_day_open_keeps_existing(self):
        from core.paper.rebalance.desk import attach_day_open_to_desk_rows

        rows = [{"stock_code": "600664", "day_open": 7.50, "price": 8.25}]
        with patch("core.paper.rebalance.match._batch_query_quotes") as bq:
            attach_day_open_to_desk_rows(rows, session="2026-09-21")
            bq.assert_not_called()
        self.assertAlmostEqual(float(rows[0]["day_open"]), 7.5)

    def test_attach_day_open_rejects_yesterday_bar(self):
        from core.paper.rebalance.desk import attach_day_open_to_desk_rows

        rows = [{"stock_code": "600664"}]
        with patch(
            "core.paper.rebalance.match._batch_query_quotes",
            return_value={},
        ), patch(
            "core.data.facade.get_bars",
            return_value={"bars": [{"date": "2026-09-18", "open": 7.40, "close": 7.50}]},
        ):
            attach_day_open_to_desk_rows(rows, session="2026-09-21")
        self.assertIsNone(rows[0].get("day_open"))

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
            ), patch(
                "core.paper.rebalance.match._batch_query_quotes",
                return_value={},
            ), patch(
                "core.data.facade.get_bars",
                return_value={"bars": []},
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
        self.assertIn("本页只在已保存调仓时间～10:00 预演/落账", text)
        self.assertIn("与自动调仓同一窗口", text)
        self.assertIn("过点不补跑、不挂开盘单", text)
        self.assertNotIn("收盘后挂次日开盘单", text)
        self.assertNotIn("09:30 口径", text)
        run_i = text.find('id="follow-fold-rebalance-run"')
        desk_i = text.find('id="paper-rebalance-worker-desk"')
        rules_i = text.find('id="follow-fold-rebalance">')
        self.assertLess(run_i, desk_i)
        self.assertLess(desk_i, rules_i)
        self.assertEqual(text.count('id="paper-rebalance-worker-bar"'), 1)


if __name__ == "__main__":
    unittest.main()
