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


def _rules(**kwargs):
    """单测默认 dual_touch，避免 veto 干扰成交腿断言。"""
    base = {"path_mode": "dual_touch", "use_atr": False}
    base.update(kwargs)
    return base


class TestT0Core(unittest.TestCase):
    def test_default_fill_mode_trigger(self):
        self.assertEqual(DEFAULT_T0_RULES["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["path_mode"], "dual_touch")

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
        )
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
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "optimistic"}
        )
        trg = simulate_t0_day(
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "trigger"}
        )
        self.assertGreaterEqual(opt["pnl"], trg["pnl"])

    def test_low_range_skipped(self):
        bar = _bar("2026-01-10", 100, 100.5, 99.5, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(sell_trigger_pct=2.0, buy_trigger_pct=1.5),
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["sold_qty"], 0)
        self.assertIn("振幅", out.get("reason") or "")

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
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["sold_qty"], 0)
        self.assertEqual(out["trades"], [])

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
        )
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
        )
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
        )
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
        )
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
        )
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
        )
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
        )
        self.assertEqual(out.get("direction_used") or "long_t", "long_t")

    def test_path_veto_skips_wrong_direction(self):
        # 收盘偏强（似先低后高），开盘却选正T → veto
        bar = _bar("2026-01-10", 100, 105, 98, 104.5)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules={
                "direction": "long_t",
                "path_mode": "veto",
                "use_atr": False,
                "min_range_pct": 1.0,
                "sell_trigger_pct": 2.0,
                "buy_trigger_pct": 1.5,
            },
        )
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertIn("定反", out.get("reason") or "")

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
                path_mode="dual_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
        )
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
                path_mode="dual_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
        )
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
                path_mode="dual_touch",
                dir_enter=0.35,
                min_range_pct=1.0,
            ),
        )
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("signal_skip"))
        self.assertIn("低置信", out.get("reason") or "")

    def test_path_adverse_long_no_trigger_cover_on_lh(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                direction="long_t",
                path_mode="adverse",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="trigger",
                must_cover_same_day=False,
            ),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 0)
        self.assertEqual(out.get("intraday_path"), "lh")

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
        )
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 0)
        self.assertNotEqual(out["exposure_pnl"], 0)

    def test_backtest_runs_with_optimistic_compare(self):
        bars = [
            _bar(f"d{i}", 100 + i * 0.1, 110, 90, 100 + i * 0.05) for i in range(20)
        ]
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(direction="long_t", min_range_pct=1.0),
            compare_optimistic=True,
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

    def test_backtest_defaults_path_veto(self):
        # 显式 long_t+veto：测路径否决（不经 Execution 收敛）
        bars = [_bar("d0", 100, 105, 98, 104.5) for _ in range(20)]
        for i, b in enumerate(bars):
            b["date"] = f"d{i}"
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={
                "use_atr": False,
                "min_range_pct": 1.0,
                "direction": "long_t",
                "path_mode": "veto",
            },
            compare_optimistic=False,
        )
        self.assertTrue(report["success"])
        self.assertEqual((report.get("rules") or {}).get("path_mode"), "veto")
        self.assertEqual((report.get("rules") or {}).get("direction"), "long_t")
        self.assertEqual(report.get("signal_skip_days"), 0)

    def test_backtest_resolve_defaults_dual_y(self):
        from core.execution import resolve_t0_rules, strip_execution_meta

        cfg = strip_execution_meta(
            resolve_t0_rules(rules={"use_atr": False}, channel="backtest", has_minute=False)
        )
        self.assertEqual(cfg.get("direction"), "dual_y")
        self.assertEqual(cfg.get("path_mode"), "veto")

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
        daily = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                direction="long_t",
                path_mode="dual_touch",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
        )
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
        self.assertGreater(int(daily.get("covered_qty") or 0), 0)
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
            compare_daily=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(int(report.get("minute_path_days") or 0), 1)
        self.assertIn("daily_compare", report)

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
                    "path_mode": "dual_touch",
                    "use_atr": False,
                    "min_range_pct": 1.0,
                }
            },
        }
        bars = {"600519": _bar("2026-01-09", 100, 105, 98, 101)}
        out = simulate_t0_on_holdings(paper, bars_by_code=bars, dry_run=True)
        self.assertTrue(out["success"])
        self.assertTrue(out["dry_run"])
        self.assertGreater(len(out["trades"]), 0)
        self.assertEqual(len(paper["trades"]), 0)
        self.assertEqual(float(paper["holdings"][0]["shares"]), 1000)

        out2 = simulate_t0_on_holdings(paper, bars_by_code=bars, dry_run=False)
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
                    "path_mode": "dual_touch",
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

    def test_dual_y_missing_scores_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0),
            scores={},
        )
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
            rules=_rules(direction="dual_y", min_range_pct=1.0, y_trade_floor=-0.15),
            scores={"y_trade": -0.5, "y_tau": 0.8, "y_eod": 0.1},
        )
        self.assertTrue(out.get("skipped"))
        self.assertIn("y_trade", out.get("reason") or "")

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
        )
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
                path_mode="dual_touch",
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
        )
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
                path_mode="dual_touch",
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
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "reverse_t")

    def test_dual_y_overnight_allow_when_yon_aligns(self):
        from core.t0.score_policy import resolve_cover_policy

        cover = resolve_cover_policy(
            scores={"y_on": -1.5, "y_trade": 0.3},
            direction="long_t",
            cfg={"must_cover_same_day": False, "y_on_allow": 1.2, "y_trade_floor": -0.15},
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
                path_mode="dual_touch",
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
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade 快照"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116<0.25 横盘跳过"),
            "y_tau_flat",
        )

    def test_build_and_merge_viz(self):
        from core.t0.viz import build_t0_viz_payload, merge_t0_viz_payloads

        days = [
            {
                "date": "2026-08-11",
                "skipped": True,
                "reason": "dual_y：缺 y_eod/y_τ/y_trade 快照",
                "signal_skip": True,
            },
            {
                "date": "2026-08-12",
                "direction": "long_t",
                "sold_qty": 100,
                "covered_qty": 100,
                "pnl": 150.0,
                "exposure_pnl": 0,
                "direction_score": 0.55,
            },
        ]
        viz = build_t0_viz_payload(days, stock_code="688047", stock_name="龙芯")
        self.assertEqual(viz["trade_count"], 1)
        self.assertEqual(viz["skip_count"], 1)
        self.assertEqual(len(viz["cumulative_pnl"]), 1)
        self.assertEqual(viz["cumulative_pnl"][0]["cum_pnl"], 150.0)
        self.assertTrue(any(c["id"] == "missing_scores" for c in viz["skip_categories"]))

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
        self.assertIn("dual_y", text)
        self.assertIn("预演做T", text)
        self.assertIn("paper-holdings-table", text)
        self.assertNotIn('id="paper-rebalance"', text)
        self.assertNotIn("跑一日", text)


if __name__ == "__main__":
    unittest.main()
