"""纸面回放引擎：T+1 / 续持 / 现金底仓 / 引擎元数据。"""

from __future__ import annotations

import os
import sys
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.paper_replay import backtest_paper_replay, mock_quote_from_bar
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
        """同票连续入选：次日不开卖腿（续持）。"""
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
                min_score=0.5,
                min_cash_pct=0.2,
                max_turnover_pct=100.0,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out["params"]["engine"], "paper_replay")
        sells = [
            t
            for t in (out.get("trades") or [])
            if t.get("side") == "sell" and t.get("stock_code") == "600519"
        ]
        self.assertEqual(sells, [], "续持不应卖出同票")
        held = {h.get("stock_code") for h in (out.get("holdings_end") or [])}
        self.assertIn("600519", held)
        self.assertGreaterEqual(len(out.get("equity_curve") or []), 3)

    def test_min_cash_pct_respected(self):
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
                min_score=0.5,
                min_cash_pct=0.2,
                max_turnover_pct=100.0,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        paper = out.get("paper") or {}
        last = (out.get("equity_curve") or [])[-1]
        equity = float(last.get("equity") or 0)
        cash = float(out.get("cash_end") or paper.get("cash") or 0)
        self.assertGreater(equity, 0)
        self.assertGreaterEqual(cash / equity, 0.19)

    def test_rotate_triggers_t1_or_sell_next_day(self):
        """换仓：买入次日才可卖（约束计数或卖腿发生在次日）。"""
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
                min_score=0.5,
                min_cash_pct=0.0,
                max_turnover_pct=100.0,
                cost_model="zero",
                initial_cash=1_000_000.0,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        hits = out.get("constraints_hit") or {}
        sells = [t for t in (out.get("trades") or []) if t.get("side") == "sell"]
        self.assertTrue(
            int(hits.get("t1_blocks") or 0) > 0 or len(sells) > 0,
            f"expected t1 or sells, got hits={hits} sells={len(sells)}",
        )


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


class TestIntradayStop(unittest.TestCase):
    def test_stop_exits_on_low(self):
        """成本 100、止损 -8% → low≤92 触发卖。"""
        from core.backtest.paper_replay import _apply_intraday_stops

        paper = {
            "cash": 0.0,
            "cost_model": "zero",
            "holdings": [
                {
                    "stock_code": "600519",
                    "shares": 100,
                    "cost": 100.0,
                    "lots": [
                        {
                            "shares": 100,
                            "bought_at": "2026-03-09T09:30:00.000",
                            "bought_date": "2026-03-09",
                        }
                    ],
                    "bought_at": "2026-03-09T09:30:00.000",
                }
            ],
            "trades": [],
        }
        date_maps = {
            "600519": {
                "2026-03-10": {
                    "open": 99.0,
                    "high": 99.0,
                    "low": 90.0,
                    "close": 91.0,
                }
            }
        }
        with paper_replay_context(
            as_of="2026-03-10",
            batch_query=lambda codes: {
                c: {"success": True, "price_raw": 91.0} for c in codes
            },
        ):
            trades = _apply_intraday_stops(
                paper,
                date_maps=date_maps,
                exec_date="2026-03-10",
                stop_loss_pnl=-8.0,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "sell")
        self.assertEqual(len(paper.get("holdings") or []), 0)


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


if __name__ == "__main__":
    unittest.main()
