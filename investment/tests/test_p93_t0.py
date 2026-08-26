import os
import sys
import json
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
    """单测默认 first_touch + 关 ATR；关中点追价以免干扰路径用例。"""
    base = {
        "path_mode": "first_touch",
        "use_atr": False,
        "t0_pm_degrade": "",
        "y_block_tau_nowcast_sign": False,
    }
    base.update(kwargs)
    base["path_mode"] = "first_touch"
    return base


class TestT0Core(unittest.TestCase):
    def test_default_fill_mode_trigger(self):
        self.assertEqual(DEFAULT_T0_RULES["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["path_mode"], "first_touch")

    def test_default_t0_rules_match_paper_overlay(self):
        d = load_t0_rules()
        self.assertEqual(d["sell_trigger_pct"], 1.0)
        self.assertEqual(d["buy_trigger_pct"], 1.0)
        self.assertTrue(d["must_cover_same_day"])
        self.assertFalse(d["use_atr"])
        self.assertEqual(d["min_range_pct"], 0.2)
        self.assertEqual(d["y_path_enter"], 2.0)
        self.assertFalse(d["y_nowcast_oc_gate"])
        self.assertEqual(d["y_nowcast_enter"], 3.0)
        self.assertEqual(d["y_path_abandon_bars"], 12)
        self.assertEqual(d["y_trade_floor"], 0.02)
        self.assertEqual(d["y_tau_map"], "trend")
        self.assertTrue(d["enabled"])

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
        reason = out.get("reason") or ""
        self.assertIn("T+1", reason)
        self.assertIn("可卖旧仓", reason)
        self.assertNotIn("动仓不足", reason)

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

    def test_deferred_eod_allows_afternoon_trigger_buy(self):
        """滚动前缀过关后卖；第二腿应能等到下午 trigger，而非前缀末 eod_cover。"""
        d = "2024-01-03"
        bar = _bar(d, 100, 106, 99, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.3, 99.7, 100),
                (940, 100, 100.3, 99.7, 100),
                (945, 100, 100.3, 99.7, 100),
                (950, 100, 100.3, 99.7, 100),
                (955, 100, 100.3, 99.7, 100),
                (1000, 100, 100.3, 99.7, 100),
                (1005, 100, 100.3, 99.7, 100),
                (1010, 100, 106, 100, 105),
                (1015, 105, 105.2, 104.8, 105),
                (1020, 105, 105.2, 104.8, 105),
                (1100, 105, 105.2, 104.8, 105),
                (1430, 101, 101.2, 100.3, 100.5),
                (1500, 100, 100, 99.5, 100),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=mins,
            rules=_rules(
                direction="long_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        trades = out.get("trades") or []
        buys = [t for t in trades if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1, trades)
        self.assertEqual(buys[0].get("leg_kind"), "trigger")
        self.assertIn("14:30", str(buys[0].get("at") or ""))
        self.assertGreaterEqual(int(out.get("prefix_bars") or 0), 12)

    def test_directional_amplitude_waits_for_long_t_upside(self):
        """总量振幅过关但上移不足时不触达；冲高后再卖，下午买回。"""
        d = "2024-01-03"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.5, 99.8),
                (940, 100, 100.1, 98.0, 98.5),
                (945, 100, 100.1, 96.0, 96.5),
                (950, 100, 100.1, 94.0, 94.5),
                (1000, 94.5, 106, 94.5, 105),
                (1430, 101, 101.2, 100.3, 100.5),
                (1500, 100, 100, 99.5, 100),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=mins,
            rules=_rules(
                direction="long_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        trades = out.get("trades") or []
        sells = [t for t in trades if str(t.get("side", "")).endswith("sell")]
        buys = [t for t in trades if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(sells), 1)
        self.assertEqual(len(buys), 1)
        self.assertIn("10:00", str(sells[0].get("at") or ""))
        self.assertEqual(buys[0].get("leg_kind"), "trigger")

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

    def test_paper_tplus1_uses_session_not_stale_bar_date(self):
        """凌晨预演：日线仍停昨收时，T+1 应按会话日解冻昨日买入。"""
        paper = {
            "cash": 200000,
            "holdings": [
                {
                    "stock_code": "000938",
                    "stock_name": "紫光股份",
                    "shares": 500,
                    "cost": 20,
                    "lots": [
                        {
                            "shares": 500,
                            "bought_date": "2026-08-25",
                            "bought_at": "2026-08-25T10:00:00",
                        }
                    ],
                }
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.9,
                    "sell_trigger_pct": 0.5,
                    "buy_trigger_pct": 0.5,
                    "direction": "reverse_t",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.5,
                    "must_cover_same_day": True,
                }
            },
        }
        # 昨收 bar（会话已是次日）
        bar = _bar("2026-08-25", 20, 21, 19, 20.5)
        mins = _mins_lh(bar=bar)
        with patch("core.paper.tplus1.session_date", return_value="2026-08-26"):
            out = simulate_t0_on_holdings(
                paper,
                bars_by_code={"000938": bar},
                minute_bars_by_code={"000938": mins},
                dry_run=True,
            )
        row = (out.get("results") or [None])[0]
        self.assertIsNotNone(row)
        reason = str(row.get("reason") or "")
        self.assertNotIn("可卖旧仓 0", reason)
        self.assertNotIn("可卖0", reason)
        # 可卖已解冻；是否成交取决于路径，但不应因 T+1=0 跳过
        if row.get("skipped"):
            self.assertNotIn("T+1", reason)

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
        self.assertEqual(cfg["y_tau_enter"], 0.02)
        self.assertEqual(cfg["y_tau_enter_strong"], 0.02)
        self.assertEqual(cfg["y_score_source"], "compute")

    def test_y_tau_enter_strong_migrates_into_enter(self):
        cfg = load_t0_rules({"y_tau_enter": 0.4, "y_tau_enter_strong": 0.6})
        self.assertEqual(cfg["y_tau_enter"], 0.6)
        self.assertEqual(cfg["y_tau_enter_strong"], 0.6)
        low_strong = load_t0_rules({"y_tau_enter": 0.5, "y_tau_enter_strong": 0.3})
        self.assertEqual(low_strong["y_tau_enter"], 0.5)
        self.assertEqual(low_strong["y_tau_enter_strong"], 0.5)

    def test_y_tau_enter_floor_allows_001(self):
        cfg = load_t0_rules({"y_tau_enter": 0.01})
        self.assertEqual(cfg["y_tau_enter"], 0.01)
        clamped = load_t0_rules({"y_tau_enter": 0.0})
        self.assertEqual(clamped["y_tau_enter"], 0.01)

    def test_open_decision_quote_uses_open_not_close(self):
        from core.t0.score_policy import open_decision_quote

        day = _bar("2026-01-10", 98, 110, 90, 105)
        prev = _bar("2026-01-09", 100, 101, 99, 100)
        q = open_decision_quote(day, prev, code="600000")
        self.assertEqual(q["open"], 98)
        self.assertEqual(q["price_raw"], 98)
        self.assertEqual(q["prev_close"], 100)
        self.assertAlmostEqual(q["change_raw"], -2.0, places=4)

    def test_enrich_quote_path_price_uses_day_close(self):
        from core.t0.score_policy import _enrich_quote_path_price, open_decision_quote

        day = _bar("2026-01-10", 98, 110, 90, 105)
        prev = _bar("2026-01-09", 100, 101, 99, 100)
        q = open_decision_quote(day, prev, code="600000")
        enriched = _enrich_quote_path_price(q, day, code="600000")
        self.assertEqual(enriched["open"], 98)
        self.assertEqual(enriched["prev_close"], 100)
        self.assertEqual(enriched["price_raw"], 105)
        self.assertAlmostEqual(enriched["change_raw"], -2.0, places=4)

    def test_scores_from_item_keeps_yon_path_separate(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "predicted_score_on": 0.2,
                "y_on": 0.2,
                "y_on_path": 3.5,
                "predicted_score_tau": 0.8,
            }
        )
        self.assertAlmostEqual(sc["y_on"], 0.2)
        self.assertAlmostEqual(sc["y_on_path"], 3.5)
        packed = __import__(
            "core.t0.score_policy", fromlist=["pack_day_scores"]
        ).pack_day_scores(sc)
        self.assertAlmostEqual(packed["y_on"], 0.2)
        self.assertAlmostEqual(packed["y_on_path"], 3.5)

    def test_eod_factor_bar_limit_matches_score_stock(self):
        """EOD 窗必须与 score_stock.fetch_daily_bars(limit=40) 一致，避免周线切桶漂移。"""
        import inspect

        from core.signal.score_stock import fetch_daily_bars
        from core.t0.score_policy import _EOD_FACTOR_BAR_LIMIT

        self.assertEqual(int(_EOD_FACTOR_BAR_LIMIT), 40)
        params = inspect.signature(fetch_daily_bars).parameters
        self.assertEqual(params["limit"].default, 40)

    def test_tip_fields_include_features_on(self):
        from core.t0.score_policy import tip_fields_from_item

        tip = tip_fields_from_item(
            {
                "features_on": {
                    "ret_oc": -0.5,
                    "ret_cc": -1.2,
                    "gap_pct": -0.8,
                    "noise": 1,
                },
                "formula_terms_on": {
                    "intercept": 0.0,
                    "total": -0.7,
                    "terms": [{"key": "ret_oc", "contrib": -0.3}],
                },
            }
        )
        self.assertEqual(tip.get("features_on", {}).get("ret_oc"), -0.5)
        self.assertNotIn("noise", tip.get("features_on") or {})
        self.assertIsNotNone(tip.get("formula_terms_on"))

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
            feats_on = sc.get("features_on") or {}
            path_feats = sc.get("features_on_path") or {}
            # 开盘决策：price=open → ret_oc≈0；路径复盘用 close，ret_oc 非零
            if feats_on.get("ret_oc") is not None:
                self.assertAlmostEqual(float(feats_on["ret_oc"]), 0.0, places=3)
            if path_feats.get("ret_oc") is not None:
                self.assertNotAlmostEqual(float(path_feats["ret_oc"]), 0.0, places=4)
            if sc.get("y_on_path") is not None and sc.get("y_on") is not None:
                # 旁路路径价不得覆盖决策 y_on（二者可同可异，但字段须并存）
                self.assertIn("y_on_path", sc)

    def test_compute_aligns_trade_with_holdings_blend(self):
        """即时算分后 align：y_trade 须与 predicted_score_blend 一致，双头不得塌成 y_eod。"""
        from core.t0.score_policy import scores_from_item

        item = {
            "predicted_score": 2.5,
            "predicted_score_eod": 2.5,
            "predicted_score_blend": 2.5,
            "predicted_score_tau": -0.04,
            "gap_pct": 0.26,
            "dual_score_window": "intraday",
            "dual_score_weights": {
                "w_eod": 0.5,
                "w_tau": 0.0,
                "tau_in_trade": False,
                "window": "eod_next",
            },
        }
        from core.signal.dual_score import align_trade_score_fields

        align_trade_score_fields(item, write_score=False, refresh_window=False)
        sc = scores_from_item(item)
        self.assertAlmostEqual(sc["y_eod"], 2.5, places=4)
        self.assertNotAlmostEqual(sc["y_trade"], 2.5, places=3)
        self.assertAlmostEqual(float((sc.get("dual_score_weights") or {}).get("w_tau") or 0), 0.5, places=3)

    def test_scores_from_item_prefers_blend_for_trade(self):
        """双头：predicted_score=ŷ_EOD，ŷ_trade 须取 blend，不能塌成与 y_eod 相同。"""
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "predicted_score": 3.29,
                "predicted_score_eod": 3.29,
                "predicted_score_blend": 1.31,
                "predicted_score_tau": 0.16,
                "predicted_score_on": 0.75,
                "predicted_score_nowcast": 2.75,
            }
        )
        self.assertAlmostEqual(sc["y_eod"], 3.29, places=4)
        self.assertAlmostEqual(sc["y_trade"], 1.31, places=4)
        self.assertAlmostEqual(sc["y_tau"], 0.16, places=4)
        self.assertNotAlmostEqual(sc["y_eod"], sc["y_trade"], places=4)

    def test_attach_day_scores_fills_skip_and_features(self):
        """跳过日也要带 scores + direction_features，预演明细才能画 y_*。"""
        from core.t0.score_policy import attach_day_scores

        snap = {
            "y_eod": 1.2,
            "y_tau": -0.3,
            "y_trade": 0.8,
            "y_on": 0.5,
            "y_nowcast": 1.0,
        }
        out = attach_day_scores({"skipped": True, "reason": "横盘"}, snap)
        self.assertTrue(out["skipped"])
        self.assertAlmostEqual(out["scores"]["y_tau"], -0.3, places=4)
        self.assertAlmostEqual(out["direction_features"]["y_eod"], 1.2, places=4)
        # 已有 features 优先，缺项用 snap 补
        out2 = attach_day_scores(
            {"direction_features": {"y_tau": 0.1}},
            {"y_eod": 2.0, "y_tau": 9.0, "y_trade": 0.5},
        )
        self.assertAlmostEqual(out2["direction_features"]["y_tau"], 0.1, places=4)
        self.assertAlmostEqual(out2["direction_features"]["y_eod"], 2.0, places=4)

    def test_attach_eod_tau_realized_labels(self):
        from core.t0.score_policy import attach_day_scores, attach_eod_tau_realized

        day = attach_eod_tau_realized(
            {"scores": {"y_eod": 1.0, "y_tau": 0.5}},
            open_px=10.0,
            close_px=10.2,
            prev_close=10.0,
        )
        self.assertAlmostEqual(day["tau_realized"], 2.0, places=4)
        self.assertAlmostEqual(day["eod_realized"], 2.0, places=4)
        self.assertAlmostEqual(day["scores"]["tau_realized"], 2.0, places=4)
        # attach_day_scores 在有开收时自动写真实值
        out = attach_day_scores(
            {"open": 10.0, "close": 9.9, "prev_close": 10.0},
            {"y_eod": 0.1, "y_tau": -0.2, "y_trade": 0.0},
        )
        self.assertAlmostEqual(out["tau_realized"], -1.0, places=4)
        self.assertAlmostEqual(out["eod_realized"], -1.0, places=4)

    def test_scores_from_item_keeps_formula_terms_for_tip(self):
        """成交明细 tip 需要因子组成；scores 须保留 slim 分项。"""
        from core.t0.score_policy import pack_day_scores, scores_from_item

        sc = scores_from_item(
            {
                "predicted_score": 1.0,
                "predicted_score_eod": 1.0,
                "predicted_score_blend": 0.8,
                "predicted_score_tau": 0.2,
                "score_formula_terms": {
                    "intercept": 0.01,
                    "total": 1.0,
                    "terms": [
                        {
                            "key": "mom3",
                            "label": "动量3",
                            "beta": 0.1,
                            "z": 2.0,
                            "contrib": 0.2,
                        }
                    ],
                },
                "factor_coefficients": {"mom3": 0.1},
                "formula_terms_tau": {
                    "intercept": 0.0,
                    "total": 0.2,
                    "terms": [
                        {
                            "key": "gap_pct",
                            "label": "缺口",
                            "beta": 0.1,
                            "z": 2.0,
                            "contrib": 0.2,
                        }
                    ],
                },
                "features_tau": {"gap_pct": 1.5},
                "gap_pct": 1.5,
            }
        )
        self.assertTrue(sc.get("score_formula_terms", {}).get("terms"))
        self.assertTrue(sc.get("formula_terms_tau", {}).get("terms"))
        packed = pack_day_scores(sc)
        self.assertIsNotNone(packed)
        self.assertTrue(packed.get("score_formula_terms", {}).get("terms"))
        self.assertAlmostEqual(float(packed.get("gap_pct")), 1.5, places=4)

    def test_slim_formula_terms_pins_amihud(self):
        """非流动性 β 往往偏小，slim 时在 limit 内保留（不追加超限）。"""
        from core.t0.score_policy import _TIP_PIN_FACTORS, _slim_formula_terms

        terms = [
            {
                "key": f"f{i}",
                "label": f"F{i}",
                "beta": 0.1,
                "z": 1.0,
                "contrib": 1.0 - i * 0.01,
            }
            for i in range(20)
        ]
        terms.append(
            {
                "key": "amihud",
                "label": "非流动性",
                "beta": 0.02,
                "z": 0.1,
                "contrib": 0.002,
            }
        )
        slim = _slim_formula_terms(
            {"intercept": 0.0, "total": 1.0, "terms": terms},
            limit=10,
            pin_keys=_TIP_PIN_FACTORS,
        )
        keys = [t["key"] for t in (slim or {}).get("terms") or []]
        self.assertIn("amihud", keys)
        self.assertLessEqual(len(keys), 10)

    def test_compute_scores_passes_index_bars_for_rs(self):
        """缺指数时相对强弱会退回开盘涨跌；须传入 index_bars 与数据中心同源。"""
        from unittest.mock import patch

        from core.t0.score_policy import clear_score_model_cache, compute_scores_from_bars

        clear_score_model_cache()
        hist = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 10 + i * 0.1, 11, 9, 10 + i * 0.1)
            for i in range(24)
        ]
        day = _bar("2025-11-01", 12.0, 12.5, 11.5, 12.2)
        idx = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 3000, 3010, 2990, 3000 + i)
            for i in range(24)
        ]
        captured = {}

        def _fake_window(code, window, **kwargs):
            captured["index_bars"] = kwargs.get("index_bars")
            captured["fundamentals"] = kwargs.get("fundamentals")
            return {
                "stock_code": code,
                "sub_scores": {"relative_strength": 85.0, "momentum": 50.0},
                "predicted_score": 0.5,
                "success": True,
            }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item", side_effect=_fake_window
        ), patch(
            "core.t0.score_policy._scoring_models",
            return_value=(None, {}, None),
        ), patch(
            "core.signal.dual_score.attach_dual_score_pit", side_effect=lambda it, **kw: it
        ), patch(
            "core.signal.return_score.apply_predicted_scores_by_model",
            side_effect=lambda items, *a, **k: list(items),
        ):
            compute_scores_from_bars(
                "601600",
                hist,
                day_bar=day,
                index_bars=idx,
                fundamentals={"market_cap": 1e11},
            )
        self.assertTrue(captured.get("index_bars"))
        self.assertEqual(len(captured["index_bars"]), 24)
        self.assertEqual(captured.get("fundamentals", {}).get("market_cap"), 1e11)

    def test_compute_scores_passes_required_factor_keys(self):
        """G69 等组 β 含 amihud；T0 须传 required_factor_keys，否则 regime 会滤掉非流动性。"""
        from unittest.mock import MagicMock, patch

        from core.t0.score_policy import clear_score_model_cache, compute_scores_from_bars

        clear_score_model_cache()
        hist = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 10 + i * 0.1, 11, 9, 10 + i * 0.1)
            for i in range(24)
        ]
        day = _bar("2025-11-01", 12.0, 12.5, 11.5, 12.2)
        captured = {}

        def _fake_window(code, window, **kwargs):
            captured["required_factor_keys"] = kwargs.get("required_factor_keys")
            return {
                "stock_code": code,
                "sub_scores": {"amihud": 32.0, "relative_strength": 85.0},
                "predicted_score": 0.5,
                "success": True,
            }

        mock_model = MagicMock()
        mock_model.coefficients = {"amihud": -0.4, "relative_strength": -0.7, "momentum": 0.1}
        mock_model.explain_prediction.return_value = {
            "intercept": 0.0,
            "total": 0.5,
            "terms": [],
        }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item", side_effect=_fake_window
        ), patch(
            "core.t0.score_policy._scoring_models",
            return_value=(None, {"601600": mock_model}, mock_model),
        ), patch(
            "core.signal.dual_score.attach_dual_score_pit", side_effect=lambda it, **kw: it
        ), patch(
            "core.signal.return_score.apply_predicted_scores_by_model",
            side_effect=lambda items, *a, **k: list(items),
        ), patch(
            "core.t0.score_policy._t0_index_bars_for_score", return_value=[]
        ), patch(
            "core.t0.score_policy._t0_local_fundamentals", return_value=None
        ):
            compute_scores_from_bars("601600", hist, day_bar=day)
        keys = set(captured.get("required_factor_keys") or [])
        self.assertIn("amihud", keys)
        self.assertIn("relative_strength", keys)

    def test_build_tau_pool_by_date_has_ref_and_breadth(self):
        """τ 截面：按日 open 缺口聚合；广度只计正缺口（与训练/刷簿同构）。"""
        from core.t0.score_policy import build_tau_pool_by_date

        bars_a = [
            _bar("2025-11-01", 10, 11, 9, 10.0),
            _bar("2025-11-02", 10.5, 11, 10, 10.4),  # gap +5% → 计入广度
        ]
        bars_b = [
            _bar("2025-11-01", 20, 21, 19, 20.0),
            _bar("2025-11-02", 19.0, 20, 18.5, 19.2),  # gap -5% → 不计（非 |gap|）
        ]
        bars_c = [
            _bar("2025-11-01", 30, 31, 29, 30.0),
            _bar("2025-11-02", 30.2, 31, 30, 30.1),  # gap +0.67% → 不计
        ]
        pool = build_tau_pool_by_date(
            {"600000": bars_a, "600001": bars_b, "600002": bars_c}
        )
        day = pool.get("2025-11-02") or {}
        self.assertEqual(len(day.get("pool_gaps") or []), 3)
        # 仅 +5% 一票命中 → 1/3
        self.assertAlmostEqual(float(day.get("sector_gap_breadth") or -1), 1.0 / 3.0, places=4)
        self.assertIn("600000", day.get("gaps_by_code") or {})
        ref = (day.get("ref_by_code") or {}).get("600000")
        self.assertIsNotNone(ref)

    def test_attach_dual_score_pit_prefers_sector_gap_median(self):
        """gap_vs_sector = gap − sector_gap_median（同行参照，非池中位数）。"""
        from core.signal.dual_score.tau import attach_dual_score_pit

        item = {
            "stock_code": "600000",
            "predicted_score": 1.0,
            "predicted_score_eod": 1.0,
            "_pool_gaps": [1.0, 2.0, 3.0],  # 池中位=2 → 若误用则 gap_vs=0
        }
        quote = {
            "date": "2025-11-02",
            "open": 102.0,
            "prev_close": 100.0,
            "price_raw": 102.0,
        }
        bars = [_bar("2025-11-01", 100, 101, 99, 100.0)]
        out = attach_dual_score_pit(
            item,
            quote=quote,
            bars=bars,
            rem_model_doc=None,
            sector_gap_breadth=0.1,
            sector_gap_median=0.5,
            fuse_intraday=True,
        )
        ft = out.get("features_tau") or {}
        self.assertAlmostEqual(float(ft.get("gap_pct")), 2.0, places=4)
        # 2.0 - 0.5 = 1.5；若用池中位 2.0 则得 0
        self.assertAlmostEqual(float(ft.get("gap_vs_sector")), 1.5, places=4)

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

    def test_dual_y_eod_tau_disagree_does_not_skip(self):
        """y_eod 与 y_τ 异号不跳过（窗口不同：CC vs OC）。"""
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
                fill_mode="optimistic",
            ),
            scores={"y_trade": 0.2, "y_tau": 0.8, "y_eod": -0.5},
            minute_bars=_mins_hl(bar=bar))
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertNotIn("冲突", out.get("reason") or "")
        self.assertEqual(out.get("direction_used"), "reverse_t")

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
                "y_tau": -0.8,
                "y_eod": -0.4,
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
                "y_tau": 0.8,
                "y_eod": 0.5,
                "y_on": 0.1,
            },
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "reverse_t")

    def test_scale_t0_ratio_no_longer_scales(self):
        """动仓比例固定；ŷ 改缩放目标价。"""
        from core.t0.score_policy import scale_t0_ratio

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_ratio_boost_cap": 1.25,
                "y_ratio_cut": 0.75,
                "y_tau_enter": 0.25,
            }
        )
        weak = scale_t0_ratio(0.4, {"y_trade": 0.10}, cfg)
        strong = scale_t0_ratio(0.4, {"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(weak, 0.4)
        self.assertAlmostEqual(strong, 0.4)

    def test_t0_confidence_scale_trade_and_triggers(self):
        from core.t0.score_policy import scale_t0_triggers, t0_confidence_scale

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_ratio_cut": 0.75,
                "y_ratio_boost_cap": 2.0,
                "y_tau_enter": 0.25,
                "y_ratio_tau_soft_band": 0.0,
            }
        )
        weak = t0_confidence_scale({"y_trade": 0.10}, cfg)
        strong = t0_confidence_scale({"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(weak, 0.75, places=3)
        self.assertAlmostEqual(strong, 2.0, places=3)
        trig = scale_t0_triggers(2.0, 1.5, {"y_trade": 0.10}, cfg)
        self.assertAlmostEqual(trig["trigger_scale"], 0.75, places=3)
        self.assertAlmostEqual(trig["sell_trigger_pct"], 1.5, places=3)
        self.assertAlmostEqual(trig["buy_trigger_pct"], 1.125, places=3)
        self.assertAlmostEqual(trig["sell_trigger_pct_base"], 2.0, places=3)
        trig_s = scale_t0_triggers(2.0, 1.5, {"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(trig_s["trigger_scale"], 2.0, places=3)
        self.assertAlmostEqual(trig_s["sell_trigger_pct"], 4.0, places=3)

    def test_t0_confidence_scale_tau_and_eod_align(self):
        from core.t0.score_policy import t0_confidence_scale

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_tau_enter": 0.25,
                "y_eod_prior": 0.35,
                "y_ratio_cut": 0.75,
                "y_ratio_tau_soft_band": 0.0,
                "y_ratio_eod_align_boost": 1.10,
            }
        )
        # 中等 τ：strength < 1；eod 同向略抬
        mid = t0_confidence_scale(
            {"y_trade": 0.30, "y_tau": -0.50, "y_eod": -0.40},
            cfg,
        )
        no_eod = t0_confidence_scale(
            {"y_trade": 0.30, "y_tau": -0.50, "y_eod": 0.05},
            cfg,
        )
        self.assertGreater(mid, no_eod)
        self.assertLessEqual(mid, 1.0)
        self.assertGreaterEqual(no_eod, 0.75)

    def test_dual_y_scaled_trigger_vs_weak_trade(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        rules = _rules(
            direction="dual_y",
            path_mode="first_touch",
            min_range_pct=1.0,
            sell_trigger_pct=2.0,
            buy_trigger_pct=1.5,
            fill_mode="optimistic",
            t0_ratio=0.4,
            y_tau_enter=0.25,
            y_trade_floor=0.15,
            y_ratio_cut=0.75,
            y_ratio_tau_soft_band=0.0,
        )
        strong = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={"y_trade": 1.20, "y_tau": -0.80, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        weak = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={"y_trade": 0.20, "y_tau": -0.80, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        self.assertTrue(strong["success"])
        self.assertTrue(weak["success"])
        self.assertFalse(strong.get("skipped"))
        self.assertFalse(weak.get("skipped"))
        # 动仓固定；弱信号压低目标价
        self.assertAlmostEqual(float(strong.get("t0_ratio") or 0), float(weak.get("t0_ratio") or 0))
        self.assertEqual(strong.get("sold_qty"), weak.get("sold_qty"))
        self.assertGreater(float(strong.get("trigger_scale") or 0), float(weak.get("trigger_scale") or 0))
        self.assertGreater(
            float(strong.get("sell_trigger_pct") or 0),
            float(weak.get("sell_trigger_pct") or 0),
        )

    def test_intraday_setup_applies_scaled_triggers(self):
        from core.t0.intraday import _intraday_setup

        bar = _bar("2026-01-10", 100, 105, 98, 101)
        mins = _mins_hl(bar=bar)
        holding = {"shares": 1000, "cost": 100, "stock_name": "测试"}
        cfg = load_t0_rules(
            {
                "direction": "dual_y",
                "use_atr": False,
                "min_range_pct": 1.0,
                "fill_mode": "optimistic",
                "t0_ratio": 0.4,
                "sell_trigger_pct": 2.0,
                "buy_trigger_pct": 1.5,
                "y_tau_enter": 0.25,
                "y_trade_floor": 0.15,
                "y_eod_prior": 0.35,
                "y_ratio_cut": 0.75,
                "y_ratio_tau_soft_band": 0.0,
                "y_block_tau_nowcast_sign": False,
                "t0_pm_degrade": "",
            }
        )
        out = _intraday_setup(
            code="000001",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=1000,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores={"y_trade": 2.0, "y_tau": -1.5, "y_eod": -0.80},
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"))
        day = out.get("day_result") or {}
        self.assertAlmostEqual(float(day.get("t0_ratio") or 0), 0.4, places=3)
        self.assertEqual(day.get("sold_qty"), 400)
        self.assertAlmostEqual(float(day.get("trigger_scale") or 0), 2.0, places=2)
        self.assertAlmostEqual(float(day.get("sell_trigger_pct") or 0), 4.0, places=2)
        # 落账明细 y_* 列依赖 day.scores / direction_features
        scores = day.get("scores") or {}
        self.assertAlmostEqual(float(scores.get("y_tau")), -1.5, places=2)
        self.assertAlmostEqual(float(scores.get("y_trade")), 2.0, places=2)
        self.assertAlmostEqual(float(scores.get("y_eod")), -0.80, places=2)
        feats = day.get("direction_features") or {}
        self.assertAlmostEqual(float(feats.get("y_tau")), -1.5, places=2)
        self.assertTrue(day.get("direction_reason"))

    def test_intraday_setup_defers_eod_cover_mid_morning(self):
        """盘中前缀有第一腿时不得把下一根 5m 当成收盘强平。"""
        from core.t0.intraday import _intraday_setup, process_holding_intraday

        d = "2026-08-26"
        bar = _bar(d, 9.38, 9.55, 9.30, 9.40)
        # 09:35 触卖；09:40 未触买回 — 旧 bug 会在 09:40 打 eod_cover
        mins = _mins(
            d,
            [
                (930, 9.38, 9.40, 9.35, 9.39),
                (935, 9.39, 9.50, 9.39, 9.48),
                (940, 9.48, 9.49, 9.46, 9.47),
            ],
        )
        holding = {"shares": 100, "cost": 9.38, "stock_name": "中国铝业"}
        cfg = load_t0_rules(
            {
                "direction": "long_t",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 1.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day": True,
                "t0_pm_degrade": "",
            }
        )
        out = _intraday_setup(
            code="601600",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertEqual(day.get("sold_qty"), 100)
        self.assertEqual(day.get("covered_qty") or 0, 0)
        trades = day.get("trades") or []
        self.assertEqual(len(trades), 1)
        self.assertNotEqual(trades[0].get("leg_kind"), "eod_cover")
        self.assertFalse(out.get("path_complete"))

        paper = {"cash": 50000, "holdings": [holding], "trades": []}
        st, new_trades, _snap = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state={"phase": "idle", "legs_written": 0},
            minute_bars=mins,
            bar=bar,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=0,
        )
        self.assertEqual(len(new_trades), 1)
        self.assertEqual(st.get("phase"), "after_leg1")
        self.assertEqual(st.get("legs_written"), 1)
        self.assertEqual(st.get("shares_day_start"), 100)

        # 同前缀再 tick：持仓已因卖出变少，仍用日初仓重放；无新腿，保持 after_leg1
        st2, new2, _ = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state=st,
            minute_bars=mins,
            bar=bar,
            cfg=cfg,
            sellable=0,
            cash=float(paper.get("cash") or 0),
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=1,
        )
        self.assertEqual(new2, [])
        self.assertEqual(st2.get("phase"), "after_leg1")

        # 收到 14:55 收盘 K：强制回补第二腿
        mins_eod = mins + _mins(d, [(1455, 9.47, 9.48, 9.45, 9.46)])
        st3, new3, _ = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state=st2,
            minute_bars=mins_eod,
            bar=bar,
            cfg=cfg,
            sellable=0,
            cash=float(paper.get("cash") or 0),
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=1,
        )
        self.assertEqual(len(new3), 1)
        self.assertTrue(str(new3[0].get("side")).endswith("buy"))
        self.assertEqual(new3[0].get("leg_kind"), "eod_cover")
        self.assertEqual(st3.get("phase"), "done")
        self.assertEqual(st3.get("legs_written"), 2)
    def test_intraday_setup_eod_cover_at_session_close(self):
        """末根进入 ≥14:55 收盘窗时，才允许 must_cover 强制回补。"""
        from core.t0.intraday import _intraday_setup

        d = "2026-08-26"
        bar = _bar(d, 9.38, 9.55, 9.30, 9.40)
        mins = _mins(
            d,
            [
                (930, 9.38, 9.40, 9.35, 9.39),
                (935, 9.39, 9.50, 9.39, 9.48),
                (1000, 9.48, 9.49, 9.46, 9.47),
                (1455, 9.47, 9.48, 9.45, 9.46),
            ],
        )
        holding = {"shares": 100, "cost": 9.38, "stock_name": "中国铝业"}
        cfg = load_t0_rules(
            {
                "direction": "long_t",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 1.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day": True,
                "t0_pm_degrade": "",
            }
        )
        out = _intraday_setup(
            code="601600",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertEqual(day.get("sold_qty"), 100)
        self.assertEqual(day.get("covered_qty"), 100)
        self.assertEqual(day.get("exit_reason"), "eod_cover")
        buys = [t for t in (day.get("trades") or []) if str(t.get("side")).endswith("buy")]
        self.assertEqual(buys[0].get("leg_kind"), "eod_cover")
        self.assertTrue(out.get("path_complete"))

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

    def test_dual_y_blocks_tau_nowcast_sign_strong_disagree(self):
        """|nowcast| 够强且与 y_τ 异号 → 跳过；缺 nowcast 不拦。"""
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.50,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": -1.20,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_enter": 1.0,
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("异号", out.get("direction_reason") or "")
        self.assertIn("nowcast", out.get("direction_reason") or "")

        # 弱 nowcast 异号不拦
        weak = resolve_dual_y_direction(
            scores={
                "y_trade": 0.50,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": -0.30,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_enter": 1.0,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(weak.get("skip"))

        # 缺 nowcast：即使开闸也不因「异号」跳过
        ok = resolve_dual_y_direction(
            scores={"y_trade": 0.50, "y_tau": 0.70, "y_eod": 0.27},
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))
        self.assertNotIn("异号", ok.get("direction_reason") or "")

        # y_trade↔y_τ 异号本身不再触发跳过
        trade_only = resolve_dual_y_direction(
            scores={
                "y_trade": -0.48,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": 0.40,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(trade_only.get("skip"))
        self.assertNotIn("异号", trade_only.get("direction_reason") or "")

    def test_t0_confidence_scale_soft_tau_band_cuts(self):
        from core.t0.score_policy import t0_confidence_scale

        cfg = {
            "y_trade_floor": 0.15,
            "y_tau_enter": 0.40,
            "y_ratio_cut": 0.75,
            "y_ratio_tau_soft_band": 0.20,
            "y_ratio_eod_align_boost": 1.0,
        }
        soft = t0_confidence_scale({"y_trade": 0.5, "y_tau": 0.45}, cfg)
        strong = t0_confidence_scale({"y_trade": 0.5, "y_tau": 0.90}, cfg)
        self.assertAlmostEqual(soft, 0.75, places=4)
        self.assertGreater(strong, soft)

    def test_reverse_t_pm_chase_midpoint(self):
        """14:00 后已开未平：第二腿目标 = 旧目标与现价中点，可触达则成交（非强制市价平）。"""
        bar = _bar("2026-07-29", 100, 102, 97, 100.5)
        # 低吸 98.5 → 原卖目标 103.425；14:00 中点≈101.31 未触；14:10 再中点≈100.91 触高
        mins = _mins(
            "2026-07-29",
            [
                (935, 100, 100, 98.4, 98.5),
                (1400, 99.5, 100.0, 99.0, 99.2),
                (1410, 99.2, 101.0, 99.1, 100.5),
                (1500, 100.5, 100.8, 100.0, 100.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "pm_chase")
        notes = " ".join(str(t.get("note") or "") for t in out.get("trades") or [])
        self.assertIn("中点追价", notes)
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "pm_chase")
        # 追价卖应低于原 5% 目标（≈103.4），高于低吸价
        self.assertLess(float(sell["price"]), 102.0)
        self.assertGreater(float(sell["price"]), 98.5)

    def test_reverse_t_pm_chase_interval_holds(self):
        """未满间隔不二次中点：14:00 调一次后 14:05 不调，原中点仍触不到则不成交。"""
        bar = _bar("2026-07-29", 100, 102, 97, 99)
        mins = _mins(
            "2026-07-29",
            [
                (935, 100, 100, 98.4, 98.5),
                (1400, 99.5, 100.0, 99.0, 99.2),  # mid≈101.31
                (1405, 99.2, 101.0, 99.1, 100.5),  # high 101 < 101.31，且未到 10min
                (1500, 99.5, 99.8, 99.0, 99.0),  # 再中点≈100.16，high 仍够不着
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=False,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("sold_back_qty") or 0, 0)
        self.assertNotEqual(out.get("exit_reason"), "pm_chase")

    def test_reverse_t_falling_day_mid_stays_above_then_eod(self):
        """单边下跌：纯中点仍高于 high，追价触不到 → 收盘强制平（非 14:00 市价）。"""
        bar = _bar("2026-07-29", 40.86, 41.0, 37.0, 37.33)
        pts = [(935, 40.9, 40.9, 40.4, 40.5)]
        px = 40.5
        t = 940
        while t <= 1500:
            hh, mm = divmod(t, 100)
            if mm >= 60:
                t = (hh + 1) * 100
                continue
            if 1130 <= t < 1300:
                t = 1300
                continue
            if t >= 1400:
                px = max(37.33, px - 0.08)
            else:
                px -= 0.05
            pts.append((t, px + 0.05, px + 0.1, px - 0.05, px))
            mm += 5
            t = (hh + 1) * 100 if mm >= 60 else hh * 100 + mm
        out = simulate_t0_day(
            bar=bar,
            shares=10000,
            cost=40.86,
            cash=2_000_000,
            rules=_rules(
                t0_ratio=1.0,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=0.5,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
            ),
            minute_bars=_mins("2026-07-29", pts),
        )
        self.assertEqual(out.get("exit_reason"), "eod_cover")
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "eod_cover")
        self.assertTrue(str(sell.get("at") or "").startswith("2026-07-29 15:00"))
        self.assertLess(float(sell["price"]), 38.5)

    def test_long_t_pm_chase_midpoint(self):
        """正T：14:00 后买回目标下移为中点并可成交。"""
        bar = _bar("2026-07-29", 100, 103, 99, 101)
        # 卖@102 → 原买回 100.47；14:00 px=101.5 → mid≈100.985，lo=100.9 触达
        mins = _mins(
            "2026-07-29",
            [
                (935, 100, 102.5, 100, 102),
                (1400, 101.8, 102.0, 100.9, 101.5),
                (1500, 101.5, 101.8, 100.5, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="long_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "pm_chase")
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        self.assertEqual(buy.get("leg_kind"), "pm_chase")

    def test_pm_chase_blocks_late_open(self):
        """中点追价起算后不再新开第一腿。"""
        bar = _bar("2026-07-29", 100, 105, 96, 101)
        mins = _mins(
            "2026-07-29",
            [
                (1000, 100, 101, 99, 100),
                (1410, 100, 100, 96, 97),  # 低吸出现在午后 → 应跳过
                (1500, 97, 105, 97, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="reverse_t",
                fill_mode="trigger",
                min_range_pct=1.0,
                t0_pm_degrade="14:00",
                use_atr=False,
            ),
            minute_bars=mins,
        )
        self.assertTrue(out.get("skipped"))
        self.assertIn("未触及低吸", out.get("reason") or "")

    def test_dual_y_fixed_long_ignores_negative_tau(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": -0.8, "y_eod": -0.5},
            cfg={
                "y_tau_enter": 0.25,
                "y_tau_map": "fixed_long",
                "y_block_tau_nowcast_sign": False,
            },
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
            scores={"y_on": 1.5, "y_trade": 0.3},
            direction="reverse_t",
            cfg={"must_cover_same_day": False, "y_on_allow": 1.2, "y_trade_floor": 0.15},
        )
        self.assertFalse(cover["must_cover"])
        self.assertTrue(cover["allow_overnight"])

    def test_dual_y_path_agrees_reverse_t(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": -20.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "reverse_t")
        self.assertIn("y_path", out.get("direction_reason") or "")

    def test_dual_y_path_disagree_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": 25.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("y_path", out.get("direction_reason") or "")

    def test_dual_y_tau_below_enter_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.5, "y_tau": 0.45},
            cfg={
                "y_tau_enter": 0.60,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("横盘", out.get("direction_reason") or "")

    def test_dual_y_upgrade_acceptance_flags_flat_trades(self):
        from core.t0.viz import summarize_dual_y_upgrade_acceptance

        days = [
            {
                "skipped": False,
                "sold_qty": 100,
                "pnl": 10,
                "direction": "reverse_t",
                "scores": {"y_tau": 0.45, "tau_realized": 0.5},
            },
            {
                "skipped": False,
                "sold_qty": 100,
                "pnl": 20,
                "direction": "reverse_t",
                "scores": {"y_tau": 0.80, "tau_realized": 0.9},
            },
        ]
        acc = summarize_dual_y_upgrade_acceptance(
            days, rules={"y_tau_enter": 0.6}
        )
        self.assertFalse(acc["ok"])
        self.assertEqual(acc["flat_tau_trades"], 1)
        self.assertEqual(acc["weak_tau_trades"], 0)

        ok = summarize_dual_y_upgrade_acceptance(
            [days[1]], rules={"y_tau_enter": 0.6}
        )
        self.assertTrue(ok["ok"])

    def test_dual_y_path_weak_skips(self):
        """path 弱信号（|ŷ|<enter）作入场过滤，横盘跳过。"""
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": -1.1},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 30.0,
                "y_use_path": True,
                "y_path_required": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("横盘", out.get("direction_reason") or "")
        self.assertIn("y_path", out.get("direction_reason") or "")

    def test_dual_y_path_weak_passes_when_path_off(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": 10.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 30.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "reverse_t")

    def test_dual_y_path_required_no_model(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.8,
                "y_path_status": "no_model",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_path_required": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=100000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("未 promote", out.get("direction_reason") or "")

    def test_dual_y_gap_tier_skips_high_gap_reverse(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.8,
                "gap_pct": 2.5,
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_gap_tier_mode": "skip_opposite",
                "y_gap_tier_pct": 1.5,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("高开", out.get("direction_reason") or "")

    def test_nowcast_oc_gate_uses_oc_not_cc(self):
        from core.t0.score_policy import _nowcast_oc_pct, resolve_dual_y_direction

        oc = _nowcast_oc_pct(1.0, 2.0)
        self.assertIsNotNone(oc)
        self.assertLess(float(oc), 0.0)
        # CC 与 y_τ 同号，但 OC 异号 → 应跳过
        blocked = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.70,
                "y_nowcast": 1.0,
                "gap_pct": 2.0,
                "nowcast_vs": "prev_close",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": True,
                "y_gap_tier_mode": "off",
                "y_nowcast_enter": 0.5,
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("y_nc_oc", blocked.get("direction_reason") or "")
        # OC 同号 → 不拦
        ok = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.70,
                "y_nowcast": 2.5,
                "gap_pct": 2.0,
                "nowcast_vs": "prev_close",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": True,
                "y_gap_tier_mode": "off",
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))

    def test_sign_gate_uses_table_y_nc_when_oc_off(self):
        """表列 y_nc 与 y_τ 异号 → OC 关时仍应拦。"""
        from core.t0.score_policy import resolve_dual_y_direction, scores_from_item

        scores = scores_from_item(
            {
                "y_trade": 3.0,
                "y_tau": 0.12,
                "y_eod": 0.08,
                "predicted_score_nowcast": -6.61,
                "nowcast_vs": "prev_close",
                "gap_pct": -10.0,
                "features_tau": {"gap_pct": -10.0},
            }
        )
        self.assertLess(float(scores.get("y_nc") or 0), 0)
        blocked = resolve_dual_y_direction(
            scores=scores,
            cfg={
                "y_tau_enter": 0.02,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": False,
                "y_nowcast_enter": 1.0,
                "y_gap_tier_mode": "off",
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("y_nc", blocked.get("direction_reason") or "")

    def test_nc_recomputes_from_kalman_not_stale_raw(self):
        """raw predicted_score_nowcast=-0.4 误标 prev_close 时，y_nc 应 Kalman 重算。"""
        from core.t0.score_policy import scores_from_item

        scores = scores_from_item(
            {
                "y_eod": 0.08,
                "y_tau": 0.12,
                "predicted_score_nowcast": -0.40,
                "nowcast_vs": "prev_close",
                "gap_pct": -6.23,
                "features_tau": {"gap_pct": -6.23},
            }
        )
        nc = float(scores.get("y_nc") or 0)
        self.assertLess(nc, -1.0)
        self.assertNotAlmostEqual(nc, -0.40, places=1)

    def test_scores_from_item_persists_y_nc_oc(self):
        from core.t0.score_policy import scores_from_item

        scores = scores_from_item(
            {
                "y_eod": 0.08,
                "y_tau": 0.12,
                "predicted_score_nowcast": -0.40,
                "nowcast_vs": "prev_close",
                "gap_pct": -6.23,
                "features_tau": {"gap_pct": -6.23},
            }
        )
        self.assertIsNotNone(scores.get("y_nc"))
        self.assertIsNotNone(scores.get("y_nc_oc"))
        self.assertGreater(float(scores["y_nc_oc"]), 0)
        self.assertLess(float(scores["y_nc"]), 0)

    def test_path_panel_first_touch_label(self):
        from core.research.path_panel import first_touch_path_label

        mins = [
            {"high": 100.5, "low": 99.8},
            {"high": 102.5, "low": 100.0},
            {"high": 101.0, "low": 98.0},
        ]
        label, reason = first_touch_path_label(
            mins, ref=100.0, sell_trig_pct=2.0, buy_trig_pct=1.5
        )
        self.assertEqual(label, 100.0)
        self.assertEqual(reason, "sell_first")
        label2, _ = first_touch_path_label(
            [{"high": 100.2, "low": 98.0}], ref=100.0, sell_trig_pct=2.0, buy_trig_pct=1.5
        )
        self.assertEqual(label2, -100.0)


class TestT0Viz(unittest.TestCase):
    def test_y_tau_attribution_hit_miss(self):
        from core.t0.viz import build_y_tau_attribution

        days = [
            {
                "date": "2026-01-10",
                "direction": "reverse_t",
                "open": 100.0,
                "close": 102.0,
                "pnl": 10.0,
                "exposure_pnl": 0,
                "sold_qty": 0,
                "bought_qty": 100,
                "direction_features": {"y_tau": 0.8, "y_path": -20.0, "gap_pct": 0.5},
            },
            {
                "date": "2026-01-11",
                "direction": "long_t",
                "open": 100.0,
                "close": 98.0,
                "pnl": -5.0,
                "exposure_pnl": 0,
                "sold_qty": 100,
                "bought_qty": 0,
                "direction_features": {"y_tau": 0.6, "gap_pct": 0.2},
            },
        ]
        att = build_y_tau_attribution(days)
        self.assertEqual(att["summary"]["traded_with_tau"], 2)
        self.assertEqual(att["by_oc_hit"]["hit"]["n"], 1)
        self.assertEqual(att["by_oc_hit"]["miss"]["n"], 1)

    def test_y_path_attribution_agree_and_skip(self):
        from core.t0.viz import build_y_path_attribution, classify_t0_skip_reason

        days = [
            {
                "date": "2026-01-10",
                "direction": "reverse_t",
                "pnl": 10.0,
                "exposure_pnl": 0,
                "sold_qty": 0,
                "bought_qty": 100,
                "direction_features": {"y_tau": 0.8, "y_path": -20.0},
            },
            {
                "date": "2026-01-11",
                "skipped": True,
                "reason": "dual_y：y_τ→long_t 但 y_path=25.0 预测先卖（不一致）",
                "skip_category": classify_t0_skip_reason(
                    "dual_y：y_τ→long_t 但 y_path=25.0 预测先卖（不一致）"
                ),
            },
        ]
        att = build_y_path_attribution(days, path_enter=15.0)
        self.assertEqual(att["summary"]["path_agree_n"], 1)
        self.assertEqual(att["summary"]["path_skip_days"], 1)
        self.assertEqual(att["skip_by_category"][0]["id"], "y_path_disagree")

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
            classify_t0_skip_reason(
                "dual_y：|y_τ|=0.450% 弱信号区（0.4%≤|y_τ|<0.6%）跳过"
            ),
            "y_tau_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_check=conflict 禁止做T"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y[trend]：冲突 y_eod=0.185% vs y_τ=-0.113%（先验≠盘中）跳过"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T分钟路径未触及卖出价"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T分钟路径未触及低吸位"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "反T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓"
            ),
            "tplus1",
        )
        from core.t0.viz import summarize_skip_reason_label

        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.009%<0.02% 横盘跳过"),
            "y_τ横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.016%<0.02% 横盘跳过"),
            "y_τ横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("反T分钟路径未触及低吸位"),
            "反T未触低吸",
        )
        self.assertEqual(
            summarize_skip_reason_label(
                "反T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓"
            ),
            "T+1无可卖",
        )
        self.assertEqual(
            summarize_skip_reason_label("正T分钟路径未触及卖出价"),
            "正T未触卖出",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "y_path_flat",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "y_path横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_trade|=0.002%<0.01% 预期幅度不足"),
            "y_trade幅度不足",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_trade|=0.085%<0.15% 预期幅度不足"),
            "y_trade_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_trade=-0.381%<floor-0.15% 资格不足"),
            "y_trade_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y：y_τ=0.450% 与 y_nowcast=-0.300% 异号跳过"
            ),
            "trade_tau_sign",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T上移振幅 0.80%<2.00%"),
            "directional_amplitude",
        )

    def test_walk_t0_skip_row_path_mode_uses_cfg_not_day_rules(self):
        """跳过日明细 path_mode 回退 cfg，不引用未定义的 day_rules。"""
        import inspect
        from core.t0 import backtest as bt

        src = inspect.getsource(bt._walk_t0)
        self.assertNotIn("day_rules", src)

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
        self.assertIn("paper-t0-worker-desk", text)
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

    def test_build_intraday_desk_status_locked_rows(self):
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-08-25",
            "updated_at": 1.0,
            "stocks": {
                "600519": {
                    "phase": "skipped",
                    "legs_written": 0,
                    "day_snapshot": {
                        "stock_name": "贵州茅台",
                        "reason": "dual_y：横盘跳过",
                        "signal_skip": True,
                    },
                },
                "000001": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-25 10:05:00",
                    # idle 常缺 stock_name，由纸面持仓补全
                },
                "600050": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-25 09:40:00",
                    "stock_name": "中国联通",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/t0_intraday_state.json"
            import json

            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-08-25"
            ), patch(
                "core.paper.load_paper",
                return_value={
                    "holdings": [
                        {"stock_code": "000001", "stock_name": "平安银行"},
                        {"stock_code": "600050", "stock_name": "中国联通"},
                    ]
                },
            ):
                desk = mod.build_intraday_desk_status()
        self.assertTrue(desk["same_session"])
        self.assertEqual(desk["locked_count"], 1)
        self.assertEqual(desk["counts"]["skipped"], 1)
        self.assertEqual(desk["counts"]["idle"], 2)
        # 有 last_bar_ts 的按时间升序；无时间戳的（终锁）排最后
        self.assertEqual(
            [r["stock_code"] for r in desk["rows"]],
            ["600050", "000001", "600519"],
        )
        self.assertEqual(desk["rows"][0]["stock_name"], "中国联通")
        self.assertEqual(desk["rows"][1]["stock_name"], "平安银行")
        self.assertEqual(desk["rows"][2]["stock_name"], "贵州茅台")
        self.assertTrue(desk["rows"][2]["locked"])
        self.assertIn("横盘", desk["rows"][2]["reason"])

    def test_desk_status_falls_back_to_a_code_name(self):
        import json
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-08-26",
            "stocks": {
                "600029": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-26 09:40:00",
                    "wait_reason": "反T下移振幅 0.98%<1.00%",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t0_intraday_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-08-26"
            ), patch(
                "core.paper.load_paper",
                return_value={"holdings": [{"stock_code": "600029", "stock_name": ""}]},
            ), patch.object(
                mod,
                "_load_a_code_name_map",
                return_value={"600029": "南方航空"},
            ):
                desk = mod.build_intraday_desk_status()
        self.assertEqual(desk["rows"][0]["stock_code"], "600029")
        self.assertEqual(desk["rows"][0]["stock_name"], "南方航空")

    def test_dual_y_threshold_lock_and_session_align(self):
        from core.t0.intraday import (
            _dual_y_threshold_skip,
            _retryable_skip,
            accept_intraday_dual_y_scores,
            align_session_day_context,
            unlock_retryable_skipped,
        )

        # 盘口可重试；dual_y 门槛对齐后终锁（不进 retryable）
        self.assertTrue(_retryable_skip("振幅不足 0.30% < 0.80%"))
        self.assertTrue(_retryable_skip("正T分钟路径未触及卖出价"))
        self.assertFalse(_retryable_skip("dual_y：|y_τ|=0.027%<0.1% 横盘跳过"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_τ|=0.027%<0.1% 横盘跳过"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_path|=1.1<30.0 横盘跳过"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_trade|=0.041%<0.1% 预期幅度不足"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：y_τ=0.45% 与 y_nowcast=-0.3% 异号跳过"))
        self.assertFalse(_retryable_skip("stance=avoid 跳过做T"))

        # 旧版 dual_y 软锁（无 score_locked）可解锁一次重评
        unlocked = unlock_retryable_skipped(
            {
                "phase": "skipped",
                "legs_written": 0,
                "reason": "dual_y：|y_τ|=0.027%<0.1% 横盘跳过",
            }
        )
        self.assertIsNotNone(unlocked)
        self.assertEqual(unlocked["phase"], "idle")
        # 对齐后门槛终锁带 score_locked，不再解锁
        self.assertIsNone(
            unlock_retryable_skipped(
                {
                    "phase": "skipped",
                    "score_locked": True,
                    "reason": "dual_y：|y_τ|=0.027%<0.1% 横盘跳过",
                }
            )
        )
        self.assertIsNone(
            unlock_retryable_skipped(
                {"phase": "skipped", "reason": "stance=avoid 跳过做T"}
            )
        )

        self.assertIsNone(
            accept_intraday_dual_y_scores(
                {"y_tau": -0.027, "y_eod": 0.5, "_score_source": "live_book"},
                source="compute",
            )
        )
        self.assertIsNotNone(
            accept_intraday_dual_y_scores(
                {"y_tau": 0.13, "y_eod": 0.5, "_score_source": "compute"},
                source="compute",
            )
        )

        daily = [
            {"date": "2026-08-24", "open": 10, "high": 11, "low": 9.5, "close": 10.5},
        ]
        mins = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.6, "high": 10.7, "low": 10.5, "close": 10.65},
            {"datetime": "2026-08-25 09:40:00", "open": 10.65, "high": 10.8, "low": 10.6, "close": 10.7},
        ]
        aligned = align_session_day_context(
            session_date="2026-08-25",
            daily_bars=daily,
            minute_by_day={"2026-08-25": mins},
        )
        self.assertTrue(aligned["ok"])
        self.assertEqual(aligned["bar"]["date"], "2026-08-25")
        self.assertEqual(aligned["bar"]["open"], 10.6)
        self.assertEqual(len(aligned["hist"]), 1)
        self.assertEqual(aligned["aligned"], "minute_open")
        # 勿回退昨分钟
        stale = align_session_day_context(
            session_date="2026-08-25",
            daily_bars=daily,
            minute_by_day={"2026-08-24": mins},
        )
        self.assertTrue(stale.get("pending"))
        self.assertFalse(stale.get("ok"))

    def test_past_morning_close_forces_dual_y_gate(self):
        from datetime import datetime
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from core.t0.intraday import needs_midday_dual_y_gate, past_morning_close

        sh = ZoneInfo("Asia/Shanghai")
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 11, 29, tzinfo=sh),
        ):
            self.assertFalse(past_morning_close())
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 11, 30, tzinfo=sh),
        ):
            self.assertTrue(past_morning_close())
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 13, 5, tzinfo=sh),
        ):
            self.assertTrue(past_morning_close())

        self.assertTrue(
            needs_midday_dual_y_gate(
                {"phase": "idle", "legs_written": 0, "last_bar_ts": "2026-08-25 11:30:00"}
            )
        )
        self.assertTrue(
            needs_midday_dual_y_gate(
                {"phase": "idle", "legs_written": 0, "direction": "long_t"}
            )
        )
        self.assertFalse(
            needs_midday_dual_y_gate(
                {"phase": "after_leg1", "legs_written": 1, "direction": "long_t"}
            )
        )
        self.assertFalse(
            needs_midday_dual_y_gate(
                {"phase": "skipped", "score_locked": True, "legs_written": 0}
            )
        )
        from core.t0.intraday import lock_zero_legs_after_morning

        locked = lock_zero_legs_after_morning(
            code="600519",
            holding={"stock_name": "贵州茅台"},
            session_date="2026-08-25",
        )
        self.assertEqual(locked["phase"], "skipped")
        self.assertTrue(locked["score_locked"])
        self.assertIn("无成交腿", locked["reason"])

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


class TestDeleteT0Records(unittest.TestCase):
    def test_delete_voids_roundtrip_and_restores_sellable(self):
        import tempfile
        from core.paper import load_paper, save_paper
        from core.paper.tplus1 import sellable_shares
        from services.paper_service import PaperService

        sess = "2026-08-26"
        prev = "2026-08-25"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 99989.91,
                "cost_model": "zero",
                "holdings": [
                    {
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "shares": 100,
                        "cost": 10.0,
                        "lots": [
                            {
                                "shares": 100,
                                "bought_at": f"{sess}T09:40:00",
                                "bought_date": sess,
                            }
                        ],
                        "t0": {"enabled": True, "last_date": sess},
                    }
                ],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.0,
                        "net_cash_delta": 1005.0,
                        "ts": f"{sess}T09:35:00",
                        "at": f"{sess} 09:35",
                    },
                    {
                        "side": "t0_buy",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.09,
                        "net_cash_delta": -1015.09,
                        "ts": f"{sess}T09:40:00",
                        "at": f"{sess} 09:40",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": -10.09,
                            "source": "paper_t0_auto",
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "stock_name": "中国铝业",
                                    "date": sess,
                                    "direction": "long_t",
                                    "pnl": -10.09,
                                    "trades": [
                                        {
                                            "side": "t0_sell",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:35",
                                        },
                                        {
                                            "side": "t0_buy",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:40",
                                        },
                                    ],
                                }
                            ],
                        },
                    }
                },
                "operation_log": [],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 100000.0, "total_value": 101000.0},
            ), patch(
                "services.paper_trades.append_snapshot",
                return_value=None,
            ), patch(
                "core.market.calendar.prev_trading_day",
                return_value=prev,
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("voided_legs"), 2)
            self.assertAlmostEqual(out.get("cash_delta"), 10.09, places=2)
            paper2 = load_paper(path)
            self.assertIsNone((paper2.get("rules") or {}).get("t0_auto", {}).get("last_run"))
            self.assertAlmostEqual(float(paper2.get("cash") or 0), 100000.0, places=2)
            h = next(x for x in paper2["holdings"] if x["stock_code"] == "601600")
            self.assertEqual(float(h.get("shares") or 0), 100)
            self.assertEqual(sellable_shares(h, as_of=sess), 100)
            voided = [t for t in paper2["trades"] if t.get("voided")]
            self.assertEqual(len(voided), 2)
            self.assertTrue(any(e.get("type") == "t0_void" for e in paper2.get("operation_log") or []))

    def test_delete_only_last_run_keeps_other_code(self):
        import tempfile
        from core.paper import load_paper, save_paper
        from services.paper_service import PaperService

        sess = "2026-08-26"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000.0,
                "holdings": [],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "shares": 100,
                        "price": 10.0,
                        "net_cash_delta": 1000.0,
                        "ts": f"{sess}T09:35:00",
                    },
                    {
                        "side": "t0_sell",
                        "stock_code": "002352",
                        "shares": 100,
                        "price": 20.0,
                        "net_cash_delta": 2000.0,
                        "ts": f"{sess}T10:00:00",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": 0,
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 10}],
                                },
                                {
                                    "stock_code": "002352",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 20}],
                                },
                            ],
                        },
                    }
                },
                "operation_log": [],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 99000.0, "total_value": 99000.0},
            ), patch("services.paper_trades.append_snapshot", return_value=None), patch(
                "core.market.calendar.prev_trading_day",
                return_value="2026-08-25",
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"))
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "002352")
            paper2 = load_paper(path)
            self.assertTrue(
                any(
                    t.get("stock_code") == "601600" and t.get("voided")
                    for t in paper2["trades"]
                )
            )
            self.assertFalse(
                any(
                    t.get("stock_code") == "002352" and t.get("voided")
                    for t in paper2["trades"]
                )
            )


class TestClearT0Intraday(unittest.TestCase):
    def test_clear_skips_legs_unless_force(self):
        import json
        import tempfile
        from core.t0 import intraday as intraday_mod
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "t0_intraday_state.json")
            paper_path = os.path.join(tmp, "paper.json")
            state = {
                "session_date": "2026-08-26",
                "stocks": {
                    "000651": {"phase": "skipped", "legs_written": 0, "score_locked": True},
                    "002352": {"phase": "after_leg1", "legs_written": 1},
                },
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
            svc = PaperService(paper_path)
            with patch.object(intraday_mod, "_state_path", return_value=state_path), patch(
                "core.t0.intraday.build_intraday_desk_status",
                return_value={"rows": [], "universe_count": 0},
            ):
                out = svc.clear_t0_intraday(clear_all=True, force=False)
                self.assertEqual(out["cleared"], 1)
                self.assertEqual(out["stock_codes"], ["000651"])
                self.assertEqual(out["skipped_with_legs"], ["002352"])
                left = json.load(open(state_path, encoding="utf-8"))
                self.assertIn("002352", left["stocks"])
                self.assertNotIn("000651", left["stocks"])

                out2 = svc.clear_t0_intraday(stock_codes=["002352"], force=True)
                self.assertEqual(out2["cleared"], 1)
                left2 = json.load(open(state_path, encoding="utf-8"))
                self.assertEqual(left2.get("stocks") or {}, {})


if __name__ == "__main__":
    unittest.main()
