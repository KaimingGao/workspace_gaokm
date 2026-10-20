"""纸面交易集成测试（replay/build/open_fill/tplus1/attribution/batch）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from core.backtest.paper_replay import (
    REPLAY_AMOUNT_BASE,
    REPLAY_AMOUNT_STRONG,
    REPLAY_CASH_FLOOR,
    REPLAY_FUSE_ENTER,
    REPLAY_FUSE_STRONG,
    REPLAY_INITIAL_CASH,
    backtest_paper_replay,
    fuse_hit_metrics,
    mock_quote_from_bar,
    realized_yhat_windows,
    rescore_replay_y_τc_at_clock,
)
from core.paper import (
    ORIGIN_MANUAL,
    ORIGIN_MIXED,
    ORIGIN_STRATEGY,
    build_ops_report,
    buy_codes_direct,
    load_paper,
    manual_buy,
    mark_to_market,
    merge_origin,
    plan_buy_codes,
    save_paper,
)
from core.paper.attribution import build_paper_attribution_lite
from core.paper.replay_ctx import paper_replay_context
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
from core.research.portfolio_bars import (
    _load_bars_parallel,
    _load_offline_bars_from_store,
)
from core.store import save_daily_cache
from core.store_bars_sqlite import reset_conn_cache


# --- from test_paper_replay.py ---
def _bars__paper_replay(n: int = 25, *, step: float = 0.4, start: float = 100.0):
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
                "core.market.priors.event.get_event_prior_cfg",
                return_value={"mode": "off"},
            )
        )
        stack.enter_context(
            patch(
                "core.market.priors.stock_sentiment.get_sentiment_prior_cfg",
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
        """同票连续入选且 fuse≥0：不开卖腿（可每日加 100/200 直到现金不够）。"""
        stock_bars = {
            "600519": _bars__paper_replay(22, step=0.5),
            "600036": _bars__paper_replay(22, step=0.3),
            "300750": _bars__paper_replay(22, step=0.4),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
        realized_fuse = (nxt_o / px_tau - 1.0) * 100.0
        self.assertAlmostEqual(mid.get("y_oo_fuse_realized"), realized_fuse, places=3)
        self.assertAlmostEqual(
            mid.get("y_oo_fuse_realized"),  # 旧键双写
            realized_fuse,
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
            self.assertIsNone(last_rows[0].get("y_oo_fuse_realized"))
            self.assertIsNone(last_rows[0].get("y_oo_fuse_realized"))
        m = out.get("metrics") or {}
        self.assertEqual(out.get("params", {}).get("end_date"), last)
        self.assertGreaterEqual(int(m.get("hit_n") or 0), 1)
        last_fills = [
            t
            for t in (out.get("sim_trades") or [])
            if str(t.get("as_of") or "")[:10] == last
            and str(t.get("status") or "") not in ("skipped", "held")
        ]
        # 末日可因单票市值上限不再加仓（仅 held/skipped）；有成交腿时不得进命中分母。
        if last_fills:
            self.assertTrue(all(t.get("y_oo_fuse_realized") is None for t in last_fills))
        else:
            last_held = [
                t
                for t in (out.get("sim_trades") or [])
                if str(t.get("as_of") or "")[:10] == last
                and str(t.get("status") or "") == "held"
            ]
            self.assertTrue(last_held, "末日应至少有 held 续持行")
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
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                initial_cash=150_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(float(out["params"]["initial_cash"]), 150_000.0)

    def test_custom_lots(self):
        from core.backtest.paper_replay import (
            clamp_replay_amount,
            clamp_replay_amount_pair,
        )

        self.assertEqual(clamp_replay_amount(150), 1000)
        self.assertEqual(clamp_replay_amount(250), 1000)
        self.assertEqual(clamp_replay_amount_pair(300, 200), (1000, 1000))
        self.assertEqual(clamp_replay_amount_pair(30000, 20000), (30000, 30000))
        stock_bars = {
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(20, step=0.5),
            "600036": _bars__paper_replay(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {
            d: [_rank_row("600519", 2.0), _rank_row("600036", 1.5)] for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=2,
                min_history=8,
                scores_by_date=scores,
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
        """fuse<0 才清仓；买入当日 T+1 不可卖，次日可卖。"""
        stock_bars = {
            "600519": _bars__paper_replay(20, step=0.5),
            "600036": _bars__paper_replay(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            if i <= 8:
                scores[d] = [_rank_row("600519", 2.0)]
            else:
                scores[d] = [_rank_row("600519", -1.0)]

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertGreaterEqual(len(sells), 1, "fuse<0 应卖出")
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
        """掉出 Top-K 但 fuse>入场：不卖。"""
        stock_bars = {
            "600519": _bars__paper_replay(20, step=0.5),
            "600036": _bars__paper_replay(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            if i % 2 == 0:
                scores[d] = [
                    _rank_row("600519", 2.0),
                    _rank_row("600036", 1.5),
                ]
            else:
                scores[d] = [
                    _rank_row("600036", 2.0),
                    _rank_row("600519", 1.5),
                ]

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertEqual(sells, [], "未进 Top-K 且 fuse>入场不应卖出")

    def test_fail_enter_exits_held(self):
        """开仓后 fuse 未过入场：整笔清仓。"""
        stock_bars = {
            "600519": _bars__paper_replay(18, step=0.5),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else 0.02
            scores[d] = [_rank_row("600519", y)]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
        """当日打不上分：清仓，不假装 fuse≥0 续持。"""
        stock_bars = {
            "600519": _bars__paper_replay(20, step=0.5),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            if i <= 10:
                scores[d] = [_rank_row("600519", 2.0)]
            else:
                scores[d] = []

        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertGreaterEqual(len(sells), 1, "缺 fuse 应卖出")
        sim = [t for t in (out.get("sim_trades") or []) if t.get("side") == "sell"]
        self.assertTrue(
            any("缺失" in str(t.get("reason") or "") for t in sim + sells),
            f"expected fuse-missing exit, sells={sells[:2]!r} sim={sim[:2]!r}",
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
            "600519": _bars__paper_replay(22, step=0.5),
            "600036": _bars__paper_replay(22, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {
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
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(22, step=0.5),
            "600036": _bars__paper_replay(22, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {
            d: [_rank_row("600519", 2.0), _rank_row("600036", 1.5)] for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=2,
                min_history=8,
                scores_by_date=scores,
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
        self.assertAlmostEqual(float(out["params"]["fuse_enter"]), REPLAY_FUSE_ENTER)
        self.assertAlmostEqual(float(out["params"]["fuse_strong"]), REPLAY_FUSE_STRONG)
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
        """开仓后 fuse 未过入场：成交明细应有清仓腿。"""
        stock_bars = {
            "600519": _bars__paper_replay(18, step=0.5),
            "600036": _bars__paper_replay(18, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 9 else 0.02
            scores[d] = [_rank_row("600519", y)]
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
        stock_bars = {"600519": _bars__paper_replay(18, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else -0.8
            scores[d] = [
                {
                    **_rank_row("600519", y),
                    "y_oo": y,
                    "y_oc": y,
                    "y_oo_fuse": y,
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
                scores_by_date=scores,
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
                    "y_oo_fuse": 1.2,
                    "y_oo_fuse_score": 0.012,
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
                "y_oo_fuse": 0.8,
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
                "y_oo_fuse": 0.9,
                "y_oo_fuse_score": 0.009,
                "reason": "fuse≥0% 持有",
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
                        "reason": "fuse=-0.500%<0% 清仓",
                        "y_oo": -0.5,
                        "y_oo_fuse": -0.5,
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
            "y_oo_fuse": 1.2,
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
        self.assertNotIn("y_hl", rows[0])
        self.assertEqual(rows[0]["formula_terms_tau"]["total"], 0.8)
        self.assertEqual(rows[0]["formula_terms_path"]["total"], 1.5)

        sk = _skip_to_sim({**src, "reason": "现金不足"}, "2026-09-10")
        self.assertNotIn("y_τ60", sk)
        self.assertNotIn("y_τw", sk)
        self.assertNotIn("y_hl", sk)
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
        self.assertNotIn("y_hl", hold)
        self.assertEqual(hold["formula_terms_path"]["total"], 1.5)

    def test_sim_trades_keep_y_co_from_ranking(self):
        stock_bars = {"600519": _bars__paper_replay(16, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {
            d: [{**_rank_row("600519", 2.0), "y_co": 0.35, "y_on": 0.35}]
            for d in dates
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(20, step=0.5),
            "600036": _bars__paper_replay(20, step=0.35),
            "300750": _bars__paper_replay(20, step=0.4),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {
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
                scores_by_date=scores,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"]["top_k"], 3)
        self.assertIn("观察池 3 只", out.get("note") or "")

class TestTopkEngineTag(unittest.TestCase):
    def test_topk_params_engine(self):
        from core.backtest.legacy.topk_backtest import backtest_topk_equal_weight
        from tests.conftest import _aligned_bars

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
                allow_heuristic_baseline=True,
                rank_mode="heuristic_score",
            )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertEqual(result.get("params", {}).get("engine"), "topk_research")
        self.assertIn("paper_replay", str(result.get("note") or ""))

class TestOpenDayYhat(unittest.TestCase):
    def test_drop_unscored_heads_keeps_features(self):
        from core.signal.cross_section_rescore import drop_unscored_heads

        items = [
            {"stock_code": "A1", "y_oo": 1.0, "features_tau": {"gap": 1}},
            {
                "stock_code": "B1",
                "y_oo": 2.0,
                "y_oo_fuse": 0.3,
                "y_oo_rank": 4,
                "formula_terms_tau": {"total": 1},
                "features_tau": {"gap": 9},
                "sub_scores": {"mom3": 1},
            },
        ]
        drop_unscored_heads(items, ["A1"])
        self.assertEqual(items[0]["y_oo"], 1.0)
        self.assertNotIn("y_oo", items[1])
        self.assertNotIn("y_oo_fuse", items[1])
        self.assertNotIn("y_oo_rank", items[1])
        self.assertNotIn("formula_terms_tau", items[1])
        self.assertEqual(items[1]["features_tau"]["gap"], 9)
        self.assertEqual(items[1]["sub_scores"]["mom3"], 1)

    def test_attach_cluster_pred_as_y_trade(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads
        from core.signal.return_score import ReturnScoreModel

        model = ReturnScoreModel(
            intercept=0.8,
            coefficients={"mom3": 0.1},
            feature_zscore=False,
        )
        entries = [
            {"stock_code": "600519", "sub_scores": {"mom3": 2.0}, "score": 55}
        ]
        out = _attach_open_yhat_heads(
            entries,
            quotes={},
            windows={},
            global_model=model,
            tau_model_doc={},
        )
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out[0]["predicted_score"]), 1.0)
        self.assertAlmostEqual(float(out[0]["y_oo"]), 1.0, places=3)
        self.assertNotIn("y_trade", out[0])
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
        self.assertAlmostEqual(float(out[0]["y_oo"]), 1.2, places=3)
        self.assertNotIn("y_trade", out[0])
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
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
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

    def test_attach_open_heads_copies_tau_cs_onto_co(self):
        from core.backtest.paper_replay import _attach_open_yhat_heads

        entries = [{"stock_code": "600519", "predicted_score": 0.2}]
        quote = {
            "open": 10.2,
            "open_raw": 10.2,
            "prev_close": 10.0,
            "change_raw": 2.0,
            "date": "2026-03-10",
            "trade_date": "2026-03-10",
        }
        window = []
        px = 10.0
        for i, day in enumerate(("2026-03-05", "2026-03-06", "2026-03-09")):
            window.append(
                {
                    "date": day,
                    "open": px,
                    "high": px + 0.2,
                    "low": px - 0.1,
                    "close": px + 0.05 * i,
                    "volume": 1000 + i,
                }
            )
        pool = {
            "pool_gaps": [2.0, 0.5, -0.2],
            "sector_gap_breadth": 0.33,
            "ref_by_code": {"600519": 0.5},
        }
        out = _attach_open_yhat_heads(
            entries,
            quotes={"600519": quote},
            windows={"600519": window},
            tau_pool_day=pool,
        )
        tau = out[0].get("features_tau") or {}
        co = out[0].get("features_co") or {}
        for key in ("sector_gap_breadth", "theme_day", "gap_vs_sector"):
            self.assertIsNotNone(tau.get(key), key)
            self.assertAlmostEqual(float(co.get(key)), float(tau.get(key)), places=6, msg=key)

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
        from tests.conftest import _overheated_bars

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
        bars = _bars__paper_replay(6, step=0.5, start=100.0)
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
        bars = _bars__paper_replay(4, step=0.4, start=100.0)
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

        row = {
            "y_τc": 5.25,
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
        self.assertNotIn("r_hat", row)

    def test_stamp_r_tau_hold_ignores_close_mark(self):
        from core.backtest.paper_replay import stamp_r_tau_on_row

        row = {
            "y_τc": 5.25,
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
        bars = _bars__paper_replay(5, step=0.5, start=100.0)
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

    def test_replay_does_not_stamp_y_hl_realized(self):
        stock_bars = {
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
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
                scores_by_date=scores,
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
        self.assertIsNone(mid.get("y_hl_realized"))
        last_rows = [
            t for t in filled if str(t.get("as_of") or "")[:10] == last
        ]
        if last_rows:
            self.assertIsNone(last_rows[0].get("realized_on"))
            self.assertIsNone(last_rows[0].get("realized_oo"))
            self.assertIsNone(last_rows[0].get("y_oo_fuse_realized"))
            self.assertIsNone(last_rows[0].get("y_hl_realized"))
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

        stock = {"600000": _bars__paper_replay(10), "600001": _bars__paper_replay(10, step=0.2)}
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
        scores = {
            d: [_rank_row("600519", 2.0)] for d in days + ["2026-09-08"]
        }
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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

    def test_missing_bar_carries_prev_close_flat_mark(self):
        """停牌缺当日 K：沿用昨收，盈亏 0，腿仍进分票。"""
        from core.backtest.paper_replay import day_stock_legs

        date_maps = {
            "000668": {
                "2026-09-30": {"open": 16.95, "close": 18.69},
                # 2026-10-08 无 K
            }
        }
        held = {
            "000668": {
                "stock_code": "000668",
                "stock_name": "荣丰控股",
                "shares": 1100.0,
                "mark_px": 18.69,
            }
        }
        legs, more = day_stock_legs(
            start=held,
            end=held,
            date_maps=date_maps,
            prev_day="2026-09-30",
            day="2026-10-08",
            open_px={},
            prev_equity=203_301.15,
        )
        self.assertEqual(more, 0)
        self.assertEqual(len(legs), 1)
        self.assertEqual(legs[0]["stock_code"], "000668")
        self.assertTrue(legs[0].get("stale_mark"))
        self.assertAlmostEqual(legs[0]["pnl"], 0.0, places=2)
        self.assertAlmostEqual(legs[0]["overnight_pnl"], 0.0, places=2)
        self.assertAlmostEqual(legs[0]["intraday_pnl"], 0.0, places=2)
        self.assertAlmostEqual(legs[0]["close"], 18.69, places=4)
        self.assertAlmostEqual(legs[0]["contrib_pct"], 0.0, places=4)

    def test_resolve_mark_px_walks_back_and_uses_last_price(self):
        from core.backtest.paper_replay import _resolve_mark_px

        date_maps = {
            "000668": {
                "2026-09-30": {"open": 16.95, "close": 18.69},
            }
        }
        dates = ["2026-09-30", "2026-10-08", "2026-10-09"]
        px = _resolve_mark_px(
            code="000668",
            day="2026-10-08",
            date_maps=date_maps,
            dates=dates,
            day_i=1,
            holding={},
        )
        self.assertAlmostEqual(px, 18.69, places=4)
        px2 = _resolve_mark_px(
            code="000668",
            day="2026-10-09",
            date_maps={"000668": {}},
            dates=dates,
            day_i=2,
            holding={"last_price": 18.69},
        )
        self.assertAlmostEqual(px2, 18.69, places=4)

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

class TestHaltMarkToMarket(unittest.TestCase):
    def test_halted_holding_not_zeroed_in_equity(self):
        """买入后停牌缺 K：净值不得把该票记 0；分票仍保留持仓。"""
        halt = "000668"
        liquid = [f"60000{i}" for i in range(5)]
        n = 22
        stock_bars = {c: _bars__paper_replay(n, step=0.2 + 0.05 * i) for i, c in enumerate(liquid)}
        # 停牌票：前 14 根有 K，之后缺行情（日历靠其余票多数覆盖）
        stock_bars[halt] = _bars__paper_replay(14, step=0.3, start=16.0)
        dates = [b["date"] for b in stock_bars[liquid[0]]]
        halt_last = stock_bars[halt][-1]["date"]
        halt_last_close = float(stock_bars[halt][-1]["close"])
        scores = {}
        for d in dates:
            if d <= halt_last:
                # 过入场买停牌票
                scores[d] = [_rank_row(halt, 2.0)]
            else:
                # 未过入场想清仓，但无报价卖不出
                scores[d] = [_rank_row(halt, -1.0)]

        with _offline_rebalance_patches(), patch(
            "core.backtest.paper_replay._watching_name_map",
            return_value={halt: "荣丰控股"},
        ):
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                initial_cash=200_000.0,
                include_session_day=False,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        held_end = {
            str(h.get("stock_code"))
            for h in (out.get("holdings_end") or [])
            if float(h.get("shares") or 0) > 0
        }
        self.assertIn(halt, held_end)
        stuck = [
            s
            for s in (out.get("sim_trades") or [])
            if s.get("stock_code") == halt
            and "无有效报价未卖出" in str(s.get("reason") or "")
        ]
        self.assertTrue(stuck, "停牌后应出现无有效报价未卖出")
        # 缺 K 日净值 = 现金 + 股数×最近收盘，不得只剩现金
        by_date = {
            str(p.get("date"))[:10]: p
            for p in (out.get("equity_curve") or [])
            if isinstance(p, dict)
        }
        halt_days = [d for d in dates if d > halt_last and d in by_date]
        self.assertTrue(halt_days, "覆盖率日历应继续走到停牌之后")
        h_end = next(
            h
            for h in (out.get("holdings_end") or [])
            if str(h.get("stock_code")) == halt
        )
        shares = float(h_end.get("shares") or 0)
        self.assertGreater(shares, 0)
        for d in halt_days:
            row = by_date[d]
            cash = float(row.get("cash") or 0)
            eq = float(row.get("equity") or 0)
            self.assertGreater(eq, cash + 1.0, f"{d}: equity should include halt mark")
            self.assertAlmostEqual(
                eq,
                cash + shares * halt_last_close,
                places=2,
                msg=f"{d}: mark should carry last close {halt_last_close}",
            )
            legs = row.get("legs") or []
            halt_leg = next((x for x in legs if x.get("stock_code") == halt), None)
            self.assertIsNotNone(halt_leg, f"{d}: legs must include halted name")
            self.assertTrue(halt_leg.get("stale_mark"))
            self.assertAlmostEqual(float(halt_leg.get("pnl") or 0), 0.0, places=2)
        contrib = next(
            (r for r in (out.get("stock_contrib") or []) if r.get("stock_code") == halt),
            None,
        )
        self.assertIsNotNone(contrib)
        self.assertGreaterEqual(int(contrib.get("hold_days") or 0), len(halt_days))

class TestReplayMinuteFill(unittest.TestCase):
    def test_clamp_fill_clock(self):
        from core.backtest.paper_replay import (
            LIVE_FILL_CLOCKS,
            REPLAY_FILL_CLOCKS,
            clamp_live_fill_clock,
            clamp_replay_fill_clock,
        )

        self.assertEqual(clamp_replay_fill_clock("9:35"), "09:35")
        self.assertEqual(clamp_replay_fill_clock("0935"), "09:35")
        self.assertEqual(clamp_replay_fill_clock("10:00"), "10:00")
        self.assertEqual(clamp_replay_fill_clock("11:00"), "09:30")
        self.assertEqual(clamp_replay_fill_clock(None), "09:30")
        self.assertEqual(clamp_replay_fill_clock("09:25"), "09:30")  # 回测不含 09:25
        self.assertEqual(len(REPLAY_FILL_CLOCKS), 7)
        self.assertEqual(clamp_live_fill_clock("09:25"), "09:25")
        self.assertEqual(clamp_live_fill_clock("9:40"), "09:40")
        self.assertEqual(clamp_live_fill_clock("11:00"), "09:30")
        self.assertEqual(LIVE_FILL_CLOCKS[0], "09:25")
        self.assertEqual(len(LIVE_FILL_CLOCKS), 8)

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
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = _minutes_aligned(stock_bars, close_delta=0.4)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
        # 成交明细 y_τc 真实值随 fill_clock，不得冻在 close/open。
        self.assertAlmostEqual(
            float(sim.get("realized_tau")),
            (close_t / fill - 1.0) * 100.0,
            places=3,
        )
        daily_open_buy = float(
            next(b for b in stock_bars["600519"] if b["date"] == buy_day)["open"]
        )
        self.assertNotAlmostEqual(fill, daily_open_buy, places=2)
        self.assertNotAlmostEqual(
            float(sim.get("realized_tau")),
            (close_t / daily_open_buy - 1.0) * 100.0,
            places=2,
        )

    def test_935_skips_when_minute_missing(self):
        stock_bars = {
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code={},
            )
        self.assertTrue(out.get("success"), out.get("error"))
        buys = [t for t in (out.get("trades") or []) if t.get("side") == "buy"]
        self.assertEqual(buys, [])
        self.assertGreater(int((out.get("constraints_hit") or {}).get("minute_skips") or 0), 0)

    def test_exit_falls_back_when_only_afternoon_minutes(self):
        stock_bars = {"600519": _bars__paper_replay(16, step=0.5)}
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for i, d in enumerate(dates):
            y = 2.0 if i <= 10 else -0.8
            scores[d] = [_rank_row("600519", y)]
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
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
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
                scores_by_date=scores,
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
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {d: [_rank_row("600519", 2.0)] for d in dates}
        minutes = _minutes_aligned(stock_bars, close_delta=0.4, open_scale=0.8)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
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
        from core.backtest.paper_replay import _common_dates, _coverage_dates, _replay_calendar

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
        scores = {d: [_rank_row("600519", 2.0)] for d in days}
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                max_window=10,
                lookback=10,
                scores_by_date=scores,
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
    def test_fill_clock_0930_skips_minute_load(self):
        """09:30 用日开盘，不读整池 5m。"""
        from quant.services.quant_service import QuantService

        svc = QuantService()
        fake_bt = {
            "success": True,
            "metrics": {"total_return_pct": 1.0, "trade_count": 0},
            "equity_curve": [{"date": "2026-01-01", "equity": 1.0}],
            "dropped_stocks": [],
        }
        bars = {
            "A": [{"date": "2026-01-01", "open": 10, "close": 10}] * 12,
            "B": [{"date": "2026-01-01", "open": 10, "close": 10}] * 12,
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
        ) as bt, patch(
            "core.backtest.paper_replay.load_replay_minute_bars",
            return_value=({"A": {"2026-01-01": []}}, {"ok": True, "covered": 1, "missing": []}),
        ) as load_m, patch(
            "core.backtest.legacy.topk_backtest.backtest_topk_equal_weight",
        ), patch(
            "core.north_star.save_last_backtest_curve",
        ), patch(
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
                fill_clock="09:30",
            )
            self.assertTrue(out.get("success"), out.get("error"))
            load_m.assert_not_called()
            kwargs = bt.call_args.kwargs
            self.assertEqual(kwargs.get("minute_bars_by_code"), {})
            self.assertEqual(kwargs.get("fill_clock"), "09:30")

            out2 = svc.run_portfolio_backtest(
                persist_curve=False,
                include_benchmark=False,
                exclude_st=False,
                fill_clock="09:35",
            )
            self.assertTrue(out2.get("success"), out2.get("error"))
            load_m.assert_called()

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
            "core.backtest.legacy.topk_backtest.backtest_topk_equal_weight",
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
            )
            self.assertTrue(out.get("success"))
            save.assert_not_called()
            topk_mock.assert_not_called()
            out2 = svc.run_portfolio_backtest(
                persist_curve=True,
                include_benchmark=False,
                exclude_st=False,
            )
            self.assertTrue(out2.get("success"))
            save.assert_called()
            topk_mock.assert_not_called()

class TestFuseHitMetrics(unittest.TestCase):
    def test_sign_hit_uses_y_oo_fuse_primary(self):
        pct, n, hits = fuse_hit_metrics(
            [
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": 0.5},
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": -0.5},
                {"y_oo_fuse": -1.0, "y_oo_fuse_realized": -0.2},
            ]
        )
        self.assertEqual(n, 3)
        self.assertEqual(hits, 2)
        self.assertEqual(pct, round(2 / 3 * 100.0, 1))

    def test_sign_hit_ignores_old_fuse_aliases(self):
        """旧键 ranking / y_fuse / realized_ranking 不再计入命中。"""
        pct, n, hits = fuse_hit_metrics(
            [
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": 0.5},
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": -0.5},
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": 0.5, "status": "skipped"},
                {"y_oo_fuse": 1.2, "y_oo_fuse_realized": 0.5, "status": "held"},
                {"y_oo_fuse": 0.01, "y_oo_fuse_realized": 1.0},  # |yf|<eps 跳过
                {"y_oo_fuse": -1.0, "y_oo_fuse_realized": -0.2},
                {"y_oo_fuse_score": 0.02, "y_oo_fuse_realized": 1.0},
                {"ranking": 1.2, "y_fuse": 1.2, "y_oo_fuse_realized": 0.5},
            ]
        )
        self.assertEqual(n, 4)
        self.assertEqual(hits, 3)
        self.assertEqual(pct, 75.0)

    def test_last_day_excluded_even_if_realized(self):
        pct, n, hits = fuse_hit_metrics(
            [
                {"as_of": "2026-03-10", "y_oo_fuse": 1.2, "y_oo_fuse_realized": 0.5},
                {"as_of": "2026-03-11", "y_oo_fuse": 1.2, "y_oo_fuse_realized": -0.5},
            ],
            last_day="2026-03-11",
        )
        self.assertEqual(n, 1)
        self.assertEqual(hits, 1)
        self.assertEqual(pct, 100.0)

    def test_empty_is_none(self):
        pct, n, hits = fuse_hit_metrics(
            [{"status": "skipped", "y_oo_fuse": 2.0, "y_oo_fuse_realized": 1.0}]
        )
        self.assertIsNone(pct)
        self.assertEqual(n, 0)
        self.assertEqual(hits, 0)

class TestOpenTcRankBackfill(unittest.TestCase):
    """09:30 缓存补挂：可交易集全缺才补；部分有名次不动。"""

    def _ridge_doc(self):
        return {
            "return_model": {
                "success": True,
                "backend": "ridge",
                "solver": "ridge",
                "target": "tc_rank",
                "horizon_mode": "tc_rank",
                "intercept": 0.0,
                "coefficients": {"z_τc_gap_pct": 1.0},
                "feature_zscore": True,
                "zscore_scope": "cross_section",
                "zscore_universe": "pool",
                "zscore_means": {},
                "zscore_stds": {},
            }
        }

    def test_backfill_when_tradable_all_missing(self):
        from core.backtest.replay_scoring import maybe_backfill_open_tc_rank

        items = [
            {"stock_code": "A1", "features_tau": {"z_τc_gap_pct": 0.0}},
            {"stock_code": "A2", "features_tau": {"z_τc_gap_pct": 2.0}},
            {"stock_code": "B1", "features_tau": {"z_τc_gap_pct": 9.0}},
        ]
        out = maybe_backfill_open_tc_rank(
            items, tc_rank_doc=self._ridge_doc(), score_codes=["A1", "A2"]
        )
        by = {it["stock_code"]: it for it in out}
        self.assertIsNotNone(by["A1"].get("y_tc_rank"))
        self.assertIsNotNone(by["A2"].get("y_tc_rank"))
        self.assertNotIn("y_tc_rank", by["B1"])

    def test_no_backfill_when_any_tradable_has_rank(self):
        from core.backtest.replay_scoring import maybe_backfill_open_tc_rank

        items = [
            {
                "stock_code": "A1",
                "features_tau": {"z_τc_gap_pct": 0.0},
                "y_tc_rank": 1,
            },
            {"stock_code": "A2", "features_tau": {"z_τc_gap_pct": 2.0}},
        ]
        out = maybe_backfill_open_tc_rank(
            items, tc_rank_doc=self._ridge_doc(), score_codes=["A1", "A2"]
        )
        by = {it["stock_code"]: it for it in out}
        self.assertEqual(by["A1"]["y_tc_rank"], 1)
        self.assertNotIn("y_tc_rank", by["A2"])

    def test_refresh_prefix_passes_zscore_codes(self):
        """晚钟重挂 τc_rank 须带 zscore_codes=可交易（避混合钟）。"""
        from unittest.mock import patch

        from core.backtest.replay_scoring import refresh_tau_heads_after_prefix

        dates = [f"2026-03-{i:02d}" for i in range(1, 16)]
        items = [
            {"stock_code": "A1", "y_oo": 0.1, "features_tau": {"z_τc_gap_pct": 1.0}},
            {"stock_code": "B1", "y_oo": 0.2, "features_tau": {"z_τc_gap_pct": 100.0}},
        ]
        with patch(
            "core.backtest.replay_scoring.rescore_replay_y_τc_at_clock",
            return_value=(items, 1),
        ), patch(
            "core.research.tc_rank.apply_tc_rank_scores"
        ) as apply_fn:
            refresh_tau_heads_after_prefix(
                items,
                clock="09:35",
                dates=dates,
                date_maps={},
                minute_maps={},
                max_window=12,
                day_i=10,
                score_codes=["A1"],
                tc_rank_doc={"return_model": {}},
            )
        self.assertTrue(apply_fn.called)
        kw = apply_fn.call_args.kwargs
        self.assertEqual(list(kw.get("score_codes") or []), ["A1"])
        self.assertEqual(list(kw.get("zscore_codes") or []), ["A1"])

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
        item = {"stock_code": "600519", "y_oo": 0.8, "y_oc": 0.197, "y_oo_fuse": 0.5}
        out, n = rescore_replay_y_τc_at_clock(
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

    def test_apply_prefix_keeps_oo_updates_oc_and_fuse(self):
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
            "y_τc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
        }
        cfg = {"fusion_w_oo": 0.5, "fusion_w_oc": 0.5, "fusion_w_co": 0.0}
        self.assertTrue(_apply_prefix_oc(item, live, cfg))
        self.assertAlmostEqual(float(item["y_oo"]), 0.8, places=6)
        self.assertAlmostEqual(float(item["y_τc"]), -0.70, places=6)
        self.assertLess(float(item["y_oo_fuse"]), 0.2)

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
            "y_τc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
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
        self.assertAlmostEqual(float(item["y_hl"]), 0.4)

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
            "y_τc": -0.70,
            "y_tau": -0.70,
            "predicted_score_tau": -0.70,
        }
        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=live,
        ) as rs:
            out, n = rescore_replay_y_τc_at_clock(
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
        self.assertAlmostEqual(float(out[0]["y_τc"]), -0.70, places=6)
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
        y_t0 = t0.get("y_τc")
        if y_t0 is None:
            y_t0 = t0.get("y_oc") or t0.get("y_tau") or t0.get("predicted_score_tau")
        if y_t0 is None:
            self.skipTest("做 T rescore 未出 ŷ_oc")
        minute_maps = {code: {day: prefix}}
        out, n = rescore_replay_y_τc_at_clock(
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
        self.assertAlmostEqual(float(out[0]["y_τc"]), float(y_t0), places=6)
        self.assertAlmostEqual(float(out[0]["y_oo"]), 0.5, places=6)
        cut = _minute_bars_until_hm(prefix, tau_hm="09:35")
        self.assertEqual(len(cut), 1)

    def test_injected_scores_skip_prefix_rescore(self):
        stock_bars = {
            "600519": _bars__paper_replay(16, step=0.5),
            "600036": _bars__paper_replay(16, step=0.3),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        scores = {}
        for d in dates:
            row = _rank_row("600519", 2.0)
            row["y_oc"] = 0.197
            row["y_oo"] = 0.8
            scores[d] = [row]
        minutes = _minutes_aligned({"600519": stock_bars["600519"]}, close_delta=0.4)
        with _offline_rebalance_patches():
            out = backtest_paper_replay(
                stock_bars,
                top_k=1,
                min_history=8,
                scores_by_date=scores,
                cost_model="zero",
                fill_clock="09:35",
                minute_bars_by_code=minutes,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertNotIn("y_oc_prefix_clock", out["params"])
        self.assertEqual(int((out.get("constraints_hit") or {}).get("tau_prefix_rescored") or 0), 0)
        self.assertEqual(int((out.get("constraints_hit") or {}).get("tau_prefix_miss") or 0), 0)

class TestReplayalpha_ooAlign(unittest.TestCase):
    def test_required_keys_from_return_models(self):
        from core.backtest.paper_replay import _required_factor_keys_from_return_models
        from core.signal.return_score import ReturnScoreModel

        m = ReturnScoreModel(
            intercept=0.0,
            coefficients={
                "momentum": 0.1,
                "raw_aoo_YKMID": 0.02,
                "raw_aoo_OROC5": -0.01,
            },
        )
        # 模型 β 可要求全量 raw_aoo_*（含滚动列）；打分补算不截断
        keys = _required_factor_keys_from_return_models(m)
        self.assertIn("momentum", keys)
        self.assertIn("raw_aoo_YKMID", keys)
        self.assertIn("raw_aoo_OROC5", keys)

    def test_score_open_day_passes_required_keys(self):
        from core.backtest.paper_replay import _score_open_day
        from tests.conftest import _overheated_bars

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
        ), patch(
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
                required_factor_keys=["raw_aoo_YKMID", "momentum"],
            )
        keys = set(captured.get("required_factor_keys") or [])
        self.assertIn("raw_aoo_YKMID", keys)
        self.assertIn("momentum", keys)
        self.assertGreaterEqual(int(captured.get("window_n") or 0), 2)

    def test_backtest_bumps_window_when_model_has_alpha_oo(self):
        from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW
        from core.signal.return_score import ReturnScoreModel

        model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"raw_aoo_YKMID": 0.05, "momentum": 0.1},
        )
        # 足够长的日线：lookback 核心 + alpha_oo 垫窗
        n = 90
        stock_bars = {
            "600519": _bars__paper_replay(n, step=0.5),
            "600036": _bars__paper_replay(n, step=0.3),
        }
        with _offline_rebalance_patches(), patch(
            "core.backtest.paper_replay._load_replay_global_model",
            return_value=model,
        ), patch(
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
        self.assertEqual(int(out["params"]["max_window"]), ALPHA_OO_PANEL_WINDOW)
        self.assertEqual(int(out["params"]["min_history"]), ALPHA_OO_PANEL_WINDOW)
        self.assertIn(
            "raw_aoo_YKMID", out["params"].get("required_factor_keys") or []
        )
        self.assertTrue(scored.called)
        kw = scored.call_args.kwargs
        self.assertEqual(int(kw.get("max_window") or 0), ALPHA_OO_PANEL_WINDOW)
        self.assertIn("raw_aoo_YKMID", kw.get("required_factor_keys") or [])


# --- from test_paper_build.py ---
PRICES = {"600519": 10.0, "000568": 20.0, "601318": 50.0}

def _fake_query(code):
    code = str(code)
    price = PRICES.get(code)
    if price is None:
        return {"success": False, "error": f"未知代码 {code}"}
    return {
        "success": True,
        "stock_code": code,
        "stock_name": f"名{code}",
        "price_raw": price,
    }

def _paper(cash=100000.0, holdings=None):
    return {
        "name": "test",
        "cash": cash,
        "initial_cash": cash,
        "holdings": holdings or [],
        "trades": [],
    }

class _QuotePatchMixin:
    """plan_buy_codes / 盯市走 facade.get_quote，不能只补 StockAPI.query。"""

    def setUp(self):
        p = patch("core.data.facade.get_quote", side_effect=_fake_query)
        p.start()
        self.addCleanup(p.stop)

class TestPlanBuyCodes(_QuotePatchMixin, unittest.TestCase):
    def test_plan_reports_shares_cost_and_remaining_cash(self):
        paper = _paper()
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, ["600519", "000568"], lot_shares=200)
        self.assertEqual(plan["lot_shares"], 200)
        self.assertEqual(plan["buy_count"], 2)
        self.assertEqual(plan["total_amount"], 200 * 10.0 + 200 * 20.0)
        self.assertEqual(plan["cash"], 100000.0)
        self.assertEqual(plan["cash_after"], 100000.0 - plan["total_amount"])
        for item in plan["items"]:
            self.assertEqual(item["shares"], 200)
            self.assertEqual(item["amount"], item["shares"] * item["price"])

    def test_plan_does_not_touch_account(self):
        paper = _paper()
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan_buy_codes(paper, ["600519"], lot_shares=200)
        self.assertEqual(paper["cash"], 100000.0)
        self.assertEqual(paper["holdings"], [])
        self.assertEqual(paper["trades"], [])

    def test_plan_skips_held_and_unaffordable(self):
        paper = _paper(cash=2500.0, holdings=[{"stock_code": "600519", "shares": 100}])
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, ["600519", "000568", "601318"], lot_shares=100)
        bought = [i["stock_code"] for i in plan["items"]]
        reasons = {s["stock_code"]: s["reason"] for s in plan["skipped"]}
        # 便宜的先买：000568 花 2000 后余 500，601318 买不起
        self.assertEqual(bought, ["000568"])
        self.assertEqual(reasons["600519"], "已持仓")
        self.assertIn("现金不足", reasons["601318"])

    def test_plan_rejects_non_lot_shares(self):
        paper = _paper()
        with self.assertRaises(ValueError):
            plan_buy_codes(paper, ["600519"], lot_shares=50)

    def test_execution_matches_preview(self):
        paper = _paper(cash=5000.0)
        codes = ["600519", "000568", "601318"]
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, codes, lot_shares=100)
            result = buy_codes_direct(paper, codes, lot_shares=100)
        self.assertEqual(
            [(i["stock_code"], i["shares"], i["amount"]) for i in plan["items"]],
            [(t["stock_code"], t["shares"], t["amount"]) for t in result["trades"]],
        )
        self.assertEqual(paper["cash"], plan["cash_after"])

class TestHoldingOrigin(_QuotePatchMixin, unittest.TestCase):
    def test_merge_origin(self):
        self.assertEqual(merge_origin(None, ORIGIN_MANUAL), ORIGIN_MANUAL)
        self.assertEqual(merge_origin("", ORIGIN_STRATEGY), ORIGIN_STRATEGY)
        self.assertEqual(merge_origin(ORIGIN_MANUAL, ORIGIN_MANUAL), ORIGIN_MANUAL)
        self.assertEqual(merge_origin(ORIGIN_MANUAL, ORIGIN_STRATEGY), ORIGIN_MIXED)
        self.assertEqual(merge_origin(ORIGIN_STRATEGY, ORIGIN_MANUAL), ORIGIN_MIXED)

    def test_buy_paths_mark_manual(self):
        paper = _paper()
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            buy_codes_direct(paper, ["600519"], lot_shares=100)
            manual_buy(paper, "000568", shares=100)
        by_code = {h["stock_code"]: h for h in paper["holdings"]}
        self.assertEqual(by_code["600519"]["origin"], ORIGIN_MANUAL)
        self.assertEqual(by_code["000568"]["origin"], ORIGIN_MANUAL)
        for trade in paper["trades"]:
            self.assertEqual(trade["origin"], ORIGIN_MANUAL)

    def test_manual_add_on_strategy_position_becomes_mixed(self):
        paper = _paper(
            holdings=[
                {
                    "stock_code": "600519",
                    "stock_name": "名600519",
                    "shares": 100,
                    "cost": 9.0,
                    "origin": ORIGIN_STRATEGY,
                }
            ]
        )
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            manual_buy(paper, "600519", shares=100)
        self.assertEqual(paper["holdings"][0]["origin"], ORIGIN_MIXED)

    def test_mark_to_market_exposes_origin_label(self):
        paper = _paper(
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0, "origin": ORIGIN_STRATEGY},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ]
        )
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            summary = mark_to_market(paper)
        by_code = {h["stock_code"]: h for h in summary["holdings"]}
        self.assertEqual(by_code["600519"]["origin_label"], "策略")
        # 早期记录没有出处，不臆造
        self.assertIsNone(by_code["000568"]["origin"])
        self.assertEqual(by_code["000568"]["origin_label"], "")

    def test_plan_per_code_shares(self):
        paper = _paper()
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519", "000568"],
                lot_shares=200,
                shares_by_code={"600519": 100, "000568": 300},
            )
        by_code = {i["stock_code"]: i for i in plan["items"]}
        self.assertEqual(by_code["600519"]["shares"], 100)
        self.assertEqual(by_code["000568"]["shares"], 300)
        self.assertEqual(plan["total_amount"], 100 * 10.0 + 300 * 20.0)

    def test_plan_by_amount_per_code(self):
        paper = _paper()
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519", "000568"],
                lot_shares=0,
                amount_per_code=2000,
            )
        by_code = {i["stock_code"]: i for i in plan["items"]}
        # 2000/10=200 股；2000/20=100 股
        self.assertEqual(by_code["600519"]["shares"], 200)
        self.assertEqual(by_code["000568"]["shares"], 100)
        self.assertEqual(plan["sizing_mode"], "amount")
        self.assertEqual(plan["cost_model"], "zero")
        self.assertEqual(plan["total_amount"], 200 * 10.0 + 100 * 20.0)

    def test_plan_by_position_pct(self):
        paper = _paper(cash=10000.0)
        with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519"],
                lot_shares=0,
                position_pct=0.2,
            )
        # 20% of 10000 = 2000 → 200 股 @10
        self.assertEqual(plan["items"][0]["shares"], 200)
        self.assertEqual(plan["sizing_mode"], "pct")

    def test_enrich_operation_log_shows_trade_fees(self):
        from core.paper.costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "buy",
                "meta": {
                    "stock_code": "600519",
                    "shares": 100,
                    "price": 10.0,
                    "amount": 1000.0,
                },
            }
        ]
        trades = [
            {
                "side": "buy",
                "stock_code": "600519",
                "shares": 100,
                "fees": 5.0,
                "commission": 5.0,
                "stamp_duty": 0.0,
                "cost_model": "simple_cn",
            }
        ]
        out = enrich_operation_log_with_trade_fees(logs, trades)
        self.assertEqual(out[0]["meta"]["commission"], 5.0)
        self.assertEqual(out[0]["meta"]["fees"], 5.0)

    def test_enrich_operation_log_backfills_sell_pnl(self):
        from core.paper.costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "sell",
                "meta": {
                    "stock_code": "600519",
                    "shares": 100,
                    "price": 11.0,
                    "amount": 1100.0,
                    "commission": 5.0,
                    "fees": 5.0,
                },
            }
        ]
        trades = [
            {
                "side": "sell",
                "stock_code": "600519",
                "shares": 100,
                "amount": 1100.0,
                "pnl_pct": 10.0,
                "commission": 5.0,
                "fees": 5.0,
            }
        ]
        out = enrich_operation_log_with_trade_fees(logs, trades)
        self.assertEqual(out[0]["meta"]["pnl_pct"], 10.0)
        self.assertEqual(out[0]["meta"]["fees"], 5.0)

    def test_manual_sell_writes_pnl_pct(self):
        from core.paper import manual_sell

        paper = {
            "cash": 10000.0,
            "cost_model": "zero",
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "operation_log": [],
        }
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._quote_price", return_value=12.0):
                with patch(
                    "core.paper.rebalance._sell_match_block_reason",
                    return_value=None,
                ):
                    trades = manual_sell(paper, stock_code="600519", shares=100)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["pnl_pct"], 20.0)

    def test_enrich_estimates_strategy_buy_without_fee_fields(self):
        from core.paper.costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "buy",
                "meta": {
                    "stock_code": "601318",
                    "shares": 100,
                    "price": 54.48,
                    "amount": 5448.0,
                    "origin": "strategy",
                },
            }
        ]
        trades = [
            {
                "side": "buy",
                "stock_code": "601318",
                "shares": 100,
                "amount": 5448.0,
                "origin": "strategy",
            }
        ]
        out = enrich_operation_log_with_trade_fees(
            logs, trades, cost_model="simple_cn"
        )
        self.assertTrue(out[0]["meta"].get("fees_estimated"))
        self.assertGreater(float(out[0]["meta"].get("commission") or 0), 0)

    def test_simple_cn_fees_reduce_cash(self):
        from core.paper.costs import calc_trade_fees

        fee = calc_trade_fees("buy", 10000.0, model="simple_cn")
        self.assertEqual(fee["cost_model"], "simple_cn")
        self.assertGreater(fee["fees"], 0)
        paper = _paper(cash=100000.0)
        paper["cost_model"] = "simple_cn"
        with patch("core.ports.market.query_quote", side_effect=_fake_query):
            with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
                plan = plan_buy_codes(
                    paper, ["600519"], lot_shares=0, amount_per_code=2000
                )
                result = buy_codes_direct(
                    paper, ["600519"], lot_shares=0, amount_per_code=2000
                )
        self.assertEqual(plan["cost_model"], "simple_cn")
        self.assertGreater(plan.get("total_fees") or 0, 0)
        self.assertAlmostEqual(paper["cash"], plan["cash_after"])
        self.assertGreater(result["trades"][0].get("fees") or 0, 0)

    def test_mark_to_market_invested_and_drawdown(self):
        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0, "origin": ORIGIN_MANUAL},
            ],
        )
        paper["snapshots"] = [
            {"equity": 12000.0},
            {"equity": 9000.0},
        ]
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._batch_query_quotes", return_value={}):
                with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
                    summary = mark_to_market(paper)
        # cash 5000 + mv 1000 = 6000；仓位 1000/6000
        self.assertAlmostEqual(summary["invested_pct"], round(1000 / 6000 * 100, 1))
        self.assertEqual(summary["cost_model"], "zero")
        self.assertGreater(summary["max_drawdown_pct"], 0)

    def test_mark_to_market_today_pnl_from_change_pct(self):
        """今日收益 = Σ MV·chg/(100+chg)；账户%相对昨收净值。"""
        from datetime import datetime as dt

        class _InSession(dt):
            @classmethod
            def now(cls, tz=None):
                n = dt(2026, 8, 18, 10, 30)
                return n.replace(tzinfo=tz) if tz else n

        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ],
        )
        quotes = {
            "600519": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "price_raw": 11.0,
                "change_raw": 10.0,  # 昨收 10 → 今 11；ΔMV=100
            },
            "000568": {
                "success": True,
                "stock_code": "000568",
                "stock_name": "泸州",
                "price_raw": 19.0,
                "change_raw": -5.0,  # 昨收 20 → 今 19；ΔMV=-100
            },
        }
        with patch("core.paper.exec.datetime", _InSession), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-08-18"
        ), patch("core.ports.market.batch_query_quotes", return_value=quotes):
            with patch("core.paper.exec._batch_query_quotes", return_value=quotes):
                summary = mark_to_market(paper)
        # 今日浮动合计 0；净值 = 5000 + 1100 + 1900 = 8000；昨收净值同为 8000
        self.assertEqual(summary["today_pnl"], 0.0)
        self.assertEqual(summary["today_pnl_pct"], 0.0)
        self.assertEqual(summary["holdings"][0]["change_pct"], 10.0)
        self.assertEqual(summary["holdings"][1]["change_pct"], -5.0)

        quotes2 = {
            "600519": {
                "success": True,
                "price_raw": 11.0,
                "change_raw": 10.0,
            },
            "000568": {
                "success": True,
                "price_raw": 20.0,
                "change_raw": 0.0,
            },
        }
        with patch("core.paper.exec.datetime", _InSession), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-08-18"
        ), patch("core.ports.market.batch_query_quotes", return_value=quotes2):
            with patch("core.paper.exec._batch_query_quotes", return_value=quotes2):
                summary2 = mark_to_market(paper)
        # ΔMV = 100；equity=5000+1100+2000=8100；昨收净值=8000；pct=1.25
        self.assertEqual(summary2["today_pnl"], 100.0)
        self.assertEqual(summary2["today_pnl_pct"], 1.25)

    def test_mark_to_market_overnight_uses_prev_close_not_cost(self):
        """凌晨现价=0 时按昨收盯市，不退回成本。"""
        paper = _paper(
            cash=5000.0,
            holdings=[{"stock_code": "600519", "shares": 100, "cost": 10.0}],
        )
        quotes = {
            "600519": {
                "success": True,
                "stock_code": "600519",
                "price_raw": 0.0,
                "prev_close": 12.75,
                "change_raw": -100.0,
            }
        }
        with patch("core.paper.exec._batch_query_quotes", return_value=quotes):
            with patch("core.paper.exec._cached_bar_mark", return_value={}):
                summary = mark_to_market(paper)
        row = summary["holdings"][0]
        self.assertAlmostEqual(row["price"], 12.75)
        self.assertEqual(row["price_source"], "prev_close")
        self.assertEqual(row["change_pct"], 0.0)
        self.assertAlmostEqual(summary["equity"], 5000.0 + 1275.0)
        self.assertAlmostEqual(row["market_value"], 1275.0)

    def test_mark_to_market_today_pnl_vs_prev_nav(self):
        """有昨收账本时，今日收益 = 当前净值 − 上一交易日最后快照（含卖出/跳空）。"""
        from datetime import datetime as dt

        class _InSession(dt):
            @classmethod
            def now(cls, tz=None):
                n = dt(2026, 8, 18, 10, 30)
                return n.replace(tzinfo=tz) if tz else n

        paper = _paper(
            cash=5000.0,
            holdings=[{"stock_code": "600519", "shares": 100, "cost": 10.0}],
        )
        paper["initial_cash"] = 6000.0
        paper["snapshots"] = [
            {"ts": "2026-08-17T23:44:12.247", "equity": 6200.0},
        ]
        quotes = {
            "600519": {
                "success": True,
                "stock_code": "600519",
                "price_raw": 11.0,
                # 若仍按个股涨跌幅，会得到 +100 / ~1.6%；账本口径应对 6200
                "change_raw": 10.0,
            },
        }
        with patch("core.paper.exec.datetime", _InSession), patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-08-18"
        ), patch("core.ports.market.batch_query_quotes", return_value=quotes):
            with patch("core.paper.exec._batch_query_quotes", return_value=quotes):
                summary = mark_to_market(paper)
        # equity = 5000 + 1100 = 6100；相对昨收账本 6200 → -100 / -1.61%
        self.assertEqual(summary["today_pnl_basis"], "prev_nav")
        self.assertEqual(summary["today_pnl"], -100.0)
        self.assertAlmostEqual(summary["today_pnl_pct"], round(-100.0 / 6200.0 * 100.0, 2))
        self.assertAlmostEqual(summary["total_pnl_pct"], round((6100 / 6000 - 1) * 100.0, 2))

    def test_today_pnl_is_zero_after_calendar_rollover(self):
        """周六凌晨：日历已跨日、会话未开，今日收益为 0，不吃周五盘中快照。"""
        from datetime import datetime as dt

        class _Sat(dt):
            @classmethod
            def now(cls, tz=None):
                n = dt(2026, 9, 19, 1, 36)
                return n.replace(tzinfo=tz) if tz else n

        paper = _paper(
            cash=177343.09,
            holdings=[
                {"stock_code": "300122", "shares": 500, "cost": 12.33},
                {"stock_code": "000739", "shares": 700, "cost": 23.30},
            ],
        )
        paper["snapshots"] = [
            {"ts": "2026-09-17T15:00:00", "equity": 200000.0},
            {"ts": "2026-09-18T11:28:43", "equity": 200240.09},
        ]
        quotes = {
            "300122": {
                "success": True,
                "stock_code": "300122",
                "price_raw": 12.75,
                "prev_close": 12.35,
            },
            "000739": {
                "success": True,
                "stock_code": "000739",
                "price_raw": 23.48,
                "prev_close": 23.09,
            },
        }
        with patch("core.paper.exec.datetime", _Sat):
            with patch("core.paper.exec._batch_query_quotes", return_value=quotes):
                with patch(
                    "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
                ):
                    summary = mark_to_market(paper)
        self.assertAlmostEqual(summary["equity"], 200154.09, places=2)
        self.assertEqual(summary["today_pnl_basis"], "prev_close")
        self.assertEqual(summary["today_pnl"], 0.0)
        self.assertEqual(summary["today_pnl_pct"], 0.0)

    def test_today_pnl_keeps_session_on_friday_evening(self):
        """周五收盘后、日历未跨日：今日收益仍相对周四账本。"""
        from datetime import datetime as dt

        class _Fri(dt):
            @classmethod
            def now(cls, tz=None):
                n = dt(2026, 9, 18, 16, 20)
                return n.replace(tzinfo=tz) if tz else n

        paper = _paper(
            cash=177343.09,
            holdings=[
                {"stock_code": "300122", "shares": 500, "cost": 12.33},
                {"stock_code": "000739", "shares": 700, "cost": 23.30},
            ],
        )
        paper["snapshots"] = [
            {"ts": "2026-09-17T15:00:00", "equity": 200000.0},
            {"ts": "2026-09-18T11:28:43", "equity": 200240.09},
        ]
        quotes = {
            "300122": {
                "success": True,
                "stock_code": "300122",
                "price_raw": 12.75,
                "prev_close": 12.35,
            },
            "000739": {
                "success": True,
                "stock_code": "000739",
                "price_raw": 23.48,
                "prev_close": 23.09,
            },
        }
        with patch("core.paper.exec.datetime", _Fri):
            with patch("core.paper.exec._batch_query_quotes", return_value=quotes):
                with patch(
                    "core.signal.session_pit.oo_cycle_date", return_value="2026-09-18"
                ):
                    summary = mark_to_market(paper)
        self.assertEqual(summary["today_pnl_basis"], "prev_nav")
        self.assertAlmostEqual(summary["today_pnl"], 154.09, places=2)

    def test_mark_to_market_today_pnl_cash_only(self):
        paper = _paper(cash=10000.0, holdings=[])
        summary = mark_to_market(paper)
        self.assertEqual(summary["today_pnl"], 0.0)
        self.assertEqual(summary["today_pnl_pct"], 0.0)

    def test_mark_to_market_today_pnl_uses_reset_anchor(self):
        """当日回零后，今日收益相对回零价，不再吃昨收涨跌幅。"""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ],
        )
        paper["initial_cash"] = 8100.0
        paper["pnl_anchor"] = {
            "ts": f"{today}T10:00:00",
            "date": today,
            "equity": 8100.0,
            "prices": {"600519": 11.0, "000568": 20.0},
        }
        quotes = {
            "600519": {
                "success": True,
                "price_raw": 12.0,
                "change_raw": 20.0,  # 昨收口径会算更大；锚点只认 +1
            },
            "000568": {
                "success": True,
                "price_raw": 20.0,
                "change_raw": 5.0,
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=quotes):
            summary = mark_to_market(paper)
        # (12-11)*100 + (20-20)*100 = 100；相对回零净值 8100 → 1.23%
        self.assertEqual(summary["today_pnl_basis"], "reset")
        self.assertEqual(summary["today_pnl"], 100.0)
        self.assertEqual(summary["today_pnl_pct"], 1.23)

    def test_mark_to_market_origin_summary(self):
        paper = _paper(
            holdings=[
                {
                    "stock_code": "600519",
                    "shares": 100,
                    "cost": 10.0,
                    "origin": ORIGIN_MANUAL,
                },
                {
                    "stock_code": "000568",
                    "shares": 100,
                    "cost": 20.0,
                    "origin": ORIGIN_STRATEGY,
                },
            ]
        )
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._batch_query_quotes", return_value={}):
                with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
                    summary = mark_to_market(paper)
        labels = {o["origin_label"]: o for o in summary["origin_summary"]}
        self.assertEqual(labels["手动"]["count"], 1)
        self.assertEqual(labels["策略"]["count"], 1)
        self.assertAlmostEqual(labels["手动"]["market_value"], 1000.0)
        self.assertAlmostEqual(labels["策略"]["market_value"], 2000.0)

class TestPlanSyncToPaper(_QuotePatchMixin, unittest.TestCase):
    def test_preview_limited_to_watchlist(self):
        from core.watching.store import plan_sync_to_paper, read_watching, write_watching

        with tempfile.TemporaryDirectory() as tmp:
            uni_path = os.path.join(tmp, "watching.json")
            paper_path = os.path.join(tmp, "paper.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": ["600519", "000568"],
                    "watchlist_origins": ["手动", "手动"],
                    "watchlist_names": ["贵州茅台", "泸州老窖"],
                },
                uni_path,
            )
            with open(paper_path, "w", encoding="utf-8") as f:
                json.dump(_paper(), f)

            with patch("core.watching.store.read_watching", return_value=read_watching(uni_path)):
                with patch("adapters.market.quote_api.StockAPI.query", side_effect=_fake_query):
                    plan = plan_sync_to_paper(
                        paper_path,
                        codes=["600519", "999999"],
                        lot_shares=100,
                    )

            self.assertTrue(plan["success"])
            self.assertEqual([i["stock_code"] for i in plan["items"]], ["600519"])
            self.assertEqual(plan["missing"], ["999999"])
            with open(paper_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["holdings"], [])

class TestSellNavCurveSnapshots(unittest.TestCase):
    """卖出前先盯市落点：涨跌先画上，卖出点本身接近持平。"""

    def test_sell_splits_mtm_rise_from_trade(self):
        from services.paper_service import PaperService

        prices = {"600519": 10.0}

        def _q(code):
            code = str(code)
            return {
                "success": True,
                "stock_code": code,
                "stock_name": f"名{code}",
                "price_raw": prices[code],
            }

        def _batch(codes):
            return {str(c): _q(c) for c in codes}

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            svc = PaperService(path)
            svc.init()
            with patch("core.paper.open_fill.require_open_fill", return_value=None):
                with patch("core.paper.exec._query_quote", side_effect=_q):
                    with patch("core.paper.exec._batch_query_quotes", side_effect=_batch):
                        svc.set_cost_model("zero")
                        svc.buy(stock_code="600519", amount=5000)

                        paper = load_paper(path)
                        stale_equity = float(paper["snapshots"][-1]["equity"])
                        paper["snapshots"] = [
                            {
                                "ts": "2024-01-01T00:00:00.000",
                                "equity": stale_equity,
                                "cash": paper.get("cash"),
                                "stock_value": 5000.0,
                            }
                        ]
                        save_paper(paper, path)

                        prices["600519"] = 12.0
                        paper = load_paper(path)
                        from core.market.calendar import prev_trading_day
                        from core.paper.tplus1 import session_date

                        sess = session_date()
                        prev = prev_trading_day(sess) or "2026-08-20"
                        for h in paper.get("holdings") or []:
                            h["bought_at"] = f"{prev}T10:00:00"
                            h["lots"] = [
                                {
                                    "shares": float(h.get("shares") or 0),
                                    "bought_at": h["bought_at"],
                                    "bought_date": prev,
                                }
                            ]
                        save_paper(paper, path)
                        out = svc.sell(codes=["600519"])
                        self.assertTrue(out.get("ok"), out)

                        paper = load_paper(path)
                        snaps = paper.get("snapshots") or []
                        self.assertGreaterEqual(len(snaps), 2)
                        pre, post = snaps[-2], snaps[-1]
                        self.assertGreater(float(pre["equity"]), stale_equity)
                        self.assertAlmostEqual(
                            float(pre["equity"]), float(post["equity"]), delta=1.0
                        )
                        self.assertNotEqual(pre.get("ts"), post.get("ts"))
                        self.assertEqual(paper.get("holdings") or [], [])

class TestSnapshotsForUiLiveOverlay(unittest.TestCase):
    """盘中盯市与落盘快照分叉时，UI 曲线末点应对齐摘要总净值。"""

    def test_appends_live_point_when_mtm_moved(self):
        from core.paper import snapshots_for_ui

        paper = {
            "snapshots": [
                {
                    "ts": "2026-08-18T11:48:25.999",
                    "equity": 99976.3,
                    "cash": 55040.3,
                    "stock_value": 44936.0,
                }
            ]
        }
        summary = {
            "equity": 100238.3,
            "cash": 55040.3,
            "stock_value": 45198.0,
            "total_pnl_pct": 0.24,
            "position_count": 5,
        }
        out = snapshots_for_ui(paper, summary)
        self.assertEqual(len(out), 2)
        self.assertFalse(out[0].get("live"))
        self.assertTrue(out[-1].get("live"))
        self.assertAlmostEqual(float(out[-1]["equity"]), 100238.3)
        self.assertGreater(str(out[-1]["ts"]), str(out[0]["ts"]))

    def test_skips_overlay_when_already_aligned(self):
        from core.paper import snapshots_for_ui

        paper = {
            "snapshots": [{"ts": "2026-08-18T11:48:25.999", "equity": 99976.3}]
        }
        out = snapshots_for_ui(paper, {"equity": 99976.3, "cash": 1})
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].get("live"))

    def test_does_not_mutate_paper_snapshots(self):
        from core.paper import snapshots_for_ui

        paper = {"snapshots": [{"ts": "2026-08-18T11:48:25.999", "equity": 100.0}]}
        snapshots_for_ui(paper, {"equity": 110.0})
        self.assertEqual(len(paper["snapshots"]), 1)
        self.assertEqual(paper["snapshots"][0]["equity"], 100.0)

class TestResetClearsStaleNorthStar(unittest.TestCase):
    def test_reset_does_not_keep_pre_reset_sharpe(self):
        from services.paper_service import PaperService

        summary = {
            "equity": 199999.99,
            "cash": 67603.99,
            "stock_value": 132396.0,
            "position_count": 1,
            "holdings": [{"stock_code": "600519", "price": 10.0}],
        }
        fresh = {
            "ok": True,
            "computed_at": "2026-10-07T19:52:00",
            "paper_risk": {
                "rolling_sharpe": None,
                "calmar": None,
                "max_drawdown_pct": 0.0,
                "sample_count": 1,
                "status": "unavailable",
            },
            "realization": {},
            "ttm": {},
            "risk_blocks": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            svc = PaperService(path)
            svc.init()
            paper = load_paper(path)
            paper["last_north_star"] = {
                "ok": True,
                "computed_at": "2026-09-10T20:21:08",
                "paper_risk": {
                    "rolling_sharpe": 17.0888,
                    "calmar": 2458.8012,
                    "sample_count": 14,
                    "status": "ok",
                },
            }
            paper["last_ops_report"] = {
                "north_star": {
                    "rolling_sharpe": 17.0888,
                    "calmar": 2458.8012,
                    "status": {"paper_risk": "ok"},
                },
                "monitor_metrics": {
                    "rolling_sharpe": 20.3,
                    "calmar": 2328.0,
                    "max_drawdown_pct": 1.24,
                },
                "target_weights": {"600000": 10},
                "optimize": {"ok": True, "count": 1},
            }
            paper["last_optimize"] = {"ok": True, "weights_pct": {"600000": 10}}
            paper["pending_orders"] = {
                "as_of": "2026-10-01",
                "target_fill_date": "2026-10-08",
                "source": "manual_sell",
                "legs": [{"side": "sell", "stock_code": "600519", "shares": 100}],
            }
            paper.setdefault("operation_log", []).append(
                {
                    "ts": "2026-10-01T10:00:00",
                    "type": "watching_matrix_rebalance",
                    "detail": "矩阵 live 调仓",
                }
            )
            save_paper(paper, path)
            with patch(
                "services.paper_account.mark_to_market", return_value=summary
            ), patch(
                "core.north_star.build_north_star_report", return_value=fresh
            ):
                out = svc.reset()
            self.assertIn("基线 200,000", out.get("message") or "")
            risk = (out.get("north_star") or {}).get("paper_risk") or {}
            self.assertIsNone(risk.get("rolling_sharpe"))
            self.assertIsNone(risk.get("calmar"))
            saved = load_paper(path)
            self.assertEqual(len(saved["snapshots"]), 1)
            reset_logs = [
                row
                for row in (saved.get("operation_log") or [])
                if row.get("type") == "reset"
            ]
            self.assertTrue(reset_logs)
            self.assertIn("基线 200,000", reset_logs[-1].get("detail") or "")
            self.assertEqual(reset_logs[-1]["meta"]["equity"], 199999.99)
            self.assertFalse(saved.get("pending_orders"))
            self.assertNotIn("last_optimize", saved)
            self.assertNotIn("target_weights", saved.get("last_ops_report") or {})
            self.assertFalse(
                any(
                    row.get("type") == "watching_matrix_rebalance"
                    for row in (saved.get("operation_log") or [])
                )
            )
            self.assertIsNone(saved["last_north_star"]["paper_risk"]["rolling_sharpe"])
            self.assertIsNone(saved["last_ops_report"]["north_star"]["rolling_sharpe"])
            self.assertIsNone(saved["last_ops_report"]["monitor_metrics"]["calmar"])
            self.assertEqual(
                saved["last_ops_report"]["monitor_metrics"]["max_drawdown_pct"], 0.0
            )


# --- from test_paper_open_fill.py ---
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

    def test_open_fill_skips_limit_up_buy(self):
        """开盘涨停：买单不成交，挂单保留。"""
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "buy",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 10.0,
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "open_raw": 11.0,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertFalse(out.get("filled"))
        self.assertEqual(len(out.get("skips") or []), 1)
        self.assertIn("涨停", str((out["skips"][0] or {}).get("reason") or ""))
        legs = (paper.get("pending_orders") or {}).get("legs") or []
        self.assertEqual(len(legs), 1)
        self.assertEqual(paper.get("cash"), 100000.0)
        self.assertFalse(paper.get("trades"))

    def test_open_fill_skips_limit_down_sell(self):
        """开盘跌停：卖单不成交，持仓与挂单都保留。"""
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 10.0,
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "open_raw": 9.0,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertFalse(out.get("filled"))
        self.assertIn("跌停", str((out.get("skips") or [{}])[0].get("reason") or ""))
        self.assertEqual(len(paper.get("holdings") or []), 1)
        self.assertEqual(len((paper.get("pending_orders") or {}).get("legs") or []), 1)

    def test_open_fill_skips_halted(self):
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 10.0,
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "open_raw": 10.2,
                "prev_close": 10.0,
                "status": "停牌",
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertFalse(out.get("filled"))
        self.assertIn("停牌", str((out.get("skips") or [{}])[0].get("reason") or ""))
        self.assertEqual(len(paper.get("holdings") or []), 1)

    def test_open_fill_still_fills_normal_open(self):
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 10.0,
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "open_raw": 10.2,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(9, 30, "2026-08-24"), quotes=quotes)
        self.assertTrue(out.get("filled"))
        self.assertFalse(paper.get("pending_orders"))
        self.assertEqual(len(paper.get("holdings") or []), 0)

    def test_session_midpoint_chase_fills_sell(self):
        """开盘后：按意向价与现价中点成交卖出挂单。"""
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 12.0,
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "price_raw": 10.0,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(10, 30, "2026-08-24"), quotes=quotes)
        self.assertTrue(out.get("filled"), out)
        self.assertEqual(out.get("mode"), "chase")
        self.assertFalse(paper.get("pending_orders"))
        self.assertEqual(len(paper.get("holdings") or []), 0)
        trade = (out.get("trades") or [])[0]
        self.assertAlmostEqual(float(trade.get("price") or 0), 11.0, places=4)
        self.assertIn("中点追价", str(trade.get("note") or ""))

    def test_session_chase_respects_interval(self):
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 12.0,
                        "last_chase_at": "2026-08-24T10:25:00",
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "price_raw": 10.0,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(10, 30, "2026-08-24"), quotes=quotes)
        self.assertFalse(out.get("filled"))
        self.assertEqual(len((paper.get("pending_orders") or {}).get("legs") or []), 1)

    def test_eod_force_fills_at_last(self):
        from core.paper.open_fill import fill_pending_at_open, stage_pending

        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "rules": {"execution_mode": "next_open"},
        }
        stage_pending(
            paper,
            {
                "as_of": "2026-08-24",
                "target_fill_date": "2026-08-24",
                "legs": [
                    {
                        "side": "sell",
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "intent_price": 12.0,
                        "last_chase_at": "2026-08-24T14:55:00",
                    }
                ],
            },
        )
        quotes = {
            "600519": {
                "success": True,
                "price_raw": 9.8,
                "prev_close": 10.0,
                "stock_name": "茅台",
            }
        }
        out = fill_pending_at_open(paper, now=_dt(14, 55, "2026-08-24"), quotes=quotes)
        self.assertTrue(out.get("filled"), out)
        self.assertEqual(out.get("mode"), "eod_force")
        trade = (out.get("trades") or [])[0]
        self.assertAlmostEqual(float(trade.get("price") or 0), 9.8, places=4)

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
            "strategy_id": "short_conservative",
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

    def test_preopen_stages_until_continuous(self):
        """09:25–09:30 有调仓腿则挂今开单，不改持仓。"""
        from core.paper.open_fill import apply_next_open_commit

        original = self._paper()
        mutated = {**self._paper(), "cash": 1.0, "holdings": []}
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
            original, mutated, result, now=_dt(9, 25), dry_run=False
        )
        self.assertEqual(out["fill_action"], "staged")
        self.assertTrue((paper.get("pending_orders") or {}).get("live_preopen"))
        self.assertEqual(len(paper.get("holdings") or []), 1)
        self.assertAlmostEqual(float(paper.get("cash") or 0), 100000.0)
        self.assertEqual(
            str((paper.get("pending_orders") or {}).get("target_fill_date") or "")[:10],
            "2026-08-21",
        )
        _, at_open = apply_next_open_commit(
            original, mutated, result, now=_dt(9, 30), dry_run=False
        )
        self.assertEqual(at_open["fill_action"], "immediate")

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

        bundle = resolve_effective_execution(strategy="short_conservative", channel="paper")
        timing = bundle.get("rebalance_timing") or {}
        self.assertEqual(timing.get("execution_mode"), "next_open")
        self.assertIn("次日开盘", bundle.get("summary") or "")


# --- from test_paper_tplus1.py ---
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


# --- from test_paper_attribution.py ---
class TestPaperAttributionLite(unittest.TestCase):
    def test_selection_allocation_residual(self):
        paper = {"cash": 20000, "snapshots": [{"equity": 100000}]}
        summary = {
            "equity": 105000,
            "stock_value": 85000,
            "cash": 20000,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "sector": "消费",
                    "market_value": 40000,
                    "pnl_pct": 10.0,
                },
                {
                    "stock_code": "300750",
                    "stock_name": "宁德",
                    "sector": "新能源",
                    "market_value": 30000,
                    "pnl_pct": -5.0,
                },
                {
                    "stock_code": "600036",
                    "stock_name": "招行",
                    "sector": "金融",
                    "market_value": 15000,
                    "pnl_pct": 2.0,
                },
            ],
        }
        out = build_paper_attribution_lite(paper, summary)
        self.assertTrue(out["ok"])
        self.assertIn("selection_pct", out)
        self.assertIn("allocation_pct", out)
        self.assertIn("residual_pct", out)
        self.assertGreater(len(out["by_sector"]), 0)
        self.assertGreater(len(out["top_contributors"]), 0)
        self.assertEqual(out["top_contributors"][0]["stock_code"], "600519")
        # 相对上一快照 100k → 105k
        self.assertAlmostEqual(out["period_return_pct"], 5.0, places=2)

        ops = build_ops_report(
            strategy_id="short_conservative",
            cost_model="simple_cn",
            attribution=out,
        )
        self.assertTrue(ops["attribution"]["ok"])
        self.assertEqual(ops["attribution"]["selection_pct"], out["selection_pct"])

    def test_empty_holdings(self):
        out = build_paper_attribution_lite({}, {"equity": 100000, "holdings": []})
        self.assertFalse(out["ok"])


# --- from test_portfolio_bars_batch.py ---
def _bars__portfolio_bars_batch(n: int, *, start_offset: int = 0):
    out = []
    start = datetime(2024, 1, 1) + timedelta(days=start_offset)
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "open": 10 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.8 + i * 0.1,
                "close": 10.2 + i * 0.1,
                "volume": 1000 + i,
            }
        )
    return out

class TestPortfolioBarsBatchOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        reset_conn_cache()
        self._env = patch.dict(
            os.environ,
            {
                "QUANTLAB_STORE_DIR": self.tmp,
                "QUANTLAB_BARS_BACKEND": "sqlite",
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(reset_conn_cache)
        self.codes = ["000001", "600519", "300750"]
        for i, code in enumerate(self.codes):
            save_daily_cache(
                "CN",
                code,
                _bars__portfolio_bars_batch(80, start_offset=i),
                data_source="akshare_cn_daily",
                store_dir=self.tmp,
            )

    def test_offline_batch_tail_matches_per_code_service(self):
        limit = 40
        batch = _load_offline_bars_from_store(self.codes, limit=limit)
        self.assertEqual(set(batch.keys()), set(self.codes))
        for code in self.codes:
            sym, bars = batch[code]
            self.assertEqual(sym, code)
            self.assertEqual(len(bars), limit)
            from core.research.portfolio_bars import _fetch_bars_for_raw

            _raw, _sym, one = _fetch_bars_for_raw(
                code, limit=limit, offline_ok=True, offline_only=True
            )
            self.assertEqual(
                [b["date"] for b in bars],
                [b["date"] for b in one],
            )

    def test_load_bars_parallel_offline_only_uses_store(self):
        by_raw, remote_n = _load_bars_parallel(
            self.codes + ["999998"],
            limit=30,
            need=16,
            offline_ok=True,
            remote_fill=False,
        )
        self.assertEqual(remote_n, 0)
        self.assertEqual(len(by_raw["000001"][1]), 30)
        self.assertEqual(by_raw["999998"][1], [])


if __name__ == "__main__":
    unittest.main()
