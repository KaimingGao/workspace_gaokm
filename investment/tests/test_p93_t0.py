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

    def test_reverse_t_buy_then_sell(self):
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
        self.assertGreater(out["sold_back_qty"], 0)
        self.assertGreater(out["pnl"], 0)

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

    def test_backtest_defaults_path_veto(self):
        # 收近高：正T会被 veto；显式不传 path_mode/direction 时回测应启用 veto+signal
        bars = [_bar("d0", 100, 105, 98, 104.5) for _ in range(20)]
        for i, b in enumerate(bars):
            b["date"] = f"d{i}"
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={"use_atr": False, "min_range_pct": 1.0},
            compare_optimistic=False,
        )
        self.assertTrue(report["success"])
        self.assertEqual((report.get("rules") or {}).get("path_mode"), "veto")
        self.assertEqual((report.get("rules") or {}).get("direction"), "signal")
        self.assertIn("signal_skip_days", report)

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
        bars = {"600519": _bar("2026-01-10", 100, 105, 98, 101)}
        out = simulate_t0_on_holdings(paper, bars_by_code=bars, dry_run=True)
        self.assertTrue(out["success"])
        self.assertTrue(out["dry_run"])
        self.assertGreater(len(out["trades"]), 0)
        self.assertEqual(len(paper["trades"]), 0)
        self.assertEqual(float(paper["holdings"][0]["shares"]), 1000)

        out2 = simulate_t0_on_holdings(paper, bars_by_code=bars, dry_run=False)
        self.assertFalse(out2["dry_run"])
        self.assertGreater(len(paper["trades"]), 0)

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
        self.assertIn("预演做T", text)
        self.assertIn("paper-holdings-table", text)
        self.assertNotIn('id="paper-rebalance"', text)
        self.assertNotIn("跑一日", text)


if __name__ == "__main__":
    unittest.main()
