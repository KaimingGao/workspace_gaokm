"""标签时点、换手预算、排序回退、PIT 计数。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


def _bars(n: int = 30, start: float = 10.0):
    out = []
    px = start
    for i in range(n):
        c = px + 0.05
        out.append(
            {
                "date": f"2024-03-{(i % 28) + 1:02d}",
                "open": round(px, 4),
                "high": round(c * 1.01, 4),
                "low": round(px * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000,
            }
        )
        px = c
    return out


class TestRobustInputs(unittest.TestCase):
    def test_turnover_ignores_bad_amounts(self):
        from core.paper.rebalance.turnover import (
            clip_shares_to_turnover_budget,
            resolve_buy_turnover_budget,
        )

        sell_amt, _, budget = resolve_buy_turnover_budget(
            sell_trades=[{"amount": "nope"}, {"amount": float("nan")}, {"amount": 1000}],
            buy_trades=None,
            equity_before=100_000,
            max_turnover_pct=10,
        )
        self.assertEqual(sell_amt, 1000.0)
        self.assertGreater(budget, 0)
        self.assertEqual(
            clip_shares_to_turnover_budget(
                shares=1000,
                fill_px=float("nan"),
                buy_amt_so_far=0,
                buy_budget_amt=budget,
                sell_amt=sell_amt,
                equity_before=100_000,
                max_turnover_pct=10,
            ),
            0,
        )
        from core.backtest.topk_oos_curve import oos_label_ready

        self.assertFalse(oos_label_ready("bad", 3, 3, 10))

    def test_bad_holding_shares_do_not_abort_plan(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        out = plan_rank_lot_day(
            scored=[{"stock_code": "600000", "y_oo_fuse": 1.2}],
            holdings=[{"stock_code": "600001", "shares": "abc"}],
            cash="12000",
            prices={"600000": 10.0},
            cfg={
                "fuse_enter": 0.01,
                "fuse_strong": 0.02,
                "cash_floor": 0.0,
                "holdings_mv_cap": 0.0,
                "t0_sell_blocks": {},
                "top_k": 5,
            },
            max_turnover_pct=float("nan"),
            min_cash_pct=float("nan"),
        )
        self.assertEqual(float(out["buys"][0]["shares"]), 1000)
        self.assertIsNone(out["max_turnover_pct"])

    def test_placeholder_dates_are_not_lookahead(self):
        from core.data.pit import count_lookahead_bars

        window = [
            {"date": "d0"},
            {"date": "2024-12-31"},
            "not-a-bar",
        ]
        self.assertEqual(count_lookahead_bars(window, "2024-03-01"), 1)
        self.assertEqual(count_lookahead_bars(window, "d0"), 0)

    def test_infinite_open_is_not_a_label(self):
        from core.backtest.topk_oos_curve import _leg_forward_return

        y = _leg_forward_return(
            {"A": {"2024-01-02": {"open": float("inf"), "close": 10}, "2024-01-03": {"close": 11}}},
            "A",
            "2024-01-02",
            "2024-01-03",
        )
        self.assertIsNone(y)

    def test_rank_key_rejects_nonfinite_fuse(self):
        from core.signal.dual_score.resolve import rank_key_for_item

        item = {
            "y_oo": 1.0,
            "y_τc": 0.2,
            "predicted_score_blend": -0.3,
        }
        with patch(
            "core.paper.rebalance.rank_lots.y_oo_fuse_pct_of",
            return_value=float("nan"),
        ):
            key = rank_key_for_item(item)
        self.assertAlmostEqual(float(key), -0.3)

    def test_eod_next_nan_is_not_a_rank_key(self):
        from core.signal.dual_score.resolve import rank_key_for_item

        item = {
            "dual_score_window": "eod_next",
            "y_oo": float("nan"),
            "predicted_score_blend": 1.2,
        }
        self.assertIsNone(rank_key_for_item(item))

    def test_compact_cutoff_does_not_keep_later_iso_dates(self):
        from core.data.pit import assert_window_pit, bars_as_of

        bars = [
            {"date": "2024-01-10", "close": 1},
            {"date": "20240120", "close": 1},
            {"date": "2024-02-01", "close": 1},
            {"date": "d0", "close": 1},
        ]
        got = [b["date"] for b in bars_as_of(bars, "20240115")]
        self.assertEqual(got, ["2024-01-10"])
        slash = bars_as_of([{"date": "2024/01/12", "close": 1}], "2024-01-15")
        self.assertEqual(len(slash), 1)
        chk = assert_window_pit(
            [{"date": "d0"}, {"date": "2024-12-31"}],
            "2024-03-01",
        )
        self.assertEqual(chk["lookahead_bars"], 1)
        self.assertTrue(chk["ok"] is False)

    def test_missing_close_does_not_abort_quote(self):
        from core.backtest.engine import _mock_quote_from_bars

        quote = _mock_quote_from_bars(
            [{"date": "2024-01-02", "close": 10}, {"date": "2024-01-03"}],
            1,
        )
        self.assertEqual(quote["change_raw"], 0.0)
        self.assertIsNone(quote["price_raw"])
        self.assertFalse(quote["success"])

    def test_bad_score_does_not_abort_later_buys(self):
        from core.paper.exec import simulate_buys

        paper = {
            "cash": 100_000.0,
            "holdings": ["junk", {"stock_code": "OLD", "shares": "abc"}],
            "rules": {
                "max_positions": "nope",
                "position_pct": "bad",
                "use_adaptive_position": False,
                "use_technical_filter": False,
            },
            "cost_model": "zero",
        }
        pool = [
            "not-an-item",
            {"stock_code": "600000", "score": "n/a"},
            {"stock_code": "600001", "score": 80},
        ]

        def _quote(code):
            return {"success": True, "price_raw": 10.0, "stock_name": code}

        with patch("core.paper.exec._query_quote", side_effect=_quote), patch(
            "core.paper.exec._bars_and_source", return_value=([], "t")
        ):
            trades = simulate_buys(
                paper,
                pool,
                equity_before="not-a-number",
                max_turnover_pct=-5,
            )
        self.assertEqual([t["stock_code"] for t in trades], ["600001"])
        self.assertEqual(pool[1]["skip_reason"], "评分无效")
        self.assertIn("junk", paper["holdings"])
        self.assertTrue(any(isinstance(h, dict) and h.get("stock_code") == "OLD" for h in paper["holdings"]))

    def test_bad_curve_params_return_an_error_dict(self):
        from core.backtest.topk_oos_curve import run_oos_topk_curve

        out = run_oos_topk_curve(
            {"600000": _bars(3)},
            top_k="nope",
            horizon_days="x",
            return_model_ridge_lambda="nan",
        )
        self.assertFalse(out["success"])
        self.assertIn("共同交易日", out["error"])

    def test_bad_engine_params_do_not_raise(self):
        from core.backtest.engine import backtest_signal_on_bars

        out = backtest_signal_on_bars(
            _bars(8),
            horizon_days="nope",
            min_score="bad",
            min_history="z",
            end_index="later",
        )
        self.assertFalse(out["success"])
        self.assertIn("日线不足", out["error"])

    def test_infinite_fuse_does_not_open(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        out = plan_rank_lot_day(
            scored=[{"stock_code": "600000", "y_oo_fuse": float("inf")}],
            holdings=[],
            cash=12000,
            prices={"600000": 10.0},
            cfg={
                "fuse_enter": 0.01,
                "fuse_strong": 0.02,
                "cash_floor": 0.0,
                "holdings_mv_cap": 0.0,
                "t0_sell_blocks": {},
                "top_k": 5,
            },
        )
        self.assertEqual(out["buys"], [])


class TestOosLabelReady(unittest.TestCase):
    def test_label_waits_for_exit_close(self):
        from core.backtest.topk_oos_curve import oos_label_exit_index, oos_label_ready

        horizon = 3
        j = 10
        n = 40
        self.assertEqual(oos_label_exit_index(j, horizon), j + 1 + horizon)
        self.assertFalse(oos_label_ready(j, j + 2, horizon, n))
        self.assertTrue(oos_label_ready(j, j + 1 + horizon, horizon, n))
        self.assertFalse(oos_label_ready(j, j + 1 + horizon, horizon, j + 1 + horizon))

    def test_curve_marks_limit_filter_off(self):
        from core.backtest.topk_oos_curve import run_oos_topk_curve

        stock_bars = {
            "600519": _bars(40),
            "000001": _bars(40, start=8),
        }
        out = run_oos_topk_curve(
            stock_bars,
            top_k=1,
            horizon_days=2,
            min_history=8,
            min_score=-999,
            rank_mode="heuristic_score",
            allow_heuristic_baseline=True,
        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertIs(out["params"].get("limit_filter"), False)


class TestTurnoverCycle(unittest.TestCase):
    def _paper(self, **rules):
        base = {
            "max_positions": 2,
            "position_pct": 0.5,
            "use_adaptive_position": False,
            "use_technical_filter": False,
        }
        base.update(rules)
        return {
            "cash": 100_000.0,
            "holdings": [],
            "trades": [{"side": "sell", "amount": 500_000.0, "stock_code": "OLD"}],
            "rules": base,
            "cost_model": "zero",
        }

    def _run(self, paper, pool, prices, **kwargs):
        from core.paper.exec import simulate_buys

        def _quote(code):
            return {
                "success": True,
                "price_raw": prices[code],
                "stock_name": code,
            }

        with patch("core.paper.exec._query_quote", side_effect=_quote), patch(
            "core.paper.exec._bars_and_source", return_value=([], "t")
        ):
            return simulate_buys(paper, pool, equity_before=100_000.0, **kwargs)

    def test_historical_sells_do_not_eat_this_cycle(self):
        paper = self._paper()
        pool = [{"stock_code": "600000", "stock_name": "甲", "score": 80}]
        plain = self._run(self._paper(), pool, {"600000": 10.0})
        capped = self._run(
            paper,
            pool,
            {"600000": 10.0},
            max_turnover_pct=10.0,
            cycle_sells=[],
        )
        plain_sh = float(plain[0]["shares"])
        cap_sh = float(capped[0]["shares"])
        self.assertGreater(plain_sh, cap_sh)
        self.assertGreater(cap_sh, 0)
        self.assertNotIn("skip_reason", pool[0])
        self.assertIn("换手预算", capped[0]["note"])

    def test_zero_clip_does_not_consume_position_slot(self):
        paper = self._paper(max_positions=1)
        pool = [
            {"stock_code": "600000", "stock_name": "贵", "score": 90},
            {"stock_code": "600001", "stock_name": "廉", "score": 80},
        ]
        trades = self._run(
            paper,
            pool,
            {"600000": 200.0, "600001": 10.0},
            max_turnover_pct=10.0,
            cycle_sells=[],
        )
        codes = [t["stock_code"] for t in trades]
        self.assertNotIn("600000", codes)
        self.assertIn("600001", codes)
        self.assertIn("换手预算不足", pool[0].get("skip_reason") or "")
        self.assertNotIn("skip_reason", pool[1])


class TestRankLotBudget(unittest.TestCase):
    def test_second_buy_sees_first_turnover(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {"stock_code": "600000", "stock_name": "弱", "y_oo_fuse": 1.2, "y_on": 0.0},
            {"stock_code": "600001", "stock_name": "强", "y_oo_fuse": 3.0, "y_on": 0.0},
        ]
        cfg = {
            "fuse_enter": 0.01,
            "fuse_strong": 0.02,
            "cash_floor": 0.0,
            "holdings_mv_cap": 0.0,
            "t0_sell_blocks": {},
            "top_k": 5,
            "fusion_w_oo": 0.5,
            "fusion_w_oc": 0.5,
        }
        full = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 10.0},
            cfg=cfg,
        )
        capped = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 10.0},
            cfg=cfg,
            max_turnover_pct=1.0,
        )
        full_by = {t["stock_code"]: t for t in full["buys"]}
        cap_by = {t["stock_code"]: t for t in capped["buys"]}
        self.assertEqual(float(full_by["600001"]["shares"]), 2000)
        self.assertEqual(float(full_by["600000"]["shares"]), 1000)
        self.assertIn("600001", cap_by)
        self.assertLess(float(cap_by["600001"]["shares"]), 2000)
        self.assertNotIn("600000", cap_by)
        reasons = " ".join(str(s.get("reason") or "") for s in capped["skips"])
        self.assertIn("换手预算", reasons)

    def test_min_cash_pct_reserves_equity(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {"stock_code": "600000", "stock_name": "甲", "y_oo_fuse": 1.2, "y_on": 0.0},
        ]
        cfg = {
            "fuse_enter": 0.01,
            "fuse_strong": 0.02,
            "cash_floor": 0.0,
            "holdings_mv_cap": 0.0,
            "t0_sell_blocks": {},
            "top_k": 5,
        }
        bare = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=12_000,
            prices={"600000": 10.0},
            cfg=cfg,
        )
        reserved = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=12_000,
            prices={"600000": 10.0},
            cfg=cfg,
            min_cash_pct=0.2,
        )
        self.assertEqual(float(bare["buys"][0]["shares"]), 1000)
        self.assertEqual(float(reserved["buys"][0]["shares"]), 900)
        self.assertGreaterEqual(reserved["cash_after_plan"], 12_000 * 0.2 - 1e-6)
        self.assertEqual(reserved["cash_floor"], 0.0)


class TestRankKeyAndPanel(unittest.TestCase):
    def test_missing_tau_uses_stored_blend(self):
        from core.signal.dual_score.resolve import rank_key_for_item

        item = {
            "predicted_score": 1.5,
            "y_oo": 1.5,
            "predicted_score_blend": -0.4,
        }
        self.assertAlmostEqual(rank_key_for_item(item), -0.4)

    def test_tau_present_keeps_recomputed_fuse(self):
        from core.paper.rebalance.rank_lots import y_oo_fuse_pct_of
        from core.signal.dual_score.resolve import rank_key_for_item

        item = {
            "predicted_score": 1.0,
            "y_oo": 1.0,
            "y_τc": 1.0,
            "predicted_score_blend": -0.2,
        }
        fuse = y_oo_fuse_pct_of(item)
        self.assertIsNotNone(fuse)
        self.assertAlmostEqual(rank_key_for_item(item), float(fuse))
        self.assertNotAlmostEqual(float(fuse), -0.2)

    def test_nonfinite_y_dropped_from_panel(self):
        from core.research.factor_ols_fit import _prepare_complete_panel

        xs = [{"a": float(i)} for i in range(20)]
        ys = [float(i) for i in range(20)]
        ys[0] = float("nan")
        ys[3] = float("inf")
        xs[0]["hole"] = None
        xs[3]["hole"] = None
        for i, row in enumerate(xs):
            if "hole" not in row:
                row["hole"] = float(i)
        xs_c, ys_c, _active, _ex, meta = _prepare_complete_panel(
            xs, ys, ["a", "hole"], min_samples_over_p=2
        )
        self.assertEqual(meta["dropped_nonfinite_y"], 2)
        self.assertIn("a", _active)
        self.assertIn("hole", _active)
        self.assertEqual(len(ys_c), 18)
        self.assertEqual(len(xs_c), 18)
        self.assertTrue(all(v == v and abs(v) != float("inf") for v in ys_c))


class TestPitReport(unittest.TestCase):
    def test_missing_as_of_is_not_counted_as_lookahead(self):
        from core.data.pit import window_as_of

        bars = _bars(12)
        bars[5]["date"] = ""
        window, meta = window_as_of(bars, 5, max_window=5)
        self.assertFalse(meta["ok"])
        self.assertEqual(meta["lookahead_bars"], 0)
        self.assertEqual(len(window), 5)

    def test_inverted_date_sets_bars_pit_false(self):
        from core.backtest.engine import backtest_signal_on_bars

        bars = _bars(36)
        bars[8]["date"] = "2024-12-31"
        result = backtest_signal_on_bars(
            bars,
            min_score=0,
            min_history=10,
            horizon_days=2,
            respect_limit=False,
        )
        self.assertTrue(result.get("success"), result.get("error"))
        pit = result["pit_report"]
        self.assertGreater(pit["lookahead_violations"], 0)
        self.assertFalse(pit["bars_pit"])

    def test_index_slice_lookahead_counts(self):
        from core.backtest.engine import backtest_signal_on_bars

        bars = _bars(36)
        index_bars = _bars(36, start=3000)
        index_bars[6]["date"] = "2024-12-31"
        result = backtest_signal_on_bars(
            bars,
            index_bars=index_bars,
            min_score=0,
            min_history=10,
            horizon_days=2,
            respect_limit=False,
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertGreater(result["pit_report"]["lookahead_violations"], 0)
        self.assertFalse(result["pit_report"]["bars_pit"])


if __name__ == "__main__":
    unittest.main()
