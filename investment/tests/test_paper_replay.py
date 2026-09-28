"""纸面回放引擎：T+1 / 续持 / 现金约束 / 引擎元数据。"""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.paper_replay import (
    REPLAY_AMOUNT_BASE,
    REPLAY_AMOUNT_STRONG,
    REPLAY_CASH_FLOOR,
    REPLAY_INITIAL_CASH,
    REPLAY_RANK_ENTER,
    REPLAY_RANK_STRONG,
    backtest_paper_replay,
    fuse_hit_metrics,
    mock_quote_from_bar,
    realized_path_label,
    realized_yhat_windows,
    rescore_replay_y_oc_at_clock,
)
from core.paper.replay_ctx import paper_replay_context
from core.paper.tplus1 import add_buy_lot, clip_sell_shares


def _bars(n: int = 25, *, step: float = 0.4, start: float = 100.0):
    out = []
    for i in range(n):
        close = start + i * step
        out.append(
            {
                "date": f"2026-03-{i + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 100000 + i * 1000,
            }
        )
    return out


def _minutes_aligned(stock_bars, *, close_delta=0.4, open_scale=1.0, prev_close=None):
    """按日线开对齐的 09:35 分钟根；open_scale 可造开盘错位。"""
    out = {}
    for code, bars in (stock_bars or {}).items():
        by_d = {}
        for b in bars or []:
            d = str((b or {}).get("date") or "")
            if not d:
                continue
            o = float((b or {}).get("open") or 0)
            mo = o * float(open_scale)
            row = {
                "date": d,
                "datetime": f"{d} 09:35:00",
                "open": mo,
                "high": mo + abs(float(close_delta)) + 0.1,
                "low": mo - 0.1,
                "close": mo + float(close_delta),
            }
            if prev_close is not None:
                row["prev_close"] = float(prev_close)
            by_d[d] = [row]
        out[str(code)] = by_d
    return out


def _rank_row(code: str, score: float = 1.5) -> dict:
    return {
        "stock_code": code,
        "stock_name": code,
        "score": score,
        "predicted_score": score,
        "predicted_score_eod": score,
        "predicted_score_tau": score,
        "score_scale": "predicted_yhat",
        "dual_score_window": "eod_next",
    }


def _risk_ok():
    return {
        "ok": True,
        "blocks": [],
        "warnings": [],
        "limits": {"max_position_pct": 40.0, "max_sector_pct": 60.0},
    }


@contextmanager
def _offline_rebalance_patches():
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "core.event_prior.get_event_prior_cfg",
                return_value={"mode": "off"},
            )
        )
        stack.enter_context(
            patch(
                "core.sentiment_prior.get_sentiment_prior_cfg",
                return_value={"mode": "off"},
            )
        )
        stack.enter_context(
            patch("core.risk.check_account_risk", side_effect=lambda *a, **k: _risk_ok())
        )
        stack.enter_context(
            patch(
                "adapters.market.history.fetch_a_daily_bars",
                side_effect=RuntimeError("paper_replay test: no network"),
            )
        )
        stack.enter_context(
            patch(
                "core.data.facade.bars_and_source",
                side_effect=RuntimeError("paper_replay test: no network"),
            )
        )
        yield


class TestMockQuote(unittest.TestCase):
    def test_open_and_prev_close(self):
        prev = {"close": 100.0}
        bar = {"open": 102.0, "close": 103.0}
        q = mock_quote_from_bar(bar, prev_bar=prev, px_field="open")
        self.assertTrue(q["success"])
        self.assertEqual(q["price_raw"], 102.0)
        self.assertEqual(q["prev_close"], 100.0)
        self.assertAlmostEqual(q["change_raw"], 2.0, places=2)


class TestReplayTplus1(unittest.TestCase):
    def test_same_day_buy_not_sellable(self):
        holding = {"stock_code": "600519", "shares": 0, "cost": 100.0, "lots": []}
        with paper_replay_context(
            as_of="2026-03-10",
            batch_query=lambda codes: {
                c: {"success": True, "price_raw": 100.0} for c in codes
            },
        ):
            add_buy_lot(holding, 100, ts="2026-03-10T09:30:00.000")
            qty, meta = clip_sell_shares(holding, 100)
        self.assertEqual(float(holding.get("shares") or 0), 100.0)
        self.assertLessEqual(qty, 1e-9)
        self.assertTrue(meta.get("clipped") or meta.get("reason"))

    def test_next_day_sellable(self):
        holding = {"stock_code": "600519", "shares": 0, "cost": 100.0, "lots": []}
        with paper_replay_context(
            as_of="2026-03-10",
            batch_query=lambda codes: {
                c: {"success": True, "price_raw": 100.0} for c in codes
            },
        ):
            add_buy_lot(holding, 100, ts="2026-03-10T09:30:00.000")
        with paper_replay_context(
            as_of="2026-03-11",
            batch_query=lambda codes: {
                c: {"success": True, "price_raw": 100.0} for c in codes
            },
        ):
            qty, _meta = clip_sell_shares(holding, 100)
        self.assertGreaterEqual(qty, 100.0 - 1e-6)


class TestPaperReplayEngine(unittest.TestCase):
    def test_hold_continuity_no_churn_sell(self):
        """同票连续入选且 ranking≥0：不开卖腿（可每日加 100/200 直到现金不够）。"""
        stock_bars = {
            "600519": _bars(22, step=0.5),
            "600036": _bars(22, step=0.3),
            "300750": _bars(22, step=0.4),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"]["engine"], "paper_replay")
        self.assertEqual(float(out["params"]["initial_cash"]), 1_000_000.0)
        self.assertEqual(float(out["params"]["cash_floor"]), REPLAY_CASH_FLOOR)
        sells = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "sell" and t.get("stock_code") == "600519"
        ]
        self.assertEqual(sells, [], "续持不应卖出同票")
        held = {h.get("stock_code") for h in (out.get("holdings_end") or [])}
        self.assertIn("600519", held)
        self.assertGreaterEqual(len(out.get("equity_curve") or []), 3)
        with_legs = [
            p
            for p in (out.get("equity_curve") or [])
            if isinstance(p, dict) and p.get("legs")
        ]
        self.assertTrue(with_legs, "日收益曲线应带个股明细 legs")
        sample = with_legs[-1]
        self.assertIn("contrib_pct", sample["legs"][0])
        self.assertIn("ret_pct", sample["legs"][0])
        self.assertIn("stock_code", sample["legs"][0])
        contrib = out.get("stock_contrib") or []
        self.assertTrue(contrib, "调仓回测应带分票贡献")
        mt = next((r for r in contrib if r.get("stock_code") == "600519"), None)
        self.assertIsNotNone(mt)
        self.assertGreater(int(mt.get("hold_days") or 0), 0)
        self.assertGreater(int(mt.get("buy_count") or 0), 0)
        self.assertEqual(int(mt.get("sell_count") or 0), 0)
        self.assertIn("pnl", mt)
        self.assertIn("contrib_pct", mt)
        last = dates[-1]
        fills = [
            t
            for t in (out.get("sim_trades") or [])
            if t.get("stock_code") == "600519" and t.get("status") != "skipped"
        ]
        self.assertTrue(fills)
        mid = next(t for t in fills if str(t.get("as_of") or "")[:10] != last)
        i = dates.index(str(mid.get("as_of") or "")[:10])
        bars = stock_bars["600519"]
        prev_c = float(bars[i - 1]["close"])
        close_t = float(bars[i]["close"])
        open_t = float(bars[i]["open"])
        nxt_o = float(bars[i + 1]["open"])
        self.assertAlmostEqual(mid.get("realized_cc"), (close_t / prev_c - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(mid.get("realized_tau"), (close_t / open_t - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(mid.get("realized_on"), (nxt_o / close_t - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(mid.get("realized_oo"), (nxt_o / open_t - 1.0) * 100.0, places=3)
        px_tau = float(mid.get("rebalance_px") or mid.get("price") or open_t)
        self.assertAlmostEqual(
            mid.get("realized_ranking"),
            (nxt_o - px_tau) / open_t * 100.0,
            places=3,
        )
        last_rows = [
            t
            for t in (out.get("sim_trades") or [])
            if str(t.get("as_of") or "")[:10] == last
        ]
        if last_rows:
            self.assertIsNone(last_rows[0].get("realized_on"))
            self.assertIsNone(last_rows[0].get("realized_oo"))
            self.assertIsNone(last_rows[0].get("realized_ranking"))
        m = out.get("metrics") or {}
        self.assertEqual(out.get("params", {}).get("end_date"), last)
        self.assertGreaterEqual(int(m.get("hit_n") or 0), 1)
        last_fills = [
            t
            for t in (out.get("sim_trades") or [])
            if str(t.get("as_of") or "")[:10] == last
            and str(t.get("status") or "") not in ("skipped", "held")
        ]
        self.assertTrue(last_fills, "末日仍有成交腿，但不进命中分母")
        self.assertTrue(all(t.get("realized_ranking") is None for t in last_fills))
        day = str(mid.get("as_of") or "")[:10]
        curve = {
            str(p.get("date") or "")[:10]: p
            for p in (out.get("equity_curve") or [])
            if isinstance(p, dict)
        }
        self.assertIn(day, curve)
        self.assertAlmostEqual(
            float(mid.get("equity_after")),
            float(curve[day].get("equity")),
            places=2,
        )

    def test_default_cash_is_20w_no_floor(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(float(out["params"]["initial_cash"]), REPLAY_INITIAL_CASH)
        self.assertEqual(float(out["params"]["cash_floor"]), REPLAY_CASH_FLOOR)
        self.assertEqual(REPLAY_INITIAL_CASH, 200_000.0)
        self.assertEqual(REPLAY_CASH_FLOOR, 0.0)
        fills = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "buy"
        ]
        self.assertTrue(fills, "20 万本金、不留地板应能买入手数")
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        paper = out.get("paper") or {}
        self.assertAlmostEqual(
            float(get_rank_lot_cfg(paper).get("cash_floor") or 0),
            REPLAY_CASH_FLOOR,
        )
        rt = ((paper.get("rules") or {}).get("execution") or {}).get("rebalance_timing") or {}
        self.assertIn("rank_lots", rt)
        self.assertAlmostEqual(float((rt.get("rank_lots") or {}).get("cash_floor") or 0), REPLAY_CASH_FLOOR)
        m = out.get("metrics") or {}
        self.assertIn("hit_rate_pct", m)
        self.assertIn("hit_n", m)
        self.assertIn("hit_hits", m)
        self.assertIn("day_count", m)
        self.assertGreaterEqual(int(m.get("hit_n") or 0), 1)
        self.assertIsNotNone(m.get("hit_rate_pct"))

    def test_initial_cash_from_arg(self):
        from core.backtest.paper_replay import clamp_replay_initial_cash

        self.assertEqual(clamp_replay_initial_cash(150000), 150_000.0)
        self.assertEqual(clamp_replay_initial_cash(None), 200_000.0)
        self.assertEqual(clamp_replay_initial_cash(1000), 10_000.0)
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=150_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(float(out["params"]["initial_cash"]), 150_000.0)

    def test_custom_lots(self):
        from core.backtest.paper_replay import clamp_replay_lot, clamp_replay_lot_pair

        self.assertEqual(clamp_replay_lot(150), 1000)
        self.assertEqual(clamp_replay_lot(250), 1000)
        self.assertEqual(clamp_replay_lot_pair(300, 200), (1000, 1000))
        self.assertEqual(clamp_replay_lot_pair(30000, 20000), (30000, 30000))
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
                lot_base_amount=30000,
                lot_strong_amount=50000,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(int(out["params"]["lot_base_amount"]), 30000)
        self.assertEqual(int(out["params"]["lot_strong_amount"]), 50000)
        buys = [t for t in (out.get("trades") or []) if t.get("side") == "buy"]
        self.assertTrue(buys)
        for t in buys:
            sh = float(t.get("shares") or 0)
            px = float(t.get("price") or 0)
            from core.paper.sizing import shares_from_amount
            want_b = shares_from_amount(30000, px)
            want_s = shares_from_amount(50000, px)
            self.assertIn(sh, (float(want_b), float(want_s)), t)

    def test_cash_floor_respected(self):
        stock_bars = {
            "600519": _bars(20, step=0.5),
            "600036": _bars(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {
            d: [_rank_row("600519", 2.0), _rank_row("600036", 1.5)] for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=2,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
                cash_floor=500_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        paper = out.get("paper") or {}
        last = (out.get("equity_curve") or [])[-1]
        equity = float(last.get("equity") or 0)
        cash = float(out.get("cash_end") or paper.get("cash") or 0)
        self.assertGreater(equity, 0)
        self.assertGreaterEqual(cash, 500_000.0 - 1e-6)
        skips = [s for s in (out.get("sim_trades") or []) if s.get("status") == "skipped"]
        if int((out.get("constraints_hit") or {}).get("cash_floor_skips") or 0) > 0:
            self.assertTrue(
                any("地板" in str(s.get("reason") or "") for s in skips),
                "cash floor skips should appear in sim_trades",
            )

    def test_fusion_negative_exits_after_t1(self):
        """ranking<0 才清仓；买入当日 T+1 不可卖，次日可卖。"""
        stock_bars = {
            "600519": _bars(20, step=0.5),
            "600036": _bars(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            if i <= 8:
                rankings[d] = [_rank_row("600519", 2.0)]
            else:
                rankings[d] = [_rank_row("600519", -1.0)]

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertGreaterEqual(len(sells), 1, "ranking<0 应卖出")
        hits = out.get("constraints_hit") or {}
        self.assertTrue(
            int(hits.get("t1_blocks") or 0) > 0 or len(sells) > 0,
            f"expected t1 or sells, got hits={hits} sells={len(sells)}",
        )
        skips = [s for s in (out.get("sim_trades") or []) if s.get("status") == "skipped"]
        t1_blocks = int(hits.get("t1_blocks") or 0)
        if t1_blocks > 0:
            self.assertTrue(
                any(
                    "T+1" in str(s.get("reason") or "") or "不可卖" in str(s.get("reason") or "")
                    for s in skips
                ),
                f"t1_blocks={t1_blocks} but sim_trades has no T+1 skip: {skips[:3]!r}",
            )

    def test_leave_topk_does_not_sell(self):
        """掉出 Top-K 但 ranking>入场：不卖。"""
        stock_bars = {
            "600519": _bars(20, step=0.5),
            "600036": _bars(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            if i % 2 == 0:
                rankings[d] = [
                    _rank_row("600519", 2.0),
                    _rank_row("600036", 1.5),
                ]
            else:
                rankings[d] = [
                    _rank_row("600036", 2.0),
                    _rank_row("600519", 1.5),
                ]

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertEqual(sells, [], "未进 Top-K 且 ranking>入场不应卖出")

    def test_fail_enter_exits_held(self):
        """开仓后 ranking 未过入场：整笔清仓。"""
        stock_bars = {
            "600519": _bars(18, step=0.5),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else 0.02
            rankings[d] = [_rank_row("600519", y)]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=200_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "sell" and t.get("stock_code") == "600519"
        ]
        self.assertTrue(sells, "未过入场应清仓")
        self.assertTrue(
            any(
                (t.get("action") or t.get("matrix_action")) == "exit"
                and float(t.get("shares") or 0) >= 100
                for t in sells
            ),
            sells[:3],
        )

    def test_missing_ranking_exits_held(self):
        """当日打不上分：清仓，不假装 ranking≥0 续持。"""
        stock_bars = {
            "600519": _bars(20, step=0.5),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            if i <= 10:
                rankings[d] = [_rank_row("600519", 2.0)]
            else:
                rankings[d] = []

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertGreaterEqual(len(sells), 1, "缺 ranking 应卖出")
        sim = [t for t in (out.get("sim_trades") or []) if t.get("side") == "sell"]
        self.assertTrue(
            any("缺失" in str(t.get("reason") or "") for t in sim + sells),
            f"expected ranking-missing exit, sells={sells[:2]!r} sim={sim[:2]!r}",
        )

    def test_stamp_stock_names_prefers_watching_map(self):
        from core.backtest.paper_replay import _stamp_stock_names

        rows = [{"stock_code": "600519", "stock_name": "600519"}]
        _stamp_stock_names(rows, {"600519": "贵州茅台"})
        self.assertEqual(rows[0]["stock_name"], "贵州茅台")
        keep = [{"stock_code": "600519", "stock_name": "茅台"}]
        _stamp_stock_names(keep, {})
        self.assertEqual(keep[0]["stock_name"], "茅台")

    def test_sim_trades_use_watching_names(self):
        stock_bars = {
            "600519": _bars(22, step=0.5),
            "600036": _bars(22, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {
            d: [_rank_row("600519", 2.0), _rank_row("600036", 1.5)] for d in dates
        }
        with _offline_rebalance_patches(), patch(
            "core.backtest.paper_replay._watching_name_map",
            return_value={"600519": "贵州茅台", "600036": "招商银行"},
        ):
            out = backtest_paper_replay(
                stock_bars,
                top_k=2,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        names = {
            str(t.get("stock_code")): t.get("stock_name")
            for t in (out.get("sim_trades") or [])
        }
        self.assertEqual(names.get("600519"), "贵州茅台")
        self.assertEqual(names.get("600036"), "招商银行")

    def test_full_ledger_not_truncated(self):
        """完整账本：trades / sim_trades / rebalance_logs 等长且带 as_of。"""
        stock_bars = {
            "600519": _bars(22, step=0.5),
            "600036": _bars(22, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {
            d: [_rank_row("600519", 2.0), _rank_row("600036", 1.5)] for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=2,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        trades = out.get("trades") or []
        sim = out.get("sim_trades") or []
        logs = out.get("rebalance_logs") or []
        curve = out.get("equity_curve") or []
        self.assertGreaterEqual(len(trades), 1)
        fills = [s for s in sim if s.get("status") == "filled"]
        self.assertEqual(len(fills), len(trades))
        self.assertEqual(out.get("sim_trade_count"), len(trades))
        self.assertGreaterEqual(len(logs), 3)
        self.assertGreaterEqual(len(curve), len(logs))
        self.assertTrue(all(str(t.get("as_of") or "")[:10] for t in trades))
        self.assertTrue(
            all(
                s.get("side") in ("buy", "sell", "hold")
                or s.get("status") == "skipped"
                for s in sim
            )
        )
        paper_trades = (out.get("paper") or {}).get("trades") or []
        self.assertEqual(len(paper_trades), len(trades))
        self.assertEqual(out["params"]["lot_base_amount"], REPLAY_AMOUNT_BASE)
        self.assertEqual(out["params"]["lot_strong_amount"], REPLAY_AMOUNT_STRONG)
        self.assertAlmostEqual(float(out["params"]["rank_enter"]), REPLAY_RANK_ENTER)
        self.assertAlmostEqual(float(out["params"]["rank_strong"]), REPLAY_RANK_STRONG)
        buys = [t for t in trades if t.get("side") == "buy"]
        self.assertTrue(buys)
        from core.paper.sizing import shares_from_amount
        for t in buys:
            sh = float(t.get("shares") or 0)
            px = float(t.get("price") or 0)
            want_b = shares_from_amount(REPLAY_AMOUNT_BASE, px)
            want_s = shares_from_amount(REPLAY_AMOUNT_STRONG, px)
            self.assertIn(sh, (float(want_b), float(want_s)), t)
            self.assertIn(t.get("action") or t.get("matrix_action"), ("open", "add"))
            self.assertTrue(str(t.get("open_date") or "")[:10], t)
        sim_buys = [
            s
            for s in sim
            if s.get("side") == "buy" and s.get("status") != "skipped"
        ]
        for s in sim_buys:
            self.assertTrue(str(s.get("open_date") or "")[:10], s)
            self.assertNotIn("cum_cost", s)
            self.assertNotIn("cost_price", s)

    def test_sim_trades_include_exit_when_rank_fails(self):
        """开仓后 ranking 未过入场：成交明细应有清仓腿。"""
        stock_bars = {
            "600519": _bars(18, step=0.5),
            "600036": _bars(18, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 9 else 0.02
            rankings[d] = [_rank_row("600519", y)]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=200_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sim = out.get("sim_trades") or []
        exits = [
            s
            for s in sim
            if s.get("action") == "exit" and s.get("stock_code") == "600519"
        ]
        self.assertTrue(exits, "未过入场应写入清仓腿")
        for e in exits:
            self.assertEqual(e.get("status"), "filled")
            self.assertEqual(e.get("side"), "sell")
            self.assertGreater(float(e.get("shares") or 0), 0)

    def test_skip_rows_keep_scores_when_minute_fill_missing(self):
        """09:40 缺该根分钟：拟清回退日开盘成交，不把底仓留到收盘。"""
        stock_bars = {"600519": _bars(18, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else -0.8
            rankings[d] = [
                {
                    **_rank_row("600519", y),
                    "y_oo": y,
                    "y_oc": y,
                    "ranking": y,
                }
            ]
        minutes = {"600519": {}}
        for i, b in enumerate(stock_bars["600519"]):
            if i > 10:
                continue
            d = b["date"]
            o = float(b["open"])
            minutes["600519"][d] = [
                {
                    "date": d,
                    "datetime": f"{d} 09:40:00",
                    "open": o,
                    "high": o + 0.5,
                    "low": o - 0.5,
                    "close": o + 0.2,
                    "prev_close": float(b.get("close") or o),
                }
            ]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=200_000.0,
                fill_clock="09:40",
                minute_bars_by_code=minutes,
                price_space_cfg={"t0_price_space_gate": False},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "sell" and t.get("stock_code") == "600519"
        ]
        self.assertTrue(sells, "缺分钟拟清应回退日开盘卖出")
        self.assertGreater(
            int((out.get("constraints_hit") or {}).get("sell_px_fallback") or 0), 0
        )
        self.assertTrue(
            any("回退日开盘" in str(t.get("reason") or "") for t in sells), sells
        )
        stuck = [
            s
            for s in (out.get("sim_trades") or [])
            if s.get("stock_code") == "600519"
            and "无有效报价未卖出" in str(s.get("reason") or "")
        ]
        self.assertFalse(stuck, stuck)

    def test_sim_converters_copy_y_co(self):
        from core.backtest.paper_replay import (
            _hold_to_sim,
            _ledger_trades_to_sim,
            _skip_to_sim,
        )

        rows = _ledger_trades_to_sim(
            [
                {
                    "side": "buy",
                    "stock_code": "600000",
                    "as_of": "2026-09-10",
                    "shares": 200,
                    "price": 10,
                    "y_oo": 1.0,
                    "y_oc": 0.5,
                    "y_on": 0.2,
                    "ranking": 1.2,
                    "ranking_score": 0.012,
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["y_co"]), 0.2)
        self.assertNotIn("y_on", rows[0])
        self.assertAlmostEqual(float(rows[0]["y_oo"]), 1.0)

        sk = _skip_to_sim(
            {
                "stock_code": "600000",
                "reason": "现金不足",
                "y_co": 0.3,
                "y_oo": 1.1,
                "ranking": 0.8,
            },
            "2026-09-10",
        )
        self.assertAlmostEqual(float(sk["y_co"]), 0.3)
        self.assertNotIn("y_on", sk)

        hold = _hold_to_sim(
            {
                "stock_code": "600000",
                "y_oo": 1.0,
                "y_oc": 0.4,
                "y_co": 0.25,
                "ranking": 0.9,
                "ranking_score": 0.009,
                "reason": "ranking≥0% 持有",
            },
            "2026-09-10",
            {"stock_code": "600000", "shares": 200, "cost": 10.0},
            date_maps={"600000": {"2026-09-10": {"open": 10.0, "close": 10.2}}},
            prices={"600000": 10.0},
        )
        self.assertAlmostEqual(float(hold["y_co"]), 0.25)
        self.assertNotIn("y_on", hold)

        from core.backtest.paper_replay import _hold_src_for_code

        src = _hold_src_for_code(
            "600000",
            plan={
                "holds": [],
                "sells": [
                    {
                        "stock_code": "600000",
                        "action": "exit",
                        "reason": "ranking=-0.500%<0% 清仓",
                        "y_oo": -0.5,
                        "ranking": -0.5,
                    }
                ],
                "skips": [],
            },
            scored=[],
        )
        self.assertAlmostEqual(float(src["y_oo"]), -0.5)
        self.assertIn("清仓", src.get("reason") or "")

    def test_sim_converters_copy_aux_yhat(self):
        from core.backtest.paper_replay import (
            _hold_to_sim,
            _ledger_trades_to_sim,
            _skip_to_sim,
        )

        src = {
            "side": "buy",
            "stock_code": "600000",
            "as_of": "2026-09-10",
            "shares": 200,
            "price": 10,
            "y_oo": 1.0,
            "ranking": 1.2,
            "y_τ30": 0.2,
            "y_τ60": -0.1,
            "y_τ90": 0.3,
            "y_hl": 1.5,
            "formula_terms_tau": {"total": 0.8, "terms": [{"key": "gap_pct", "contrib": 0.1}]},
            "formula_terms_path": {"total": 1.5, "terms": [{"key": "range_pct", "contrib": 0.2}]},
        }
        rows = _ledger_trades_to_sim([src])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("y_τ30", rows[0])
        self.assertNotIn("y_τw", rows[0])
        self.assertAlmostEqual(float(rows[0]["y_hl"]), 1.5)
        self.assertEqual(rows[0]["formula_terms_tau"]["total"], 0.8)
        self.assertEqual(rows[0]["formula_terms_path"]["total"], 1.5)

        sk = _skip_to_sim({**src, "reason": "现金不足"}, "2026-09-10")
        self.assertNotIn("y_τ60", sk)
        self.assertNotIn("y_τw", sk)
        self.assertAlmostEqual(float(sk["y_hl"]), 1.5)
        self.assertEqual(sk["formula_terms_tau"]["total"], 0.8)

        hold = _hold_to_sim(
            src,
            "2026-09-10",
            {"stock_code": "600000", "shares": 200, "cost": 10.0},
            date_maps={"600000": {"2026-09-10": {"open": 10.0, "close": 10.2}}},
            prices={"600000": 10.0},
        )
        self.assertNotIn("y_τ90", hold)
        self.assertNotIn("y_τw", hold)
        self.assertAlmostEqual(float(hold["y_hl"]), 1.5)
        self.assertEqual(hold["formula_terms_path"]["total"], 1.5)

    def test_sim_trades_keep_y_co_from_ranking(self):
        stock_bars = {"600519": _bars(16, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {
            d: [{**_rank_row("600519", 2.0), "y_co": 0.35, "y_on": 0.35}]
            for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=200_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        filled = [s for s in (out.get("sim_trades") or []) if s.get("status") == "filled"]
        self.assertTrue(filled)
        for s in filled:
            self.assertAlmostEqual(float(s.get("y_co")), 0.35, msg=s)
            self.assertNotIn("y_on", s, msg=s)

    def test_default_top_k_is_universe_size(self):
        stock_bars = {
            "600519": _bars(20, step=0.5),
            "600036": _bars(20, step=0.35),
            "300750": _bars(20, step=0.4),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {
            d: [
                _rank_row("600519", 2.0),
                _rank_row("600036", 1.8),
                _rank_row("300750", 1.6),
            ]
            for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                min_history=8,
                rankings_by_date=rankings,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"]["top_k"], 3)
        self.assertIn("观察池 3 只", out.get("note") or "")


class TestTopkEngineTag(unittest.TestCase):
    def test_topk_params_engine(self):
        from core.backtest.topk_backtest import backtest_topk_equal_weight
        from tests.test_p10_quant import _aligned_bars

        stock_bars = {
            "600519": _aligned_bars("a", n=40),
            "600036": _aligned_bars("b", n=40, step=0.35),
            "300750": _aligned_bars("c", n=40, step=0.45),
        }
        with patch(
            "adapters.market.history.fetch_a_daily_bars",
            side_effect=RuntimeError("no network"),
        ):
            result = backtest_topk_equal_weight(
                stock_bars,
                top_k=2,
                horizon_days=3,
                min_score=0,
                min_history=12,
                use_live_cluster_models=False,
                allow_heuristic_baseline=True,
                rank_mode="heuristic_score",
            )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertEqual(result.get("params", {}).get("engine"), "topk_research")
        self.assertIn("paper_replay", str(result.get("note") or ""))


class TestRankingsFromTopk(unittest.TestCase):
    def test_convert_picks(self):
        from core.backtest.paper_replay import rankings_from_topk_precomputed

        pre = {
            "2026-03-10": {
                "picks": [("600519", 1.5), ("600036", 1.2)],
                "items_by_code": {
                    "600519": {
                        "stock_code": "600519",
                        "predicted_score_eod": 1.5,
                        "score_scale": "predicted_yhat",
                    }
                },
            }
        }
        out = rankings_from_topk_precomputed(pre, top_k=1)
        self.assertEqual(len(out["2026-03-10"]), 1)
        self.assertEqual(out["2026-03-10"][0]["stock_code"], "600519")
        self.assertEqual(out["2026-03-10"][0]["rank_source"], "topk_precomputed")


class TestOpenDayYhat(unittest.TestCase):
    def test_attach_cluster_pred_as_y_trade(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads
        from core.signal.return_score import ReturnScoreModel

        model = ReturnScoreModel(
            intercept=0.8,
            coefficients={"mom3": 0.1},
            standardized=False,
        )
        entries = [
            {"stock_code": "600519", "sub_scores": {"mom3": 2.0}, "score": 55}
        ]
        out = _attach_open_yhat_heads(
            entries,
            quotes={},
            windows={},
            cluster_models={"600519": model},
            tau_model_doc={},
        )
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out[0]["predicted_score"]), 1.0)
        self.assertIsNotNone(out[0].get("y_trade"))
        self.assertGreater(float(out[0]["y_trade"]), 0.5)
        terms = out[0].get("score_formula_terms") or {}
        self.assertTrue(terms.get("terms"))
        self.assertEqual(terms["terms"][0]["key"], "mom3")
        self.assertAlmostEqual(float(terms["terms"][0]["contrib"]), 0.2)

    def test_attach_open_heads_writes_y_oo_not_nowcast(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads

        entries = [
            {
                "stock_code": "600519",
                "predicted_score": 1.2,
                "predicted_score_eod": 1.2,
                "score_scale": "predicted_yhat",
            }
        ]
        quote = {
            "open": 10.2,
            "prev_close": 10.0,
            "change_raw": 2.0,
            "date": "2026-03-10",
        }
        window = [
            {
                "date": "2026-03-09",
                "open": 9.9,
                "high": 10.1,
                "low": 9.8,
                "close": 10.0,
                "volume": 1,
            }
        ]
        out = _attach_open_yhat_heads(
            entries,
            quotes={"600519": quote},
            windows={"600519": window},
            tau_model_doc={},
        )
        self.assertAlmostEqual(float(out[0]["y_trade"]), 1.2, places=3)
        self.assertIsNone(out[0].get("y_nowcast"))
        self.assertIsNone(out[0].get("predicted_score_nowcast"))
        self.assertAlmostEqual(float(out[0].get("y_oo")), 1.2, places=3)

    def test_attach_open_heads_stamps_pool_cs_and_open_minute_z(self):
        from unittest.mock import patch

        from core.backtest.paper_replay import _attach_open_yhat_heads

        entries = [
            {
                "stock_code": "600519",
                "predicted_score": 1.2,
                "predicted_score_eod": 1.2,
                "score_scale": "predicted_yhat",
            }
        ]
        quote = {
            "open": 10.2,
            "prev_close": 10.0,
            "change_raw": 2.0,
            "date": "2026-03-10",
        }
        window = [
            {
                "date": "2026-03-09",
                "open": 9.9,
                "high": 10.1,
                "low": 9.8,
                "close": 10.0,
                "volume": 1,
            }
        ]
        rem_doc = {
            "return_model": {
                "intercept": 0.1,
                "coefficients": {
                    "gap_pct": -0.04,
                    "sector_gap_breadth": 0.02,
                    "ret_open_to_tau": -0.5,
                    "tau_elapsed_min": 0.01,
                },
                "active_features": [
                    "gap_pct",
                    "sector_gap_breadth",
                    "ret_open_to_tau",
                    "tau_elapsed_min",
                ],
                "zscore_means": {
                    "gap_pct": 0.0,
                    "sector_gap_breadth": 0.0,
                    "ret_open_to_tau": 0.4,
                    "tau_elapsed_min": 20.0,
                },
                "zscore_stds": {
                    "gap_pct": 1.0,
                    "sector_gap_breadth": 1.0,
                    "ret_open_to_tau": 1.0,
                    "tau_elapsed_min": 1.0,
                },
            },
            "model_role": "research",
        }
        pool = {
            "pool_gaps": [2.0, 0.5, -0.2],
            "sector_gap_breadth": 0.33,
            "ref_by_code": {"600519": 0.5},
        }
        with patch("core.research.tau_ridge.load_tau_model", return_value=rem_doc):
            out = _attach_open_yhat_heads(
                entries,
                quotes={"600519": quote},
                windows={"600519": window},
                tau_model_doc=rem_doc,
                tau_pool_day=pool,
            )
        feats = out[0].get("features_tau") or {}
        self.assertAlmostEqual(float(feats.get("ret_open_to_tau")), 0.0, places=6)
        self.assertAlmostEqual(float(feats.get("tau_elapsed_min")), 0.0, places=6)
        self.assertAlmostEqual(float(feats.get("sector_gap_breadth")), 0.33, places=6)
        keys = {t["key"] for t in (out[0].get("formula_terms_tau") or {}).get("terms") or []}
        self.assertIn("ret_open_to_tau", keys)
        self.assertIn("tau_elapsed_min", keys)
        self.assertIn("sector_gap_breadth", keys)

    def test_attach_open_nowcast_missing_is_none_not_zero(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads

        entries = [{"stock_code": "600519", "score": 55}]
        out = _attach_open_yhat_heads(
            entries,
            quotes={},
            windows={},
            tau_model_doc={},
        )
        yn = out[0].get("y_nowcast")
        nc = out[0].get("predicted_score_nowcast")
        self.assertIsNone(yn)
        self.assertIsNone(nc)

    def test_attach_open_yhat_heads_calls_path_attach(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads

        seen = []

        def _fake_path(item, hist_bars=None, allow_open_z=True, **_kwargs):
            seen.append(
                (
                    item.get("stock_code"),
                    bool(allow_open_z),
                    len(hist_bars or []),
                )
            )
            item["y_hl"] = 1.25

        entries = [
            {
                "stock_code": "600519",
                "predicted_score": 1.2,
                "predicted_score_eod": 1.2,
            }
        ]
        window = [
            {
                "date": "2026-03-09",
                "open": 9.9,
                "high": 10.1,
                "low": 9.8,
                "close": 10.0,
                "volume": 1,
            }
        ]
        with patch(
            "core.t0.score_policy._attach_y_path_to_item",
            side_effect=_fake_path,
        ):
            out = _attach_open_yhat_heads(
                entries,
                quotes={
                    "600519": {
                        "open": 10.2,
                        "prev_close": 10.0,
                        "date": "2026-03-10",
                    }
                },
                windows={"600519": window},
                tau_model_doc={},
            )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "600519")
        self.assertTrue(seen[0][1])
        self.assertEqual(seen[0][2], 1)
        self.assertEqual(out[0].get("date"), "2026-03-10")
        self.assertAlmostEqual(float(out[0]["y_hl"]), 1.25)
        self.assertNotIn("y_τ30", out[0])

    def test_score_open_day_includes_overheated(self):
        from core.backtest.paper_replay import _score_open_day
        from tests.test_signal import _overheated_bars

        bars = _overheated_bars()
        dates = [str(b["date"]) for b in bars]
        date_maps = {"600869": {str(b["date"]): b for b in bars}}
        scored = _score_open_day(
            stock_bars={"600869": bars},
            dates=dates,
            date_maps=date_maps,
            day_i=len(dates) - 1,
            max_window=30,
            horizon_days=1,
            cfg=None,
            cluster_models={},
            global_model=None,
            tau_model_doc={},
        )
        codes = {str(it.get("stock_code") or "") for it in scored}
        self.assertIn("600869", codes)
        row = next(it for it in scored if str(it.get("stock_code")) == "600869")
        self.assertTrue(row.get("mom_chase_risk") or (row.get("overheat") or {}).get("hit"))
        self.assertFalse(row.get("paper_hard_reject"))
        self.assertFalse(row.get("hard_reject"))


class TestRealizedYhatWindows(unittest.TestCase):
    def test_cc_and_on_match_bar_math(self):
        bars = _bars(6, step=0.5, start=100.0)
        dates = [b["date"] for b in bars]
        date_maps = {"600519": {b["date"]: b for b in bars}}
        r_cc, r_on, r_tau = realized_yhat_windows(
            "600519",
            dates[2],
            dates=dates,
            date_maps=date_maps,
        )
        prev_c = float(bars[1]["close"])
        close_t = float(bars[2]["close"])
        nxt_o = float(bars[3]["open"])
        open_t = float(bars[2]["open"])
        self.assertAlmostEqual(r_cc, (close_t / prev_c - 1.0) * 100.0, places=4)
        self.assertAlmostEqual(r_on, (nxt_o / close_t - 1.0) * 100.0, places=4)
        self.assertAlmostEqual(r_tau, (close_t / open_t - 1.0) * 100.0, places=4)

    def test_edges_are_none(self):
        bars = _bars(4, step=0.4, start=100.0)
        dates = [b["date"] for b in bars]
        date_maps = {"600519": {b["date"]: b for b in bars}}
        first_cc, first_on, first_tau = realized_yhat_windows(
            "600519", dates[0], dates=dates, date_maps=date_maps
        )
        last_cc, last_on, last_tau = realized_yhat_windows(
            "600519", dates[-1], dates=dates, date_maps=date_maps
        )
        self.assertIsNone(first_cc)
        self.assertIsNotNone(first_on)
        self.assertIsNotNone(first_tau)
        self.assertIsNotNone(last_cc)
        self.assertIsNone(last_on)
        self.assertIsNotNone(last_tau)
        miss_cc, miss_on, miss_tau = realized_yhat_windows(
            "", dates[1], dates=dates, date_maps=date_maps
        )
        self.assertIsNone(miss_cc)
        self.assertIsNone(miss_on)
        self.assertIsNone(miss_tau)

    def test_stamp_r_tau_uses_fill_not_daily_open(self):
        """R_τ 真实=收盘/成交价(τ)，不是收盘/日开（生益 09:40 口径）。"""
        from core.backtest.paper_replay import stamp_r_tau_on_row
        from core.signal.yhat_geom import remaining_at_tau

        row = {
            "y_oc": 5.25,
            "status": "filled",
            "side": "buy",
            "price": 153.2,
        }
        bar = {"open": 146.0, "close": 150.25}
        mins = [
            {
                "datetime": "2026-09-15 09:40:00",
                "open": 153.54,
                "close": 153.2,
            }
        ]
        stamp_r_tau_on_row(
            row, daily_bar=bar, minute_bars=mins, fill_clock="09:40"
        )
        self.assertAlmostEqual(row["day_open"], 146.0)
        self.assertAlmostEqual(row["day_close"], 150.25)
        self.assertAlmostEqual(row["r_realized"], (150.25 / 153.2 - 1.0) * 100.0, places=3)
        rot = (153.2 / 146.0 - 1.0) * 100.0
        self.assertAlmostEqual(row["r_hat"], remaining_at_tau(5.25, rot), places=3)

    def test_stamp_r_tau_hold_ignores_close_mark(self):
        from core.backtest.paper_replay import stamp_r_tau_on_row

        row = {
            "y_oc": 5.25,
            "status": "held",
            "side": "hold",
            "price": 150.25,
        }
        bar = {"open": 146.0, "close": 150.25}
        mins = [
            {
                "datetime": "2026-09-15 09:40:00",
                "open": 153.54,
                "close": 153.2,
            }
        ]
        stamp_r_tau_on_row(
            row, daily_bar=bar, minute_bars=mins, fill_clock="09:40"
        )
        self.assertAlmostEqual(row["r_realized"], (150.25 / 153.2 - 1.0) * 100.0, places=3)
        self.assertNotAlmostEqual(row["r_realized"], 0.0, places=2)

    def test_uses_stock_bar_after_window(self):
        """覆盖率日历截在 T 时，仍用该票仓里的 T+1 开盘对账 ŷ_co。"""
        bars = _bars(5, step=0.5, start=100.0)
        all_dates = [b["date"] for b in bars]
        window = all_dates[:-1]
        date_maps = {"600519": {b["date"]: b for b in bars}}
        _cc, r_on, r_tau = realized_yhat_windows(
            "600519",
            window[-1],
            dates=window,
            date_maps=date_maps,
        )
        close_t = float(bars[-2]["close"])
        nxt_o = float(bars[-1]["open"])
        self.assertIsNotNone(r_tau)
        self.assertAlmostEqual(r_on, (nxt_o / close_t - 1.0) * 100.0, places=4)

    def test_path_label_from_minutes(self):
        day = "2026-03-03"
        bar = {"date": day, "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2}
        mins = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": 10.0,
                "high": 10.05,
                "low": 9.8,
                "close": 9.9,
            },
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 9.9,
                "high": 10.5,
                "low": 9.9,
                "close": 10.2,
            },
        ]
        val = realized_path_label(
            "600519",
            day,
            date_maps={"600519": {day: bar}},
            minute_maps={"600519": {day: mins}},
        )
        self.assertIsNotNone(val)
        self.assertGreater(float(val), 0.0)

    def test_replay_stamps_y_hl_realized(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = {}
        for code, bars in stock_bars.items():
            by_d = {}
            for b in bars:
                d = b["date"]
                o = float(b["open"])
                by_d[d] = [
                    {
                        "date": d,
                        "datetime": f"{d} 09:35:00",
                        "open": o,
                        "high": o + 0.05,
                        "low": o - 0.4,
                        "close": o - 0.1,
                    },
                    {
                        "date": d,
                        "datetime": f"{d} 14:55:00",
                        "open": o + 0.1,
                        "high": o + 0.6,
                        "low": o,
                        "close": float(b["close"]),
                    },
                ]
            minutes[code] = by_d
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                minute_bars_by_code=minutes,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        last = dates[-1]
        filled = [
            t
            for t in (out.get("sim_trades") or [])
            if t.get("stock_code") == "600519" and t.get("status") != "skipped"
        ]
        self.assertTrue(filled)
        mid = next(t for t in filled if str(t.get("as_of") or "")[:10] != last)
        self.assertIsNotNone(mid.get("y_hl_realized"), mid)
        self.assertGreater(float(mid.get("y_hl_realized")), 0.0)
        last_rows = [
            t for t in filled if str(t.get("as_of") or "")[:10] == last
        ]
        if last_rows:
            self.assertIsNone(last_rows[0].get("realized_on"))
            self.assertIsNone(last_rows[0].get("realized_oo"))
            self.assertIsNone(last_rows[0].get("realized_ranking"))
            self.assertIsNotNone(last_rows[0].get("y_hl_realized"))
            self.assertIsNotNone(last_rows[0].get("realized_tau"))


class TestSessionOverlay(unittest.TestCase):
    def test_before_open_is_none(self):
        from core.backtest.paper_replay import replay_session_day

        self.assertIsNone(replay_session_day(now=datetime(2026, 9, 8, 9, 0, 0)))

    def test_after_open_is_today(self):
        from core.backtest.paper_replay import replay_session_day

        self.assertEqual(
            replay_session_day(now=datetime(2026, 9, 8, 10, 0, 0)), "2026-09-08"
        )

    def test_before_fill_clock_is_none(self):
        from core.backtest.paper_replay import replay_session_day

        self.assertIsNone(
            replay_session_day(
                now=datetime(2026, 9, 8, 9, 32, 0), fill_clock="09:35"
            )
        )
        self.assertEqual(
            replay_session_day(
                now=datetime(2026, 9, 8, 9, 35, 0), fill_clock="09:35"
            ),
            "2026-09-08",
        )

    def test_stale_sample_not_jumped(self):
        from core.backtest.paper_replay import overlay_session_day_bars

        stock = {"600000": _bars(10), "600001": _bars(10, step=0.2)}
        out, session, n = overlay_session_day_bars(
            stock,
            now=datetime(2026, 9, 8, 10, 0, 0),
            quotes_by_code={"600000": {"open": 1.0}, "600001": {"open": 2.0}},
            fetch_quotes=False,
        )
        self.assertIsNone(session)
        self.assertEqual(n, 0)
        self.assertEqual(out["600000"][-1]["date"], stock["600000"][-1]["date"])

    def test_overlay_appends_open_bar(self):
        from core.backtest.paper_replay import overlay_session_day_bars
        from core.market.calendar import prev_trading_day

        last = prev_trading_day("2026-09-08")
        bar = {
            "date": last,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1,
        }
        stock = {"600000": [dict(bar)], "600001": [dict(bar)]}
        out, session, n = overlay_session_day_bars(
            stock,
            now=datetime(2026, 9, 8, 10, 0, 0),
            quotes_by_code={
                "600000": {"success": True, "open": 10.2},
                "600001": {"success": True, "open": 20.0},
            },
            fetch_quotes=False,
        )
        self.assertEqual(session, "2026-09-08")
        self.assertEqual(n, 2)
        self.assertTrue(out["600000"][-1].get("session_overlay"))
        self.assertAlmostEqual(float(out["600000"][-1]["open"]), 10.2)
        self.assertEqual(out["600000"][-1]["date"], "2026-09-08")

    def test_overlay_parses_yuan_open_not_last(self):
        from core.backtest.paper_replay import overlay_session_day_bars
        from core.market.calendar import prev_trading_day

        last = prev_trading_day("2026-09-08")
        bar = {
            "date": last,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1,
        }
        stock = {"600000": [dict(bar)]}
        out, session, n = overlay_session_day_bars(
            stock,
            now=datetime(2026, 9, 8, 10, 0, 0),
            quotes_by_code={
                "600000": {"open": "10.2元", "price": "99.0元", "last": 99.0},
            },
            fetch_quotes=False,
        )
        self.assertEqual(session, "2026-09-08")
        self.assertEqual(n, 1)
        self.assertAlmostEqual(float(out["600000"][-1]["open"]), 10.2)

    def test_overlay_skips_when_only_last(self):
        from core.backtest.paper_replay import overlay_session_day_bars
        from core.market.calendar import prev_trading_day

        last = prev_trading_day("2026-09-08")
        bar = {
            "date": last,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1,
        }
        stock = {"600000": [dict(bar)]}
        out, session, n = overlay_session_day_bars(
            stock,
            now=datetime(2026, 9, 8, 10, 0, 0),
            quotes_by_code={"600000": {"price": 99.0, "last": 99.0}},
            fetch_quotes=False,
        )
        self.assertEqual(n, 0)
        self.assertIsNone(session)
        self.assertEqual(out["600000"][-1]["date"], last)

    def test_realized_none_on_overlay(self):
        from core.backtest.paper_replay import overlay_session_day_bars

        last = "2026-09-07"
        bars = [
            {
                "date": "2026-09-04",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1,
            },
            {
                "date": last,
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.2,
                "volume": 1,
            },
        ]
        stock = {"600519": bars}
        out, session, n = overlay_session_day_bars(
            stock,
            now=datetime(2026, 9, 8, 10, 0, 0),
            quotes_by_code={"600519": {"open": 10.4}},
            fetch_quotes=False,
        )
        self.assertEqual(session, "2026-09-08")
        self.assertEqual(n, 1)
        dates = [b["date"] for b in out["600519"]]
        date_maps = {"600519": {b["date"]: b for b in out["600519"]}}
        r_cc, r_on, r_tau = realized_yhat_windows(
            "600519", session, dates=dates, date_maps=date_maps
        )
        self.assertIsNone(r_cc)
        self.assertIsNone(r_on)
        self.assertIsNone(r_tau)

    def test_replay_runs_session_day(self):
        from core.market.calendar import prev_trading_day

        last = prev_trading_day("2026-09-08")
        days = []
        d = last
        while len(days) < 16:
            days.append(d)
            d = prev_trading_day(d)
        days = list(reversed(days))

        def _mk(step):
            rows = []
            for i, day in enumerate(days):
                close = 100.0 + i * step
                rows.append(
                    {
                        "date": day,
                        "open": close - 0.2,
                        "high": close + 0.5,
                        "low": close - 0.5,
                        "close": close,
                        "volume": 100000,
                    }
                )
            return rows

        stock_bars = {
            "600519": _mk(0.5),
            "600036": _mk(0.3),
        }
        rankings = {
            d: [_rank_row("600519", 2.0)] for d in days + ["2026-09-08"]
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                session_now=datetime(2026, 9, 8, 10, 0, 0),
                session_quotes={"600519": {"open": 108.0}, "600036": {"open": 50.0}},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"].get("session_day"), "2026-09-08")
        self.assertEqual(out["params"].get("end_date"), "2026-09-08")
        curve_days = [str(p.get("date") or "")[:10] for p in (out.get("equity_curve") or [])]
        self.assertIn("2026-09-08", curve_days)
        session_fills = [
            t
            for t in (out.get("sim_trades") or [])
            if str(t.get("as_of") or "")[:10] == "2026-09-08"
            and t.get("status") != "skipped"
        ]
        for t in session_fills:
            self.assertIsNone(t.get("realized_cc"))
            self.assertIsNone(t.get("realized_on"))
            self.assertIsNone(t.get("realized_tau"))


class TestDayStockLegs(unittest.TestCase):
    def test_hold_contrib_from_prev_close_to_close(self):
        from core.backtest.paper_replay import day_stock_legs

        date_maps = {
            "600519": {
                "2026-03-09": {"open": 99.0, "close": 100.0},
                "2026-03-10": {"open": 101.0, "close": 102.0},
            }
        }
        held = {
            "600519": {
                "stock_code": "600519",
                "stock_name": "茅台",
                "shares": 1000.0,
            }
        }
        legs, more = day_stock_legs(
            start=held,
            end=held,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px={"600519": 101.0},
            prev_equity=1_000_000.0,
        )
        self.assertEqual(more, 0)
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["stock_code"], "600519")
        self.assertEqual(legs[0]["stock_name"], "茅台")
        self.assertAlmostEqual(legs[0]["ret_pct"], 2.0, places=3)
        self.assertAlmostEqual(legs[0]["contrib_pct"], 0.2, places=4)
        self.assertAlmostEqual(legs[0]["pnl"], 2000.0, places=2)
        self.assertAlmostEqual(legs[0]["overnight_pnl"], 1000.0, places=2)
        self.assertAlmostEqual(legs[0]["intraday_pnl"], 1000.0, places=2)

    def test_exit_at_open_ret_is_overnight_only(self):
        from core.backtest.paper_replay import day_stock_legs

        date_maps = {
            "688825": {
                "2026-09-07": {"open": 58.5, "close": 58.48},
                "2026-09-08": {"open": 58.4, "close": 57.0},
            }
        }
        start = {
            "688825": {
                "stock_code": "688825",
                "stock_name": "长鑫科技",
                "shares": 2000.0,
            }
        }
        legs, more = day_stock_legs(
            start=start,
            end={},
            date_maps=date_maps,
            prev_day="2026-09-07",
            day="2026-09-08",
            open_px={"688825": 58.4},
            prev_equity=525_368.64,
        )
        self.assertEqual(more, 0)
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["shares"], 0)
        overnight = (58.4 / 58.48 - 1.0) * 100.0
        self.assertAlmostEqual(legs[0]["ret_pct"], overnight, places=3)
        self.assertLess(abs(legs[0]["ret_pct"]), 1.0)
        self.assertNotAlmostEqual(legs[0]["ret_pct"], (57.0 / 58.48 - 1.0) * 100.0, places=2)
        pnl = 2000.0 * (58.4 - 58.48)
        self.assertAlmostEqual(legs[0]["contrib_pct"], pnl / 525_368.64 * 100.0, places=4)

    def test_open_at_open_ret_is_intraday_only(self):
        from core.backtest.paper_replay import day_stock_legs

        date_maps = {
            "600183": {
                "2026-03-09": {"close": 10.0},
                "2026-03-10": {"open": 10.2, "close": 10.4},
            }
        }
        end = {
            "600183": {
                "stock_code": "600183",
                "stock_name": "生益科技",
                "shares": 1000.0,
            }
        }
        legs, more = day_stock_legs(
            start={},
            end=end,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px={"600183": 10.2},
            prev_equity=1_000_000.0,
        )
        self.assertEqual(more, 0)
        self.assertAlmostEqual(legs[0]["ret_pct"], (10.4 / 10.2 - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(legs[0]["contrib_pct"], 1000.0 * (10.4 - 10.2) / 1_000_000.0 * 100.0, places=4)

    def test_caps_top_legs(self):
        from core.backtest.paper_replay import DAY_LEG_TOP, day_stock_legs

        date_maps = {}
        start = {}
        end = {}
        open_px = {}
        for i in range(DAY_LEG_TOP + 3):
            code = f"{600000 + i}"
            date_maps[code] = {
                "2026-03-09": {"close": 10.0},
                "2026-03-10": {"open": 10.0, "close": 10.0 + (i + 1) * 0.1},
            }
            row = {"stock_code": code, "stock_name": code, "shares": 1000.0}
            start[code] = row
            end[code] = row
            open_px[code] = 10.0
        legs, more = day_stock_legs(
            start=start,
            end=end,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px=open_px,
            prev_equity=1_000_000.0,
        )
        self.assertEqual(len(legs), DAY_LEG_TOP)
        self.assertEqual(more, 3)
        uncapped, more0 = day_stock_legs(
            start=start,
            end=end,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px=open_px,
            prev_equity=1_000_000.0,
            top=0,
        )
        self.assertEqual(len(uncapped), DAY_LEG_TOP + 3)
        self.assertEqual(more0, 0)

    def test_name_by_code_overrides_code_label(self):
        from core.backtest.paper_replay import day_stock_legs

        date_maps = {
            "600519": {
                "2026-03-09": {"open": 99.0, "close": 100.0},
                "2026-03-10": {"open": 101.0, "close": 102.0},
            }
        }
        held = {
            "600519": {
                "stock_code": "600519",
                "stock_name": "600519",
                "shares": 1000.0,
            }
        }
        legs, _more = day_stock_legs(
            start=held,
            end=held,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px={"600519": 101.0},
            prev_equity=1_000_000.0,
            name_by_code={"600519": "贵州茅台"},
        )
        self.assertEqual(legs[0]["stock_name"], "贵州茅台")


class TestStockContrib(unittest.TestCase):
    def test_aggregates_hold_and_exit(self):
        from core.backtest.paper_replay import (
            accumulate_day_contrib,
            day_stock_legs,
            finalize_stock_contrib,
        )

        date_maps = {
            "600519": {
                "2026-03-09": {"open": 99.0, "close": 100.0},
                "2026-03-10": {"open": 101.0, "close": 102.0},
                "2026-03-11": {"open": 103.0, "close": 99.0},
            }
        }
        held = {
            "600519": {
                "stock_code": "600519",
                "stock_name": "茅台",
                "shares": 1000.0,
            }
        }
        acc = {}
        d1, _ = day_stock_legs(
            start=held,
            end=held,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px={"600519": 101.0},
            prev_equity=1_000_000.0,
            top=0,
        )
        accumulate_day_contrib(acc, d1, day="2026-03-10")
        d2, _ = day_stock_legs(
            start=held,
            end={},
            date_maps=date_maps,
            prev_day="2026-03-10",
            day="2026-03-11",
            open_px={"600519": 103.0},
            prev_equity=1_002_000.0,
            top=0,
        )
        accumulate_day_contrib(acc, d2, day="2026-03-11")
        rows = finalize_stock_contrib(
            acc,
            initial_cash=1_000_000.0,
            trades=[
                {"stock_code": "600519", "side": "buy", "shares": 1000},
                {"stock_code": "600519", "side": "sell", "shares": 1000},
            ],
        )
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["stock_code"], "600519")
        self.assertEqual(r["stock_name"], "茅台")
        self.assertEqual(r["hold_days"], 2)
        self.assertEqual(r["buy_count"], 1)
        self.assertEqual(r["sell_count"], 1)
        self.assertEqual(r["shares_end"], 0)
        self.assertEqual(r["first_date"], "2026-03-10")
        self.assertEqual(r["last_date"], "2026-03-11")
        # d1: 1000*(102-101)+1000*(101-100)=2000；d2 清仓只计隔夜 1000*(103-102)=1000
        self.assertAlmostEqual(r["pnl"], 3000.0, places=2)
        self.assertAlmostEqual(r["contrib_pct"], 0.3, places=4)
        self.assertAlmostEqual(r["overnight_pnl"], 2000.0, places=2)
        self.assertAlmostEqual(r["intraday_pnl"], 1000.0, places=2)

    def test_uncapped_rows_keep_small_contrib_names(self):
        from core.backtest.paper_replay import (
            DAY_LEG_TOP,
            accumulate_day_contrib,
            day_stock_legs,
            finalize_stock_contrib,
        )

        date_maps = {}
        start = {}
        end = {}
        open_px = {}
        n = DAY_LEG_TOP + 4
        for i in range(n):
            code = f"{600000 + i}"
            date_maps[code] = {
                "2026-03-09": {"close": 10.0},
                "2026-03-10": {"open": 10.0, "close": 10.0 + (i + 1) * 0.1},
            }
            row = {"stock_code": code, "stock_name": f"票{i}", "shares": 1000.0}
            start[code] = row
            end[code] = row
            open_px[code] = 10.0
        legs, more = day_stock_legs(
            start=start,
            end=end,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px=open_px,
            prev_equity=1_000_000.0,
        )
        self.assertEqual(len(legs), DAY_LEG_TOP)
        self.assertEqual(more, 4)
        all_legs, _ = day_stock_legs(
            start=start,
            end=end,
            date_maps=date_maps,
            prev_day="2026-03-09",
            day="2026-03-10",
            open_px=open_px,
            prev_equity=1_000_000.0,
            top=0,
        )
        acc = {}
        accumulate_day_contrib(acc, all_legs, day="2026-03-10")
        rows = finalize_stock_contrib(acc, initial_cash=1_000_000.0)
        self.assertEqual(len(rows), n)
        self.assertEqual(rows[0]["stock_code"], f"{600000 + n - 1}")


class TestReplayMinuteFill(unittest.TestCase):
    def test_clamp_fill_clock(self):
        from core.backtest.paper_replay import (
            REPLAY_FILL_CLOCKS,
            clamp_replay_fill_clock,
        )

        self.assertEqual(clamp_replay_fill_clock("9:35"), "09:35")
        self.assertEqual(clamp_replay_fill_clock("0935"), "09:35")
        self.assertEqual(clamp_replay_fill_clock("10:00"), "10:00")
        self.assertEqual(clamp_replay_fill_clock("11:00"), "09:30")
        self.assertEqual(clamp_replay_fill_clock(None), "09:30")
        self.assertEqual(len(REPLAY_FILL_CLOCKS), 7)

    def test_fill_px_0930_uses_first_open(self):
        from core.backtest.paper_replay import replay_fill_px

        daily = {"open": 100.0, "close": 101.0}
        mins = [
            {
                "datetime": "2026-03-10 09:35:00",
                "open": 100.4,
                "close": 100.8,
            }
        ]
        self.assertAlmostEqual(
            replay_fill_px(daily_bar=daily, minute_bars=mins, fill_clock="09:30"),
            100.4,
        )
        self.assertAlmostEqual(
            replay_fill_px(daily_bar=daily, minute_bars=mins, fill_clock="09:35"),
            100.8,
        )
        self.assertIsNone(
            replay_fill_px(daily_bar=daily, minute_bars=mins, fill_clock="10:00")
        )
        self.assertAlmostEqual(
            replay_fill_px(daily_bar=daily, minute_bars=[], fill_clock="09:30"),
            100.0,
        )
        afternoon = [
            {
                "datetime": "2026-03-10 14:45:00",
                "open": 99.0,
                "close": 99.5,
            }
        ]
        self.assertAlmostEqual(
            replay_fill_px(daily_bar=daily, minute_bars=afternoon, fill_clock="09:30"),
            100.0,
        )
        self.assertIsNone(
            replay_fill_px(daily_bar=daily, minute_bars=afternoon, fill_clock="09:40")
        )

    def test_sell_fill_falls_back_later_then_open(self):
        from core.backtest.paper_replay import replay_sell_fill_px

        daily = {"open": 100.0, "close": 101.0}
        later = [
            {
                "datetime": "2026-03-10 09:45:00",
                "open": 100.2,
                "close": 100.6,
            }
        ]
        px, src = replay_sell_fill_px(
            daily_bar=daily, minute_bars=later, fill_clock="09:40"
        )
        self.assertAlmostEqual(px, 100.6)
        self.assertEqual(src, "09:45")
        px2, src2 = replay_sell_fill_px(
            daily_bar=daily, minute_bars=[], fill_clock="09:40"
        )
        self.assertAlmostEqual(px2, 100.0)
        self.assertEqual(src2, "daily_open")
        afternoon = [
            {
                "datetime": "2026-03-10 14:45:00",
                "open": 90.0,
                "close": 91.0,
            }
        ]
        px3, src3 = replay_sell_fill_px(
            daily_bar=daily, minute_bars=afternoon, fill_clock="09:40"
        )
        self.assertAlmostEqual(px3, 100.0)
        self.assertEqual(src3, "daily_open")

    def test_engine_fills_at_935_close(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = _minutes_aligned(stock_bars, close_delta=0.4)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code=minutes,
                price_space_cfg={"t0_price_space_gate": True},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"]["fill_clock"], "09:35")
        self.assertEqual(out["params"]["execution_mode"], "minute_5m")
        buys = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "buy" and t.get("stock_code") == "600519"
        ]
        self.assertTrue(buys)
        daily_open = float(stock_bars["600519"][8]["open"])
        self.assertAlmostEqual(float(buys[0].get("price") or 0), daily_open + 0.4, places=4)
        self.assertNotAlmostEqual(float(buys[0].get("price") or 0), daily_open, places=2)
        buy_day = str(buys[0].get("as_of") or buys[0].get("ts") or "")[:10]
        sim = next(
            (
                t
                for t in (out.get("sim_trades") or [])
                if t.get("stock_code") == "600519"
                and t.get("side") == "buy"
                and str(t.get("as_of") or "")[:10] == buy_day
            ),
            None,
        )
        self.assertIsNotNone(sim)
        fill = float(buys[0].get("price") or 0)
        close_t = float(next(b for b in stock_bars["600519"] if b["date"] == buy_day)["close"])
        self.assertAlmostEqual(
            float(sim.get("r_realized")),
            (close_t / fill - 1.0) * 100.0,
            places=3,
        )
        self.assertIsNotNone(sim.get("r_hat"))

    def test_935_skips_when_minute_missing(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code={},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        buys = [t for t in (out.get("trades") or []) if t.get("side") == "buy"]
        self.assertEqual(buys, [])
        self.assertGreater(int((out.get("constraints_hit") or {}).get("minute_skips") or 0), 0)

    def test_exit_falls_back_when_only_afternoon_minutes(self):
        stock_bars = {"600519": _bars(16, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else -0.8
            rankings[d] = [_rank_row("600519", y)]
        minutes = {"600519": {}}
        for i, b in enumerate(stock_bars["600519"]):
            d = b["date"]
            o = float(b["open"])
            if i <= 10:
                minutes["600519"][d] = [
                    {
                        "date": d,
                        "datetime": f"{d} 09:40:00",
                        "open": o,
                        "close": o + 0.2,
                    }
                ]
            else:
                minutes["600519"][d] = [
                    {
                        "date": d,
                        "datetime": f"{d} 14:45:00",
                        "open": o - 5,
                        "close": o - 4,
                    }
                ]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                initial_cash=200_000.0,
                fill_clock="09:40",
                minute_bars_by_code=minutes,
                price_space_cfg={"t0_price_space_gate": True},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "sell" and t.get("stock_code") == "600519"
        ]
        self.assertTrue(sells)
        self.assertTrue(any("回退日开盘" in str(t.get("reason") or "") for t in sells))
        for t in sells:
            day = str(t.get("as_of") or "")[:10]
            bar = next(b for b in stock_bars["600519"] if b["date"] == day)
            self.assertAlmostEqual(float(t.get("price") or 0), float(bar["open"]), places=4)

    def test_price_space_check_open_and_prev(self):
        from core.backtest.paper_replay import replay_price_space_check

        day = {"open": 100.0, "prev_close": 99.0}
        mins = [{"datetime": "2026-03-10 09:35:00", "open": 100.2, "prev_close": 99.1}]
        skip, seen = replay_price_space_check(
            day, mins, {"t0_price_space_max_dev_pct": 5.0, "t0_price_space_prev_dev_pct": 5.0}
        )
        self.assertIsNone(skip)
        self.assertFalse(seen)
        bad_open = replay_price_space_check(
            {"open": 110.0, "prev_close": 99.0},
            mins,
            {"t0_price_space_max_dev_pct": 0.25, "t0_price_space_prev_dev_pct": 5.0},
        )
        self.assertIsNotNone(bad_open[0])
        self.assertIn("price_space_mismatch", str(bad_open[0]))
        self.assertTrue(bad_open[1])
        bad_prev = replay_price_space_check(
            {"open": 100.0, "prev_close": 110.0},
            [{"datetime": "2026-03-10 09:35:00", "open": 100.0, "prev_close": 100.0}],
            {"t0_price_space_max_dev_pct": 5.0, "t0_price_space_prev_dev_pct": 0.25},
        )
        self.assertIsNotNone(bad_prev[0])
        self.assertIn("P_d/P_m", str(bad_prev[0]))
        off = replay_price_space_check(
            {"open": 110.0, "prev_close": 99.0},
            mins,
            {
                "t0_price_space_gate": False,
                "t0_price_space_max_dev_pct": 0.25,
                "t0_price_space_prev_dev_pct": 0.25,
            },
        )
        self.assertIsNone(off[0])
        self.assertTrue(off[1])
        none_m, seen_m = replay_price_space_check(day, [], {"t0_price_space_gate": True})
        self.assertIsNone(none_m)
        self.assertFalse(seen_m)
        afternoon_only, seen_a = replay_price_space_check(
            {"open": 100.0, "prev_close": 99.0},
            [{"datetime": "2026-03-10 14:45:00", "open": 80.0, "prev_close": 70.0}],
            {"t0_price_space_gate": True, "t0_price_space_max_dev_pct": 0.25},
        )
        self.assertIsNone(afternoon_only)
        self.assertFalse(seen_a)

    def test_engine_skips_open_mismatch(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = _minutes_aligned(stock_bars, close_delta=0.4, open_scale=0.8)
        tight = {
            "t0_price_space_gate": True,
            "t0_price_space_max_dev_pct": 0.25,
            "t0_price_space_prev_dev_pct": 5.0,
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code=minutes,
                price_space_cfg=tight,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        buys = [t for t in (out.get("trades") or []) if t.get("side") == "buy"]
        self.assertEqual(buys, [])
        hits = out.get("constraints_hit") or {}
        self.assertGreater(int(hits.get("price_space_skips") or 0), 0)
        self.assertGreater(int(hits.get("price_space_mismatch_seen") or 0), 0)
        skips = [
            t
            for t in (out.get("sim_trades") or [])
            if t.get("status") == "skipped"
            and "price_space_mismatch" in str(t.get("reason") or "")
        ]
        self.assertTrue(skips)

    def test_engine_fills_when_price_space_gate_off(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = _minutes_aligned(stock_bars, close_delta=0.4, open_scale=0.8)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code=minutes,
                price_space_cfg={
                    "t0_price_space_gate": False,
                    "t0_price_space_max_dev_pct": 0.25,
                    "t0_price_space_prev_dev_pct": 5.0,
                },
            )
        self.assertTrue(out.get("success"), out.get("error"))
        buys = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "buy" and t.get("stock_code") == "600519"
        ]
        self.assertTrue(buys)
        daily_open = float(stock_bars["600519"][8]["open"])
        self.assertAlmostEqual(
            float(buys[0].get("price") or 0), daily_open * 0.8 + 0.4, places=3
        )
        hits = out.get("constraints_hit") or {}
        self.assertEqual(int(hits.get("price_space_skips") or 0), 0)
        self.assertGreater(int(hits.get("price_space_mismatch_seen") or 0), 0)
        self.assertFalse(out["params"].get("price_space_gate"))

    def test_0930_fill_not_used_as_open_gate(self):
        """闸比分钟首开，不比 09:30 成交价（首根收盘可走出阈外）。"""
        from core.backtest.paper_replay import replay_fill_px, replay_price_space_check

        daily = {"open": 100.0, "prev_close": 99.5}
        mins = [
            {
                "datetime": "2026-03-10 09:30:00",
                "open": 100.1,
                "close": 108.0,
                "prev_close": 99.6,
            }
        ]
        skip, seen = replay_price_space_check(
            daily, mins, {"t0_price_space_max_dev_pct": 1.0, "t0_price_space_prev_dev_pct": 1.0}
        )
        self.assertIsNone(skip)
        self.assertFalse(seen)
        fill = replay_fill_px(daily_bar=daily, minute_bars=mins, fill_clock="09:30")
        self.assertAlmostEqual(fill, 108.0, places=4)
        self.assertGreater(abs(fill / 100.0 - 1.0) * 100.0, 1.0)


class TestReplayCalendar(unittest.TestCase):
    def _iso_bars(self, n, *, start="2026-01-05"):
        from datetime import datetime, timedelta

        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        out = []
        for i in range(n):
            d = d0 + timedelta(days=i)
            close = 100.0 + i * 0.2
            out.append(
                {
                    "date": d.isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 1000,
                }
            )
        return out

    def test_short_name_does_not_cap_coverage_calendar(self):
        from core.backtest.paper_replay import _coverage_dates, _common_dates, _replay_calendar

        long = self._iso_bars(40)
        short = long[-12:]
        stock_bars = {
            "000001": long,
            "000002": list(long),
            "000003": list(long),
            "000004": list(long),
            "000005": short,
        }
        inter = _common_dates(stock_bars)
        cov = _coverage_dates(stock_bars)
        self.assertEqual(len(inter), 12)
        self.assertEqual(len(cov), 40)
        dates, start_i = _replay_calendar(
            stock_bars, lookback=30, min_history=8, max_window=10
        )
        self.assertEqual(len(dates) - start_i, 30)

    def test_engine_lookback_sets_trade_days(self):
        long = self._iso_bars(25)
        stock_bars = {"600519": long, "600036": list(long)}
        days = [b["date"] for b in long]
        rankings = {d: [_rank_row("600519", 2.0)] for d in days}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                max_window=10,
                lookback=10,
                rankings_by_date=rankings,
                cost_model="zero",
                include_session_day=False,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"].get("lookback"), 10)
        self.assertEqual(out["params"].get("trade_days"), 10)
        self.assertEqual(out["params"].get("common_dates"), 10)
        curve = out.get("equity_curve") or []
        # 起点 1 根 + 10 个交易日
        self.assertEqual(len(curve), 11)


class TestPortfolioBacktestPersistCurve(unittest.TestCase):
    def test_persist_curve_false_skips_north_star_save(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake_bt = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 1, "max_drawdown_pct": 2.0},
            "equity_curve": [{"date": "2026-01-01", "equity": 1.0}],
            "dropped_stocks": [],
        }
        bars = {
            "A": [{"date": "2026-01-01", "close": 10}] * 10,
            "B": [{"date": "2026-01-01", "close": 10}] * 10,
        }
        with patch(
            "quant.services.quant_service_replay.resolve_replay_candidates",
            return_value={
                "ok": True,
                "codes": ["A", "B"],
                "source": "explicit",
                "excluded": [],
            },
        ), patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            return_value=dict(fake_bt),
        ), patch(
            "core.backtest.paper_replay.load_replay_minute_bars",
            return_value=({}, {"ok": True, "covered": 0, "missing": []}),
        ), patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
        ) as topk_mock, patch(
            "core.north_star.save_last_backtest_curve",
        ) as save, patch(
            "core.north_star.append_ttm_event",
        ), patch(
            "core.data.facade.summarize_data_quality",
            return_value={},
        ), patch(
            "core.data.consistency.attach_source_audit",
            side_effect=lambda result, **kwargs: result,
        ):
            out = svc.run_portfolio_backtest(
                persist_curve=False,
                include_benchmark=False,
                exclude_st=False,
                universe_fit_tiers=["A", "B", "C"],
            )
            self.assertTrue(out.get("success"))
            save.assert_not_called()
            topk_mock.assert_not_called()
            out2 = svc.run_portfolio_backtest(
                persist_curve=True,
                include_benchmark=False,
                exclude_st=False,
                universe_fit_tiers=["A", "B", "C"],
            )
            self.assertTrue(out2.get("success"))
            save.assert_called()
            topk_mock.assert_not_called()


class TestFuseHitMetrics(unittest.TestCase):
    def test_sign_hit_uses_realized_ranking(self):
        pct, n, hits = fuse_hit_metrics(
            [
                {"ranking": 1.2, "realized_ranking": 0.5},
                {"ranking": 1.2, "realized_ranking": -0.5},
                {"ranking": 1.2, "realized_ranking": 0.5, "status": "skipped"},
                {"ranking": 1.2, "realized_ranking": 0.5, "status": "held"},
                {"ranking": 0.01, "realized_ranking": 1.0},
                {"ranking": -1.0, "realized_ranking": -0.2},
                {"ranking_score": 0.02, "realized_ranking": 1.0},
                {"y_fuse": 1.2, "realized_tau": 0.4, "realized_cc": -0.5},
            ]
        )
        self.assertEqual(n, 4)
        self.assertEqual(hits, 3)
        self.assertEqual(pct, 75.0)

    def test_last_day_excluded_even_if_realized(self):
        pct, n, hits = fuse_hit_metrics(
            [
                {"as_of": "2026-03-10", "ranking": 1.2, "realized_ranking": 0.5},
                {"as_of": "2026-03-11", "ranking": 1.2, "realized_ranking": -0.5},
            ],
            last_day="2026-03-11",
        )
        self.assertEqual(n, 1)
        self.assertEqual(hits, 1)
        self.assertEqual(pct, 100.0)

    def test_empty_is_none(self):
        pct, n, hits = fuse_hit_metrics(
            [{"status": "skipped", "ranking": 2.0, "realized_ranking": 1.0}]
        )
        self.assertIsNone(pct)
        self.assertEqual(n, 0)
        self.assertEqual(hits, 0)


class TestReplayPrefixYoc(unittest.TestCase):
    def _dates(self):
        return [f"2026-03-{i:02d}" for i in range(1, 16)]

    def _date_maps(self, dates, code="600519"):
        rows = {}
        for i, d in enumerate(dates):
            close = 10.0 + i * 0.1
            rows[d] = {
                "date": d,
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1000,
            }
        return {code: rows}

    def test_0930_keeps_open_y_oc(self):
        dates = self._dates()
        item = {"stock_code": "600519", "y_oo": 0.8, "y_oc": 0.197, "ranking": 0.5}
        out, n = rescore_replay_y_oc_at_clock(
            [item],
            clock="09:30",
            dates=dates,
            date_maps=self._date_maps(dates),
            minute_maps={},
            max_window=12,
            day_i=10,
            cfg={"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0},
        )
        self.assertEqual(n, 0)
        self.assertAlmostEqual(float(out[0]["y_oc"]), 0.197, places=6)
        self.assertAlmostEqual(float(out[0]["y_oo"]), 0.8, places=6)

    def test_apply_prefix_keeps_oo_updates_oc_and_ranking(self):
        from core.backtest.paper_replay import _apply_prefix_oc

        item = {
            "stock_code": "600519",
            "y_oo": 0.8,
            "predicted_score": 0.8,
            "predicted_score_eod": 0.8,
            "y_trade": 0.8,
            "y_oc": 0.197,
            "y_co": 0.0,
        }
        live = {
            "_score_source": "prefix_causal",
            "y_oc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
            "y_tau_oc": -0.70,
        }
        cfg = {"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0}
        self.assertTrue(_apply_prefix_oc(item, live, cfg))
        self.assertAlmostEqual(float(item["y_oo"]), 0.8, places=6)
        self.assertAlmostEqual(float(item["y_oc"]), -0.70, places=6)
        self.assertLess(float(item["ranking"]), 0.2)

    def test_apply_prefix_copies_aux_yhat(self):
        from core.backtest.paper_replay import _apply_prefix_oc

        item = {
            "stock_code": "600519",
            "y_oo": 0.8,
            "predicted_score": 0.8,
            "predicted_score_eod": 0.8,
            "y_trade": 0.8,
            "y_oc": 0.197,
            "y_co": 0.0,
            "y_τ30": 0.05,
            "y_hl": 0.4,
        }
        live = {
            "_score_source": "prefix_causal",
            "y_oc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
            "y_tau_oc": -0.70,
            "y_τ30": 0.2,
            "y_τ60": -0.1,
            "y_τ90": 0.3,
            "y_hl": 1.5,
        }
        cfg = {"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0}
        self.assertTrue(_apply_prefix_oc(item, live, cfg))
        self.assertNotIn("y_τ30", item)
        self.assertNotIn("y_τ60", item)
        self.assertNotIn("y_τ90", item)
        self.assertNotIn("y_τw", item)
        self.assertAlmostEqual(float(item["y_hl"]), 1.5)

    def test_935_calls_t0_rescore_with_same_prefix(self):
        dates = self._dates()
        code = "600519"
        date_maps = self._date_maps(dates, code)
        day_i = 12
        day = dates[day_i]
        prefix = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": 11.6,
                "high": 11.73,
                "low": 11.5,
                "close": 11.51,
            }
        ]
        open_item = {
            "stock_code": code,
            "y_oo": 0.5,
            "predicted_score": 0.5,
            "predicted_score_eod": 0.5,
            "y_oc": 0.197,
            "y_co": 0.0,
        }
        live = {
            "_score_source": "prefix_causal",
            "y_oc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
            "y_tau_oc": -0.70,
        }
        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=live,
        ) as rs:
            out, n = rescore_replay_y_oc_at_clock(
                [dict(open_item)],
                clock="09:35",
                dates=dates,
                date_maps=date_maps,
                minute_maps={code: {day: prefix}},
                max_window=30,
                day_i=day_i,
                cfg={"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0},
            )
        self.assertEqual(n, 1)
        self.assertAlmostEqual(float(out[0]["y_oc"]), -0.70, places=6)
        self.assertAlmostEqual(float(out[0]["y_oo"]), 0.5, places=6)
        self.assertTrue(rs.called)
        kw = rs.call_args.kwargs
        self.assertEqual(kw.get("stock_code"), code)
        self.assertEqual(len(kw.get("minute_prefix") or []), 1)
        self.assertEqual(kw["minute_prefix"][0]["datetime"], f"{day} 09:35:00")
        self.assertFalse(kw.get("include_tau_horizons"))
        self.assertAlmostEqual(float((kw.get("open_snap") or {}).get("y_oc")), 0.197, places=6)

    def test_935_matches_t0_rescore_y_oc(self):
        from core.t0.score_policy import (
            _minute_bars_until_hm,
            rescore_scores_at_fixed_prefix,
            scores_have_any,
        )

        dates = self._dates()
        code = "600519"
        date_maps = self._date_maps(dates, code)
        day_i = 12
        day = dates[day_i]
        hist = [date_maps[code][d] for d in dates[:day_i]]
        day_bar = dict(date_maps[code][day])
        day_bar["prev_close"] = hist[-1]["close"]
        prefix = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": float(day_bar["open"]),
                "high": float(day_bar["open"]) + 0.1,
                "low": float(day_bar["open"]) - 0.2,
                "close": float(day_bar["open"]) - 0.12,
            }
        ]
        open_item = {
            "stock_code": code,
            "y_oo": 0.5,
            "predicted_score": 0.5,
            "predicted_score_eod": 0.5,
            "y_oc": 0.197,
        }
        t0 = rescore_scores_at_fixed_prefix(
            stock_code=code,
            minute_prefix=prefix,
            day_bar=day_bar,
            hist_bars=hist,
            fuse_intraday=True,
            open_snap=open_item,
        )
        if not scores_have_any(t0) or str(t0.get("_score_source") or "") != "prefix_causal":
            self.skipTest("τ 模型未加载，跳过 ŷ_oc 数值对齐")
        y_t0 = t0.get("y_oc")
        if y_t0 is None:
            y_t0 = t0.get("y_tau") or t0.get("predicted_score_tau")
        if y_t0 is None:
            self.skipTest("做 T rescore 未出 ŷ_oc")
        minute_maps = {code: {day: prefix}}
        out, n = rescore_replay_y_oc_at_clock(
            [dict(open_item)],
            clock="09:35",
            dates=dates,
            date_maps=date_maps,
            minute_maps=minute_maps,
            max_window=30,
            day_i=day_i,
            cfg={"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0},
        )
        self.assertGreaterEqual(n, 1)
        self.assertAlmostEqual(float(out[0]["y_oc"]), float(y_t0), places=6)
        self.assertAlmostEqual(float(out[0]["y_oo"]), 0.5, places=6)
        cut = _minute_bars_until_hm(prefix, tau_hm="09:35")
        self.assertEqual(len(cut), 1)

    def test_injected_rankings_skip_prefix_rescore(self):
        stock_bars = {
            "600519": _bars(16, step=0.5),
            "600036": _bars(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for d in dates:
            row = _rank_row("600519", 2.0)
            row["y_oc"] = 0.197
            row["y_oo"] = 0.8
            rankings[d] = [row]
        minutes = _minutes_aligned({"600519": stock_bars["600519"]}, close_delta=0.4)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                rankings_by_date=rankings,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code=minutes,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertIsNone(out["params"].get("y_oc_prefix_clock"))
        self.assertEqual(int((out.get("constraints_hit") or {}).get("tau_prefix_rescored") or 0), 0)


class TestReplayAlpha158Align(unittest.TestCase):
    def test_required_keys_from_return_models(self):
        from core.backtest.paper_replay import _required_factor_keys_from_return_models
        from core.signal.return_score import ReturnScoreModel

        m = ReturnScoreModel(
            intercept=0.0,
            coefficients={
                "momentum": 0.1,
                "raw_alpha158_KMID": 0.02,
                "raw_alpha158_ROC5": -0.01,
            },
        )
        keys = _required_factor_keys_from_return_models({"600519": m}, None)
        self.assertIn("momentum", keys)
        self.assertIn("raw_alpha158_KMID", keys)
        self.assertIn("raw_alpha158_ROC5", keys)

    def test_score_open_day_passes_required_keys(self):
        from core.backtest.paper_replay import _score_open_day
        from tests.test_signal import _overheated_bars

        bars = _overheated_bars()
        dates = [str(b["date"]) for b in bars]
        date_maps = {"600869": {str(b["date"]): b for b in bars}}
        captured = {}

        def _fake_item(code, window, **kwargs):
            captured["required_factor_keys"] = kwargs.get("required_factor_keys")
            captured["window_n"] = len(window)
            return {
                "stock_code": code,
                "sub_scores": {"momentum": 55.0},
                "score": 55.0,
            }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item",
            side_effect=_fake_item,
        ):
            with patch(
                "core.backtest.paper_replay._attach_open_yhat_heads",
                side_effect=lambda entries, **_kw: list(entries),
            ):
                _score_open_day(
                    stock_bars={"600869": bars},
                    dates=dates,
                    date_maps=date_maps,
                    day_i=len(dates) - 1,
                    max_window=62,
                    horizon_days=1,
                    cfg=None,
                    required_factor_keys=["raw_alpha158_KMID", "momentum"],
                )
        keys = set(captured.get("required_factor_keys") or [])
        self.assertIn("raw_alpha158_KMID", keys)
        self.assertIn("momentum", keys)
        self.assertGreaterEqual(int(captured.get("window_n") or 0), 2)

    def test_backtest_bumps_window_when_model_has_alpha158(self):
        from core.signal.factors.alpha158 import ALPHA158_PANEL_WINDOW
        from core.signal.return_score import ReturnScoreModel

        model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"raw_alpha158_KMID": 0.05, "momentum": 0.1},
        )
        # 足够长的日线：lookback 核心 + Alpha158 垫窗
        n = 90
        stock_bars = {
            "600519": _bars(n, step=0.5),
            "600036": _bars(n, step=0.3),
        }
        with _offline_rebalance_patches():
            with patch(
                "core.backtest.paper_replay._load_replay_cluster_models",
                return_value={},
            ):
                with patch(
                    "core.backtest.paper_replay._load_replay_global_model",
                    return_value=model,
                ):
                    with patch(
                        "core.backtest.paper_replay._score_open_day",
                        return_value=[],
                    ) as scored:
                        out = backtest_paper_replay(
                            stock_bars,
                            top_k=2,
                            min_history=12,
                            max_window=30,
                            lookback=20,
                            cost_model="zero",
                            include_session_day=False,
                        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(int(out["params"]["max_window"]), ALPHA158_PANEL_WINDOW)
        self.assertEqual(int(out["params"]["min_history"]), ALPHA158_PANEL_WINDOW)
        self.assertIn(
            "raw_alpha158_KMID", out["params"].get("required_factor_keys") or []
        )
        self.assertTrue(scored.called)
        kw = scored.call_args.kwargs
        self.assertEqual(int(kw.get("max_window") or 0), ALPHA158_PANEL_WINDOW)
        self.assertIn("raw_alpha158_KMID", kw.get("required_factor_keys") or [])


if __name__ == "__main__":
    unittest.main()
