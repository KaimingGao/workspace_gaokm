import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.t0.backtest import backtest_t0_on_bars
from core.t0.config import DEFAULT_T0_RULES, load_t0_rules
from core.t0.rules import simulate_t0_day, simulate_t0_on_holdings
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine
from agent.routing import infer_quant_task


def _bar(date, o, h, l, c):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _mins(date, points):
    """points: list of (hhmm, open, high, low, close)."""
    out = []
    for hhmm, o, h, l, c in points:
        hh, mm = divmod(int(hhmm), 100)
        out.append(
            {
                "datetime": f"{date} {hh:02d}:{mm:02d}:00",
                "date": date,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )
    return out


def _mins_hl(date=None, o=100, high=105, low=98, close=101, bar=None):
    """先冲高后回落：利好正T两腿。可传 bar 对齐 OHLC/开盘跳空。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    return _mins(
        str(date),
        [
            (1000, o, high, o, high - 0.5),
            (1400, high - 0.5, high - 0.5, low, close),
        ],
    )


def _mins_lh(date=None, o=100, high=105, low=96, close=101, bar=None):
    """先砸后拉：利好反T两腿。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    return _mins(
        str(date),
        [
            (1000, o, o, low, low + 0.5),
            (1400, low + 0.5, high, low + 0.5, close),
        ],
    )


def _rules(**kwargs):
    """单测默认 first_touch + 关 ATR。"""
    base = {"path_mode": "first_touch", "use_atr": False}
    base.update(kwargs)
    base["path_mode"] = "first_touch"
    return base


class TestT0Core(unittest.TestCase):
    def test_default_fill_mode_trigger(self):
        self.assertEqual(DEFAULT_T0_RULES["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["path_mode"], "first_touch")

    def test_sell_and_cover_same_day(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                direction="long_t",
                ref="cost",
                min_range_pct=1.0,
            ),
            stock_code="600519",
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 400)
        self.assertEqual(out["shares_end"], 1000)
        self.assertEqual(len(out["trades"]), 2)
        self.assertGreater(out["pnl"], 0)

    def test_trigger_fill_more_conservative_than_optimistic(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        common = _rules(
            t0_ratio=0.4,
            sell_trigger_pct=2.0,
            buy_trigger_pct=1.5,
            direction="long_t",
            min_range_pct=1.0,
        )
        opt = simulate_t0_day(
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "optimistic"},
        minute_bars=_mins_hl(bar=bar))
        trg = simulate_t0_day(
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "trigger"},
        minute_bars=_mins_hl(bar=bar))
        self.assertGreaterEqual(opt["pnl"], trg["pnl"])

    def test_low_range_skipped(self):
        bar = _bar("2026-01-10", 100, 100.5, 99.5, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(sell_trigger_pct=2.0, buy_trigger_pct=1.5, direction="long_t"),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["sold_qty"], 0)
        self.assertIn("振幅", out.get("reason") or "")

    def test_rolling_range_gate_waits_for_prefix(self):
        """前缀振幅未达标时不跑触达；午后扩幅后才评估（对齐 live Worker）。"""
        bar = _bar("2026-01-10", 100, 103, 99.5, 100)
        mins = _mins(
            "2026-01-10",
            [
                (935, 100, 102.0, 99.98, 101.0),  # 触卖价 102，但前缀振幅仍小
                (940, 100, 100.05, 99.99, 100.0),
                (1400, 100, 103.0, 99.5, 100.0),  # 扩幅后过闸
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="long_t",
                min_range_pct=2.5,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("range_mode"), "rolling")
        self.assertGreaterEqual(int(out.get("prefix_bars") or 0), 3)
        self.assertGreater(out.get("sold_qty") or 0, 0)

    def test_no_trigger_when_range_ok_but_levels_miss(self):
        # 振幅够，但高点未到卖出阈值
        bar = _bar("2026-01-10", 100, 101.2, 98.5, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                sell_trigger_pct=3.0,
                buy_trigger_pct=1.5,
                direction="long_t",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar),
        )
        self.assertTrue(out["success"])
        self.assertEqual(out.get("sold_qty") or 0, 0)
        self.assertFalse(out.get("trades") or [])
        # 分钟路径：未触达可记跳过
        if out.get("skipped"):
            self.assertTrue(
                "未触" in (out.get("reason") or "") or "触" in (out.get("reason") or "")
            )
    def test_tplus1_sellable_cap(self):
        bar = _bar("2026-01-10", 100, 110, 90, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            sellable_shares=100,
            rules=_rules(
                t0_ratio=1.0,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
                direction="long_t",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertEqual(out["sold_qty"], 100)

    def test_small_holding_ratio_bumps_to_one_lot(self):
        """100 股 × 40% 取整为 0 时，应抬到 1 手，避免小仓静默无成交。"""
        from core.t0.rules import _t0_qty_lots

        self.assertEqual(_t0_qty_lots(100, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(200, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(250, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(300, 0.4, 100), 100)
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=100,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                direction="long_t",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["sold_qty"], 100)

    def test_reverse_t_buy_then_sell_old(self):
        """反T：低吸加仓 + 卖旧底仓；完成往返后仓位不变。"""
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            stock_code="600519",
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertEqual(out["direction_used"], "reverse_t")
        self.assertGreater(out["bought_qty"], 0)
        self.assertEqual(out["sold_back_qty"], out["bought_qty"])
        self.assertEqual(out["shares_end"], 1000)
        self.assertGreater(out["pnl"], 0)
        notes = " ".join(str(t.get("note") or "") for t in out.get("trades") or [])
        self.assertIn("旧底仓", notes)
        self.assertNotIn("卖回加的", notes)

    def test_reverse_t_requires_sellable_old(self):
        """无可卖旧仓时反T不能靠卖当日新买股完成。"""
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            sellable_shares=0,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("bought_qty") or 0, 0)
        self.assertIn("可卖", out.get("reason") or "")

    def test_reverse_t_sellable_caps_round_trip(self):
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            sellable_shares=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["bought_qty"], 100)
        self.assertEqual(out["sold_back_qty"], 100)
        self.assertEqual(out["shares_end"], 1000)

    def test_auto_gap_down_picks_reverse_t(self):
        # 默认 ref=open 时，auto 必须看昨收跳空，否则永远正 T
        bar = _bar("2026-01-10", 98, 103, 96, 100)
        bar["prev_close"] = 100
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="auto",
                ref="open",
                auto_weak_pct=0.5,
                auto_strong_pct=0.5,
                t0_ratio=0.4,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["direction_used"], "reverse_t")

    def test_auto_with_ref_open_no_prev_close_defaults_long(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="auto",
                ref="open",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertEqual(out.get("direction_used") or "long_t", "long_t")


    def test_signal_strong_gap_picks_long(self):
        # 高开 + 昨收偏强 → 正T
        hist = [
            _bar("d1", 98, 100, 97, 99),
            _bar("d2", 99, 101, 98, 100),
            _bar("d3", 100, 104, 99, 103),  # 昨收近高
        ]
        bar = _bar("d4", 105, 108, 102, 104)  # 高开约 +1.9%
        bar["prev_close"] = 103
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "long_t")
        self.assertIsNotNone(out.get("direction_score"))

    def test_signal_weak_gap_picks_reverse(self):
        hist = [
            _bar("d1", 102, 103, 100, 101),
            _bar("d2", 101, 102, 99, 100),
            _bar("d3", 100, 101, 96, 97),  # 昨收偏弱
        ]
        bar = _bar("d4", 95, 99, 93, 96)  # 低开
        bar["prev_close"] = 97
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "reverse_t")

    def test_signal_low_confidence_skips(self):
        hist = [
            _bar("d1", 100, 101, 99, 100),
            _bar("d2", 100, 101, 99, 100),
            _bar("d3", 100, 101, 99, 100),
        ]
        bar = _bar("d4", 100.1, 103, 98, 101)  # 几乎平开
        bar["prev_close"] = 100
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.35,
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("signal_skip"))
        self.assertIn("低置信", out.get("reason") or "")


    def test_exposure_when_uncovered(self):
        bar = _bar("2026-01-10", 100, 105, 104, 104.5)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=5.0,
                direction="long_t",
                fill_mode="trigger",
                must_cover_same_day=False,
                min_range_pct=0.5,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 0)
        self.assertNotEqual(out["exposure_pnl"], 0)

    def test_backtest_runs_with_optimistic_compare(self):
        bars = [
            _bar(f"d{i}", 100 + i * 0.1, 110, 90, 100 + i * 0.05) for i in range(20)
        ]
        mins = {b["date"]: _mins_hl(bar=b) for b in bars}
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(direction="long_t", min_range_pct=1.0),
            compare_optimistic=True,
            minute_by_date=mins,
            require_minute=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(report["t0_trade_days"], 1)
        self.assertIn("t0_pnl_total", report)
        self.assertIn("optimistic_compare", report)
        self.assertIn("exposure_pnl_total", report)
        self.assertIn("cover_rate_pct", report)
        self.assertIn("participate_rate_pct", report)
        self.assertIn("avg_pnl_per_trade_day", report)
        self.assertIn("t0_pnl_with_exposure", report)
        self.assertEqual(
            report["t0_pnl_with_exposure"],
            round(report["t0_pnl_total"] + report["exposure_pnl_total"], 2),
        )
        self.assertIn("delta_pnl_ratio_pct", report["optimistic_compare"])
        self.assertNotIn("round_trip_profit_rate_pct", report)
        sample = report.get("trade_days_sample") or []
        if report["t0_trade_days"] > 0 and sample:
            self.assertTrue(any((d.get("trades") or []) for d in sample))
            legs = next(d.get("trades") for d in sample if d.get("trades"))
            self.assertIn("price", legs[0])
            row = next(d for d in sample if d.get("trades"))
            self.assertIn("sell_price", row)

    def test_backtest_eval_window_decoupled_from_score_warmup(self):
        from datetime import date, timedelta

        base = date(2025, 1, 2)
        bars_all = [
            _bar((base + timedelta(days=i)).isoformat(), 100, 106, 96, 101)
            for i in range(45)
        ]
        eval_10 = bars_all[-10:]
        mins = {b["date"]: _mins_hl(bar=b) for b in eval_10}
        captured: list = []

        def _fake_scores(code, *, hist_bars, **kw):
            captured.append(len(hist_bars or []))
            return {
                "y_eod": 0.5,
                "y_tau": 0.35,
                "y_trade": 0.4,
                "y_on": 0.2,
                "y_nowcast": None,
                "y_check": None,
                "eod_trust": None,
            }

        rules = _rules(direction="dual_y", use_atr=False, min_range_pct=0.5)
        with patch("core.t0.score_policy.resolve_scores_for_code", side_effect=_fake_scores):
            report = backtest_t0_on_bars(
                eval_10,
                bars_history=bars_all,
                eval_lookback=10,
                initial_shares=1000,
                initial_cost=100,
                rules=rules,
                minute_by_date=mins,
                compare_optimistic=False,
                require_minute=True,
                stock_code="600519",
            )
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("score_warmup_bars"), 35)
        self.assertTrue(captured)
        self.assertEqual(captured[-1], 44)

        captured.clear()
        with patch("core.t0.score_policy.resolve_scores_for_code", side_effect=_fake_scores):
            backtest_t0_on_bars(
                eval_10,
                initial_shares=1000,
                initial_cost=100,
                rules=rules,
                minute_by_date=mins,
                compare_optimistic=False,
                require_minute=True,
                stock_code="600519",
            )
        self.assertEqual(captured[-1], 9)


    def test_backtest_resolve_defaults_dual_y(self):
        from core.execution import resolve_t0_rules, strip_execution_meta

        cfg = strip_execution_meta(
            resolve_t0_rules(rules={"use_atr": False}, channel="backtest", has_minute=False)
        )
        self.assertEqual(cfg.get("direction"), "dual_y")
        self.assertEqual(cfg.get("path_mode"), "first_touch")

    def test_minute_first_touch_avoids_false_cover(self):
        """先砸后拉：正T卖在尾盘，分钟路径不应再用早盘低点虚回补。"""
        bar = _bar("2024-01-02", 100, 105, 97, 104)
        minutes = [
            {
                "datetime": "2024-01-02 09:35:00",
                "date": "2024-01-02",
                "open": 100,
                "high": 100.5,
                "low": 97,
                "close": 97.5,
            },
            {
                "datetime": "2024-01-02 10:00:00",
                "date": "2024-01-02",
                "open": 97.5,
                "high": 99,
                "low": 97.2,
                "close": 98,
            },
            {
                "datetime": "2024-01-02 14:00:00",
                "date": "2024-01-02",
                "open": 100,
                "high": 105,
                "low": 100,
                "close": 104,
            },
        ]
        synth = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                direction="long_t",
                path_mode="first_touch",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_hl(bar=bar))
        minute = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="long_t",
                path_mode="first_touch",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
        )
        self.assertGreater(int(synth.get("covered_qty") or 0), 0)
        self.assertGreater(int(minute.get("sold_qty") or 0), 0)
        self.assertEqual(int(minute.get("covered_qty") or 0), 0)
        self.assertEqual(minute.get("path_mode"), "first_touch")

    def test_minute_first_touch_covers_after_sell(self):
        bar = _bar("2024-01-03", 100, 105, 97, 100)
        minutes = [
            {
                "datetime": "2024-01-03 10:00:00",
                "date": "2024-01-03",
                "open": 100,
                "high": 105,
                "low": 101,
                "close": 104,
            },
            {
                "datetime": "2024-01-03 11:00:00",
                "date": "2024-01-03",
                "open": 104,
                "high": 104,
                "low": 97,
                "close": 98,
            },
        ]
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="long_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertEqual(int(out.get("covered_qty") or 0), int(out.get("sold_qty") or 0))
        self.assertGreater(float(out.get("pnl") or 0), 0)
        trades = out.get("trades") or []
        self.assertTrue(all(t.get("at") for t in trades), trades)
        self.assertEqual(trades[0].get("leg_kind"), "trigger")
        self.assertEqual(trades[1].get("leg_kind"), "trigger")

    def test_minute_forced_eod_cover_stamps_at(self):
        bar = _bar("2024-01-03", 100, 105, 99, 100)
        minutes = [
            {
                "datetime": "2024-01-03 10:00:00",
                "date": "2024-01-03",
                "open": 100,
                "high": 105,
                "low": 101,
                "close": 104,
            },
            {
                "datetime": "2024-01-03 15:00:00",
                "date": "2024-01-03",
                "open": 104,
                "high": 104,
                "low": 100.5,
                "close": 100,
            },
        ]
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="long_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out["success"])
        buys = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].get("leg_kind"), "eod_cover")
        self.assertIn("15:00", str(buys[0].get("at") or ""))
        self.assertEqual(out.get("touch_cover_at"), buys[0].get("at"))

    def test_backtest_minute_by_date_sets_first_touch(self):
        bars = []
        for i in range(8):
            b = _bar(f"2024-01-{i+1:02d}", 100, 106, 96, 101)
            bars.append(b)
        # 仅最后一天给分钟：先高后低
        d = bars[-1]["date"]
        mins = {
            d: [
                {
                    "datetime": f"{d} 10:00:00",
                    "date": d,
                    "open": 100,
                    "high": 106,
                    "low": 101,
                    "close": 105,
                },
                {
                    "datetime": f"{d} 14:00:00",
                    "date": d,
                    "open": 105,
                    "high": 105,
                    "low": 96,
                    "close": 100,
                },
            ]
        }
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={
                "use_atr": False,
                "min_range_pct": 1.0,
                "direction": "long_t",
                "path_mode": "first_touch",
                "fill_mode": "trigger",
                "sell_trigger_pct": 2.0,
                "buy_trigger_pct": 1.5,
            },
            minute_by_date=mins,
            compare_optimistic=False,
            compare_daily=False,
            require_minute=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(int(report.get("minute_path_days") or 0), 1)
        self.assertNotIn("daily_compare", report)
        self.assertGreaterEqual(int(report.get("missing_minute_days") or 0), 1)

    def test_require_minute_rejects_daily_only(self):
        bars = [_bar(f"2024-01-{i+1:02d}", 100, 106, 96, 101) for i in range(5)]
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={
                "use_atr": False,
                "min_range_pct": 1.0,
                "direction": "long_t",
                "path_mode": "first_touch",
            },
            minute_by_date=None,
            require_minute=True,
            compare_optimistic=False,
        )
        self.assertFalse(report.get("success"))
        self.assertTrue("分钟" in (report.get("error") or "") or "日线" in (report.get("error") or ""))

    def test_paper_holdings_dry_run(self):
        paper = {
            "cash": 50000,
            "holdings": [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 100}
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.4,
                    "sell_trigger_pct": 2.0,
                    "buy_trigger_pct": 1.5,
                    "direction": "long_t",
                    "fill_mode": "optimistic",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 1.0,
                }
            },
        }
        bars = {"600519": _bar("2026-01-09", 100, 105, 98, 101)}
        mins = {"600519": _mins_hl(bar=bars["600519"])}
        out = simulate_t0_on_holdings(
            paper, bars_by_code=bars, minute_bars_by_code=mins, dry_run=True
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["dry_run"])
        self.assertGreater(len(out["trades"]), 0)
        self.assertEqual(len(paper["trades"]), 0)
        self.assertEqual(float(paper["holdings"][0]["shares"]), 1000)

        out2 = simulate_t0_on_holdings(
            paper, bars_by_code=bars, minute_bars_by_code=mins, dry_run=False
        )
        self.assertFalse(out2["dry_run"])
        self.assertGreater(len(paper["trades"]), 0)
        t0_logs = [
            e
            for e in paper.get("operation_log") or []
            if (e.get("meta") or {}).get("origin") == "t0"
        ]
        self.assertGreater(len(t0_logs), 0)

    def test_routing_and_skill_task(self):
        self.assertEqual(infer_quant_task("茅台底仓做T回测一下"), "t0_backtest")
        self.assertIn("t0_backtest", AVAILABLE_TASKS)

    def test_engine_offline_backtest(self):
        bars = [_bar(f"d{i}", 100, 108, 95, 101) for i in range(30)]
        with patch("skills.common.history.fetch_daily_bars", return_value=(bars, "mock")), patch(
            "skills.common.quote_api.StockAPI.query",
            return_value={"success": True, "stock_code": "600519", "stock_name": "茅台"},
        ):
            out = QuantEngine().run(
                {
                    "task": "t0_backtest",
                    "stock_code": "600519",
                    "lookback": 40,
                    "initial_shares": 1000,
                    "direction": "long_t",
                    "path_mode": "first_touch",
                    "use_atr": False,
                }
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("task"), "t0_backtest")


class TestDualYDirection(unittest.TestCase):
    def test_load_dual_y_alias(self):
        cfg = load_t0_rules({"direction": "yhat"})
        self.assertEqual(cfg["direction"], "dual_y")
        self.assertIn("y_trade_floor", cfg)
        self.assertEqual(cfg["y_tau_enter"], 0.25)
        self.assertEqual(cfg["y_score_source"], "compute")

    def test_open_decision_quote_uses_open_not_close(self):
        from core.t0.score_policy import open_decision_quote

        day = _bar("2026-01-10", 98, 110, 90, 105)
        prev = _bar("2026-01-09", 100, 101, 99, 100)
        q = open_decision_quote(day, prev, code="600000")
        self.assertEqual(q["open"], 98)
        self.assertEqual(q["price_raw"], 98)
        self.assertEqual(q["prev_close"], 100)
        self.assertAlmostEqual(q["change_raw"], -2.0, places=4)

    def test_compute_scores_from_bars_pit(self):
        from core.t0.score_policy import (
            clear_score_model_cache,
            compute_scores_from_bars,
            scores_have_any,
        )

        clear_score_model_cache()
        hist = []
        px = 100.0
        for i in range(30):
            d = f"2025-11-{(i % 28) + 1:02d}"
            o = px
            c = px * (1.0 + (0.01 if i % 3 else -0.005))
            hist.append(_bar(d, o, max(o, c) * 1.01, min(o, c) * 0.99, c))
            px = c
        day = _bar("2025-12-01", px * 1.02, px * 1.05, px * 0.98, px * 1.01)
        sc = compute_scores_from_bars("600000", hist, day_bar=day, fuse_intraday=True)
        # 无模型时可能空；有簇/全局模型时应有 ŷ
        if scores_have_any(sc):
            self.assertIsNotNone(sc.get("y_eod") or sc.get("y_trade") or sc.get("y_tau"))
            self.assertEqual(sc.get("_score_source"), "compute")

    def test_compute_scores_map_shares_pool_gaps(self):
        from core.t0.score_policy import (
            clear_score_model_cache,
            compute_scores_map_from_bars,
            scores_have_any,
        )

        clear_score_model_cache()

        def _hist(n=25, start=40.0):
            bars = []
            px = start
            for i in range(n):
                o = px
                c = px * (1.0 + (0.008 if i % 2 == 0 else -0.004))
                bars.append(_bar(f"2025-10-{(i % 28) + 1:02d}", o, max(o, c) * 1.01, min(o, c) * 0.99, c))
                px = c
            return bars, px

        h1, px1 = _hist(25, 40)
        h2, px2 = _hist(25, 55)
        specs = [
            {"code": "600000", "hist_bars": h1, "day_bar": _bar("2025-11-01", px1 * 1.03, px1 * 1.04, px1, px1 * 1.02)},
            {"code": "600001", "hist_bars": h2, "day_bar": _bar("2025-11-01", px2 * 0.97, px2, px2 * 0.95, px2 * 0.98)},
        ]
        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            side_effect=lambda code, hist, **kw: {
                "y_eod": 0.5,
                "y_tau": 0.4 if (kw.get("pool_gaps") and len(kw.get("pool_gaps") or []) >= 2) else None,
                "y_trade": 0.5,
                "y_on": 0.1,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "compute",
                "_pool_n": len(kw.get("pool_gaps") or []),
            },
        ) as mocked:
            out = compute_scores_map_from_bars(specs)
            self.assertEqual(mocked.call_count, 2)
            # 两次调用都应带上截面 gaps
            for call in mocked.call_args_list:
                self.assertGreaterEqual(len(call.kwargs.get("pool_gaps") or []), 2)
            self.assertTrue(scores_have_any(out.get("600000")))

    def test_compute_fallback_skips_ledger(self):
        from core.t0.score_policy import resolve_scores_for_code

        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            return_value={
                "y_eod": None,
                "y_tau": None,
                "y_trade": None,
                "y_on": None,
                "y_nowcast": None,
                "y_check": None,
                "eod_trust": None,
            },
        ), patch(
            "core.t0.score_policy.load_scores_for_code_date",
            side_effect=AssertionError("ledger must not be used on compute fallback"),
        ), patch(
            "core.t0.score_policy._scores_from_live_book",
            return_value={
                "y_eod": 0.2,
                "y_tau": 0.3,
                "y_trade": 0.2,
                "y_on": None,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "live_book",
            },
        ):
            sc = resolve_scores_for_code(
                "600000",
                hist_bars=[_bar("2026-01-01", 1, 1, 1, 1)] * 20,
                day_bar=_bar("2026-01-02", 1, 1, 1, 1),
                source="compute",
                allow_fallback=True,
            )
            self.assertEqual(sc.get("_score_source"), "live_book")
            self.assertAlmostEqual(float(sc.get("y_tau") or 0), 0.3)

    def test_backtest_dual_y_compute_not_ledger(self):
        from core.t0.score_policy import clear_score_model_cache

        clear_score_model_cache()
        bars = []
        px = 50.0
        for i in range(40):
            d = f"2026-01-{(i % 28) + 1:02d}"
            o = px
            h = o * 1.03
            l = o * 0.97
            c = o * (1.005 if i % 2 == 0 else 0.995)
            bars.append(_bar(d, o, h, l, c))
            px = c
        minute_by_date = {str(b["date"]): _mins_hl(bar=b) for b in bars}
        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            return_value={
                "y_eod": 0.5,
                "y_tau": 0.8,
                "y_trade": 0.4,
                "y_on": 0.1,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "compute",
            },
        ) as mocked:
            out = backtest_t0_on_bars(
                bars,
                initial_shares=1000,
                initial_cost=50,
                rules=_rules(
                    direction="dual_y",
                    min_range_pct=0.5,
                    y_tau_enter=0.25,
                    path_mode="first_touch",
                    use_atr=False,
                ),
                stock_code="600000",
                initial_cash=50000,
                minute_by_date=minute_by_date,
            )
            self.assertTrue(out.get("success"))
            self.assertGreater(mocked.call_count, 0)

    def test_dual_y_missing_scores_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0),
            scores={},
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("signal_skip"))
        self.assertIn("缺", out.get("reason") or "")

    def test_dual_y_trade_floor_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0, y_trade_floor=0.15),
            scores={"y_trade": 0.05, "y_tau": 0.8, "y_eod": 0.1},
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out.get("skipped"))
        self.assertIn("|y_trade|", out.get("reason") or "")

    def test_dual_y_trade_floor_allows_large_negative(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0, y_trade_floor=0.15),
            scores={"y_trade": -0.5, "y_tau": 0.8, "y_eod": 0.1},
            minute_bars=_mins_hl(bar=bar))
        self.assertNotIn("预期幅度不足", out.get("reason") or "")

    def test_dual_y_conflict_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                min_range_pct=1.0,
                y_eod_prior=0.35,
                y_tau_enter=0.25,
            ),
            scores={"y_trade": 0.2, "y_tau": 0.8, "y_eod": -0.5},
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out.get("skipped"))
        self.assertIn("冲突", out.get("reason") or "")

    def test_dual_y_long_t_and_cover(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                must_cover_same_day=False,
                y_tau_enter=0.25,
            ),
            scores={
                "y_trade": 0.3,
                "y_tau": 0.8,
                "y_eod": 0.4,
                "y_on": 0.2,
            },
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "long_t")
        self.assertTrue((out.get("cover_policy") or {}).get("must_cover"))
        self.assertGreater(out.get("sold_qty") or 0, 0)

    def test_dual_y_reverse_t(self):
        bar = _bar("2026-01-10", 100, 105, 96, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                y_tau_enter=0.25,
            ),
            scores={
                "y_trade": 0.2,
                "y_tau": -0.8,
                "y_eod": -0.5,
                "y_on": 0.1,
            },
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "reverse_t")

    def test_dual_y_trend_map_inverts_positive_tau(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": 0.4},
            cfg={"y_tau_enter": 0.25, "y_tau_map": "trend"},
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "reverse_t")
        self.assertIn("trend", out.get("direction_reason") or "")

    def test_dual_y_fixed_long_ignores_negative_tau(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": -0.8, "y_eod": -0.5},
            cfg={"y_tau_enter": 0.25, "y_tau_map": "fixed_long"},
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "long_t")

    def test_load_y_tau_map_alias(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"y_tau_map": "follow"})
        self.assertEqual(cfg["y_tau_map"], "trend")

    def test_dual_y_overnight_allow_when_yon_aligns(self):
        from core.t0.score_policy import resolve_cover_policy

        cover = resolve_cover_policy(
            scores={"y_on": -1.5, "y_trade": 0.3},
            direction="long_t",
            cfg={"must_cover_same_day": False, "y_on_allow": 1.2, "y_trade_floor": 0.15},
        )
        self.assertFalse(cover["must_cover"])
        self.assertTrue(cover["allow_overnight"])


class TestT0Viz(unittest.TestCase):
    def test_backtest_includes_viz(self):
        bars = [_bar("d0", 100, 105, 98, 101) for _ in range(25)]
        for i, b in enumerate(bars):
            b["date"] = f"2026-01-{i+1:02d}"
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=0.5,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
            ),
            compare_optimistic=False,
            stock_code="688047",
        )
        self.assertTrue(report.get("success"))
        self.assertIn("viz", report)
        self.assertIn("skip_categories", report["viz"])
        self.assertIn("daily_activity", report["viz"])

    def test_classify_skip_reason(self):
        from core.t0.viz import classify_t0_skip_reason

        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade（即时算分失败）"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade 快照"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116<0.25 横盘跳过"),
            "y_tau_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116%<0.25% 横盘跳过"),
            "y_tau_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_check=conflict 禁止做T"),
            "conflict",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_trade|=0.085%<0.15% 预期幅度不足"),
            "y_trade_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_trade=-0.381%<floor-0.15% 资格不足"),
            "y_trade_weak",
        )

    def test_build_and_merge_viz(self):
        from core.t0.viz import build_t0_viz_payload, merge_t0_viz_payloads

        days = [
            {
                "date": "2026-08-11",
                "skipped": True,
                "reason": "dual_y：缺 y_eod/y_τ/y_trade 快照",
                "signal_skip": True,
                "close": 10.5,
                "shares": 1000,
            },
            {
                "date": "2026-08-12",
                "direction": "long_t",
                "sold_qty": 100,
                "covered_qty": 100,
                "pnl": 150.0,
                "exposure_pnl": 0,
                "direction_score": 0.55,
                "close": 10.8,
                "shares": 1000,
            },
        ]
        viz = build_t0_viz_payload(days, stock_code="688047", stock_name="龙芯", initial_shares=1000)
        self.assertEqual(viz["trade_count"], 1)
        self.assertEqual(viz["skip_count"], 1)
        self.assertEqual(len(viz["cumulative_pnl"]), 1)
        self.assertEqual(viz["cumulative_pnl"][0]["cum_pnl"], 150.0)
        self.assertTrue(any(c["id"] == "missing_scores" for c in viz["skip_categories"]))
        sc = viz["stock_contrib"][0]
        self.assertEqual(sc["skip_top_id"], "missing_scores")
        self.assertIn("skip_breakdown", sc)
        self.assertEqual(sc["skip_breakdown"][0]["id"], "missing_scores")
        self.assertIn("color", sc["skip_breakdown"][0])
        self.assertEqual(sc["shares"], 1000)
        self.assertEqual(sc["window_start_date"], "2026-08-11")
        self.assertEqual(sc["window_end_date"], "2026-08-12")
        self.assertAlmostEqual(float(sc["start_price"]), 10.5, places=2)
        self.assertAlmostEqual(float(sc["end_price"]), 10.8, places=2)
        self.assertAlmostEqual(float(sc["return_pct"]), 150.0 / 10500.0 * 100.0, places=2)

        viz2 = build_t0_viz_payload(
            [
                {
                    "date": "2026-08-13",
                    "direction": "reverse_t",
                    "bought_qty": 100,
                    "sold_back_qty": 100,
                    "pnl": -20.0,
                    "exposure_pnl": 0,
                    "direction_score": -0.4,
                }
            ],
            stock_code="600519",
            stock_name="茅台",
        )
        merged = merge_t0_viz_payloads([viz, viz2])
        self.assertEqual(merged["trade_count"], 2)
        self.assertEqual(len(merged["stock_contrib"]), 2)
        self.assertEqual(merged["cumulative_pnl"][-1]["cum_pnl"], 130.0)
        self.assertIn("summary", merged)
        self.assertIn("y_tau_scatter", viz)
        self.assertEqual(viz["summary"]["y_tau_enter"], 0.25)

    def test_scatter_y_tau_not_direction_score_on_y_trade_skip(self):
        from core.t0.viz import build_t0_viz_payload, extract_scores

        day = {
            "date": "2026-08-17",
            "skipped": True,
            "signal_skip": True,
            "direction_score": 0.05,
            "direction_reason": "dual_y：|y_trade|=0.05%<0.15% 预期幅度不足",
            "skip_category": "y_trade_weak",
            "direction_features": {
                "y_tau": 0.132,
                "y_trade": 0.05,
            },
        }
        sc = extract_scores(day)
        self.assertAlmostEqual(sc["y_tau"], 0.132, places=3)
        self.assertAlmostEqual(sc["y_trade"], 0.05, places=3)

        viz = build_t0_viz_payload([day], stock_code="601898")
        scatter = viz.get("y_tau_scatter") or []
        self.assertEqual(len(scatter), 1)
        self.assertAlmostEqual(float(scatter[0]["y_tau"]), 0.132, places=3)
        self.assertEqual(scatter[0]["outcome"], "signal_skip")
        self.assertEqual(scatter[0].get("skip_category"), "y_trade_weak")

    def test_attach_compare_to_viz(self):
        from core.t0.viz import attach_compare_to_viz, build_t0_viz_payload

        report = {
            "t0_pnl_total": 100.0,
            "optimistic_compare": {
                "t0_pnl_total": 150.0,
                "delta_pnl": 50.0,
                "delta_pnl_ratio_pct": 50.0,
            },
            "participate_rate_pct": 12.5,
            "viz": build_t0_viz_payload([]),
        }
        attach_compare_to_viz(report)
        self.assertIn("compare", report["viz"])
        self.assertEqual(report["viz"]["compare"]["optimistic_pnl"], 150.0)
        self.assertEqual(report["viz"]["summary"]["participate_rate_pct"], 12.5)


class TestT0Api(unittest.TestCase):
    def test_t0_backtest_api_mocked(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "task": "t0_backtest",
            "stock_code": "600519",
            "t0_trade_days": 5,
            "t0_pnl_total": 120.5,
            "t0_win_rate_pct": 60.0,
        }
        with patch.object(deps.quant, "run_t0_backtest", return_value=mock):
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/t0-backtest",
                json={"code": "茅台", "from_paper": False},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

    def test_index_has_t0_controls(self):
        path = os.path.join(ROOT, "web", "static", "partials", "follow_panel.html")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("paper-t0-backtest", text)
        self.assertIn("paper-t0-action-status", text)
        self.assertIn("paper-t0-run", text)
        self.assertIn("paper-t0-confirm", text)
        self.assertIn("paper-t0-worker-enabled", text)
        self.assertIn("paper-t0-worker-m-lastrun", text)
        self.assertIn("paper-t0-worker-trades", text)
        self.assertIn("paper-t0-run", text)
        self.assertIn("dual_y", text)
        self.assertIn("手动预演", text)
        self.assertIn("自动做 T 后台进程", text)
        self.assertNotIn("paper-t0-auto-enabled", text)
        self.assertNotIn("paper-t0-auto-run", text)
        self.assertNotIn("启用自动做T", text)
        self.assertNotIn("落账控制", text)
        self.assertIn("持仓操作流水", text)
        self.assertIn("paper-holdings-table", text)
        self.assertNotIn('id="paper-rebalance"', text)
        self.assertNotIn("跑一日", text)


class TestT0HoldingsVirtualSizing(unittest.TestCase):
    def test_align_daily_bars_to_minute_drops_uncovered(self):
        from quant.research.t0_backtest import _align_daily_bars_to_minute

        bars = [
            {"date": "2026-07-01", "close": 10},
            {"date": "2026-07-02", "close": 11},
            {"date": "2026-07-03", "close": 12},
            {"date": "2026-07-04", "close": 13},
        ]
        mins = {
            "2026-07-02": [{"t": 1}, {"t": 2}],
            "2026-07-03": [{"t": 1}, {"t": 2}],
            "2026-07-04": [{"t": 1}],  # <2 → 不可用
        }
        aligned, meta = _align_daily_bars_to_minute(bars, mins)
        self.assertTrue(meta.get("aligned"))
        self.assertEqual([b["date"] for b in aligned], ["2026-07-02", "2026-07-03"])
        self.assertEqual(meta.get("bars_before"), 4)
        self.assertEqual(meta.get("bars_after"), 2)

    def test_holdings_uses_virtual_shares_and_cash(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "t0_pnl_total": 1000.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 5,
            "t0_cover_days": 5,
            "skip_days": 10,
            "signal_skip_days": 2,
            "long_t_days": 5,
            "reverse_t_days": 0,
            "long_t_pnl": 1000.0,
            "reverse_t_pnl": 0.0,
            "long_t_cover_days": 5,
            "reverse_t_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 15,
            "missing_minute_days": 0,
            "hold_mv_start": 1_000_000.0,
            "t0_win_days": 3,
            "t0_loss_days": 2,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)) as mocked:
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台", "shares": 200, "cost": 34}],
                lookback=20,
                compare_optimistic=False,
            )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], m.T0_BT_VIRTUAL_SHARES)
        self.assertEqual(kwargs["initial_cash"], m.T0_BT_VIRTUAL_CASH)
        self.assertIsNone(kwargs["initial_cost"])
        self.assertTrue(out.get("virtual_sizing"))
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), 0.1)
        self.assertIn("虚拟每票", out.get("scope_label") or "")


class TestIntradaySkipLogic(unittest.TestCase):
    def test_should_process_skips_done_without_new_bar(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "done", "last_bar_ts": "2026-08-24 10:05:00"}
        self.assertFalse(
            should_process_intraday_stock(st, "2026-08-24 10:05:00", force_session_close=False)
        )
        self.assertFalse(
            should_process_intraday_stock(st, "2026-08-24 10:10:00", force_session_close=False)
        )

    def test_should_process_on_new_bar(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "idle", "last_bar_ts": "2026-08-24 10:05:00"}
        self.assertTrue(
            should_process_intraday_stock(st, "2026-08-24 10:10:00", force_session_close=False)
        )

    def test_force_session_close_retries_non_terminal(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "pending_cover", "last_bar_ts": "2026-08-24 14:55:00"}
        self.assertTrue(
            should_process_intraday_stock(st, "2026-08-24 14:55:00", force_session_close=True)
        )
        self.assertFalse(
            should_process_intraday_stock(
                {"phase": "done", "last_bar_ts": "2026-08-24 15:00:00"},
                "2026-08-24 15:00:00",
                force_session_close=True,
            )
        )


class TestT0AutoWorker(unittest.TestCase):
    def test_worker_tick_waits_outside_session(self):
        from core.t0.auto_worker import T0AutoWorker

        w = T0AutoWorker()
        with patch(
            "core.t0.intraday.session_in_market",
            return_value=(False, "2026-08-24"),
        ), patch(
            "core.t0.auto_worker._session_closed",
            return_value=(False, "2026-08-24"),
        ):
            w._enabled = True
            w._tick()
        self.assertIn("待机", w.status()["last_tick_message"])

    def test_worker_tick_interval_is_five_minutes(self):
        from core.t0.auto_worker import TICK_INTERVAL_SEC
        from core.t0.config import T0_INTRADAY_TICK_SEC

        self.assertEqual(TICK_INTERVAL_SEC, T0_INTRADAY_TICK_SEC)
        self.assertEqual(TICK_INTERVAL_SEC, 300.0)

    def test_set_worker_starts_and_stops(self):
        import tempfile
        from core.t0.auto_worker import t0_auto_worker
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.t0.auto_worker._worker_config_path") as path_mock, patch(
                "services.paper_service.PaperService.run_t0_intraday_tick",
                return_value={"ok": True, "trade_count": 0, "skipped": True, "reason": "mock"},
            ), patch(
                "core.t0.intraday.session_in_market",
                return_value=(False, "2026-08-24"),
            ), patch(
                "core.t0.auto_worker._session_closed",
                return_value=(False, "2026-08-24"),
            ):
                path = os.path.join(tmp, "t0_auto_worker.json")
                path_mock.return_value = path
                paper_path = os.path.join(tmp, "paper.json")
                svc = PaperService(paper_path)
                svc.init()
                out = svc.set_t0_worker(True)
                self.assertTrue(out["worker"]["running"])
                self.assertTrue(out["t0_auto"]["enabled"])
                svc.set_t0_worker(False)
                self.assertFalse(t0_auto_worker.is_running())
                paper = __import__("core.paper", fromlist=["load_paper"]).load_paper(
                    paper_path
                )
                self.assertFalse(paper["rules"]["t0_auto"]["enabled"])


class TestT0AutoLastRunDetail(unittest.TestCase):
    def test_last_run_stores_compact_results(self):
        import tempfile
        from services.paper_trades import _compact_t0_result_rows, _write_t0_auto_last_run
        from core.paper import load_paper, save_paper

        sample = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "date": "2026-08-24",
                "skipped": True,
                "reason": "振幅不足",
                "direction_used": None,
                "trades": [{"side": "sell", "price": 100, "shares": 100, "at": "10:05"}],
                "minute_bars": 48,
            },
            {
                "stock_code": "000001",
                "stock_name": "平安",
                "date": "2026-08-24",
                "skipped": False,
                "direction_used": "long_t",
                "pnl": 12.5,
                "exposure_pnl": 0,
                "sold_qty": 100,
                "covered_qty": 100,
                "trades": [
                    {"side": "t0_sell", "price": 10.2, "shares": 100, "at": "10:15"},
                    {"side": "t0_buy", "price": 10.0, "shares": 100, "at": "14:30"},
                ],
            },
        ]
        compact = _compact_t0_result_rows(sample)
        self.assertEqual(len(compact), 2)
        self.assertTrue(compact[0].get("skip_category"))
        self.assertEqual(compact[1]["direction"], "long_t")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "rules": {},
                "trades": [],
                "operation_log": [],
            }
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": compact[1]["trades"],
                    "pnl_total": 12.5,
                    "results": sample,
                    "execution": {"t0": {"direction": "dual_y", "y_tau_map": "scalp"}},
                },
                source="paper_t0_auto",
            )
            save_paper(paper, path)
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(lr["source"], "paper_t0_auto")
            self.assertEqual(lr["session_date"], "2026-08-24")
            self.assertEqual(lr["rules"]["direction"], "dual_y")
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "000001")

    def test_last_run_skipped_without_write(self):
        import tempfile
        from services.paper_trades import _write_t0_auto_last_run
        from core.paper import load_paper, save_paper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {"cash": 100000, "holdings": [], "rules": {}, "trades": [], "operation_log": []}
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": [],
                    "results": [{"stock_code": "600519", "skipped": True, "reason": "振幅不足"}],
                },
                source="paper_t0_auto",
            )
            self.assertNotIn("last_run", paper.get("rules", {}).get("t0_auto", {}))


if __name__ == "__main__":
    unittest.main()
