"""纸面回放引擎：T+1 / 续持 / 现金地板 / 引擎元数据。"""

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
    REPLAY_CASH_FLOOR,
    REPLAY_INITIAL_CASH,
    REPLAY_RANK_ENTER,
    REPLAY_RANK_STRONG,
    backtest_paper_replay,
    mock_quote_from_bar,
    realized_yhat_windows,
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
                "core.paper.rebalance.buy._buy_match_block_reason",
                return_value=None,
            )
        )
        stack.enter_context(
            patch(
                "core.paper.rebalance.sell._sell_match_block_reason",
                return_value=None,
            )
        )
        stack.enter_context(
            patch(
                "skills.common.history.fetch_a_daily_bars",
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
        """同票连续入选且 ranking≥0：不开卖腿（可每日加 1000/2000 直到地板）。"""
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
        nxt_o = float(bars[i + 1]["open"])
        self.assertAlmostEqual(mid.get("realized_cc"), (close_t / prev_c - 1.0) * 100.0, places=3)
        self.assertAlmostEqual(mid.get("realized_on"), (nxt_o / close_t - 1.0) * 100.0, places=3)
        last_rows = [
            t
            for t in (out.get("sim_trades") or [])
            if str(t.get("as_of") or "")[:10] == last
        ]
        if last_rows:
            self.assertIsNone(last_rows[0].get("realized_on"))
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

    def test_default_cash_is_50w_floor_10w(self):
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
        self.assertEqual(REPLAY_INITIAL_CASH, 500_000.0)
        self.assertEqual(REPLAY_CASH_FLOOR, 100_000.0)

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
        """掉出 Top-K 但 ranking≥0：不卖。"""
        stock_bars = {
            "600519": _bars(20, step=0.5),
            "600036": _bars(20, step=0.35),
        }
        dates = [b["date"] for b in stock_bars["600519"]]
        rankings = {}
        for i, d in enumerate(dates):
            code = "600519" if (i % 2 == 0) else "600036"
            rankings[d] = [_rank_row(code, 2.0)]

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
        self.assertEqual(sells, [], "未进 Top-K 不应卖出")

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
        fills = [s for s in sim if s.get("status") != "skipped"]
        self.assertEqual(len(fills), len(trades))
        self.assertEqual(out.get("sim_trade_count"), len(trades))
        self.assertGreaterEqual(len(logs), 3)
        self.assertGreaterEqual(len(curve), len(logs))
        self.assertTrue(all(str(t.get("as_of") or "")[:10] for t in trades))
        self.assertTrue(all(s.get("side") in ("buy", "sell") for s in sim))
        paper_trades = (out.get("paper") or {}).get("trades") or []
        self.assertEqual(len(paper_trades), len(trades))
        self.assertEqual(out["params"]["lot_base"], 1000)
        self.assertEqual(out["params"]["lot_strong"], 2000)
        self.assertAlmostEqual(float(out["params"]["rank_enter"]), REPLAY_RANK_ENTER)
        self.assertAlmostEqual(float(out["params"]["rank_strong"]), REPLAY_RANK_STRONG)
        buys = [t for t in trades if t.get("side") == "buy"]
        self.assertTrue(buys)
        for t in buys:
            sh = float(t.get("shares") or 0)
            self.assertIn(sh, (1000.0, 2000.0), t)
            self.assertIn(t.get("action") or t.get("matrix_action"), ("open", "add"))
            self.assertEqual(t.get("cost_price"), t.get("price"), t)
            self.assertTrue(str(t.get("open_date") or "")[:10], t)
            self.assertIsNotNone(t.get("cum_cost"), t)
            self.assertGreater(float(t["cum_cost"]), 0)
        sim_buys = [
            s
            for s in sim
            if s.get("side") == "buy" and s.get("status") != "skipped"
        ]
        for s in sim_buys:
            self.assertEqual(s.get("cost_price"), s.get("price"), s)
            self.assertTrue(str(s.get("open_date") or "")[:10], s)
            self.assertIsNotNone(s.get("cum_cost"), s)
            self.assertGreater(float(s["cum_cost"]), 0)

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
            "skills.common.history.fetch_a_daily_bars",
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

    def test_attach_open_nowcast_follows_eod_when_no_tau(self):
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
        yn = out[0].get("y_nowcast")
        self.assertIsNotNone(yn)
        self.assertAlmostEqual(float(yn), 1.2, places=3)

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
        if yn is None and nc is None:
            return
        self.assertNotEqual(yn, 0)
        self.assertNotEqual(nc, 0)


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


class TestSessionOverlay(unittest.TestCase):
    def test_before_open_is_none(self):
        from core.backtest.paper_replay import replay_session_day

        self.assertIsNone(replay_session_day(now=datetime(2026, 9, 8, 9, 0, 0)))

    def test_after_open_is_today(self):
        from core.backtest.paper_replay import replay_session_day

        self.assertEqual(
            replay_session_day(now=datetime(2026, 9, 8, 10, 0, 0)), "2026-09-08"
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


if __name__ == "__main__":
    unittest.main()
