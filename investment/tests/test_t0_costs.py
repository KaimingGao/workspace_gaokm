"""做 T 成本与纸面调仓对齐。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestT0Costs(unittest.TestCase):
    def test_simple_cn_pnl_matches_cash_delta(self):
        from core.t0.minute_path import _first_touch_long

        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 09:45:00", "open": 10.5, "high": 10.5, "low": 9.8, "close": 10.0},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 9.8, "close": 10.0}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_long(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            sell_trig=5.0,
            buy_trig=5.0,
            lot=100,
            fill_mode="trigger",
            cfg={"t0_ratio": 0.4, "must_cover_same_day": True, "lot_size": 100},
            cost_model="simple_cn",
            cost_params=__import__("core.paper.costs", fromlist=["cost_params"]).cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
        )
        self.assertTrue(out.get("success"))
        self.assertEqual(len(out.get("trades") or []), 2)
        self.assertGreater(float(out.get("fees_total") or 0), 0)
        self.assertEqual(out.get("cost_model"), "simple_cn")
        self.assertAlmostEqual(float(out["pnl"]), float(out["cash_delta"]), places=2)
        for t in out["trades"]:
            self.assertIn("fees", t)
            self.assertIn("net_cash_delta", t)

    def test_zero_model_no_fees(self):
        from core.t0.costs import resolve_t0_cost_context

        model, params = resolve_t0_cost_context(paper={"cost_model": "zero"})
        self.assertEqual(model, "zero")
        gross = 1000.0
        from core.t0.costs import append_t0_leg

        trades = []
        append_t0_leg(
            trades,
            cost_model=model,
            cost_params=params,
            side="t0_sell",
            stock_code="600519",
            shares=100,
            price=10.0,
            trigger=10.0,
            at="t",
            leg_kind="trigger",
            note="test",
        )
        self.assertEqual(trades[0]["fees"], 0.0)
        self.assertEqual(trades[0]["net_cash_delta"], gross)

    def test_eod_cover_abandons_when_buy_fees_exceed_sell_net(self):
        """反T must_cover：卖出净得不够买回（含佣金）→ abandon_cover_cash。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_long

        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 14:55:00", "open": 10.5, "high": 10.5, "low": 10.2, "close": 10.5},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 10.5, "low": 10.2, "close": 10.5},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_long(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            sell_trig=5.0,
            buy_trig=5.0,
            lot=100,
            fill_mode="trigger",
            cfg={"t0_ratio": 0.4, "must_cover_same_day": True, "lot_size": 100},
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
        )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(int(out.get("sold_qty") or 0), 400)
        self.assertEqual(int(out.get("covered_qty") or 0), 0)
        self.assertEqual(out.get("exit_reason"), "abandon_cover_cash", out)
        self.assertEqual(len(out.get("trades") or []), 1)

    def test_apply_trades_skips_buy_when_shared_cash_short(self):
        """共享现金不足时买腿跳过，不把 paper.cash 打成负。"""
        from core.t0.intraday import _apply_trades_to_paper

        paper = {"cash": 100.0, "trades": [], "operation_log": []}
        holding = {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 10}
        trades = [
            {
                "side": "t0_buy",
                "shares": 100,
                "price": 10.0,
                "amount": 1000.0,
                "net_cash_delta": -1000.0,
                "fees": 0.0,
            }
        ]
        applied = _apply_trades_to_paper(
            paper, holding, trades, as_of="2026-08-25", log_source="test"
        )
        self.assertEqual(applied, [])
        self.assertAlmostEqual(float(paper["cash"]), 100.0)
        self.assertEqual(len(paper.get("trades") or []), 0)

    def test_reverse_leg1_shrinks_for_fees(self):
        """正T低吸：含佣金后缩量，现金不转负。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_reverse

        # 触发价 10.395×1000+佣金5=10400；现金略少 → 必须缩量
        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.5, "high": 10.6, "low": 10.3, "close": 10.45},
            {"datetime": "2026-08-25 09:40:00", "open": 10.45, "high": 10.5, "low": 10.35, "close": 10.4},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 11.2, "low": 10.4, "close": 11.0},
        ]
        bar = {"date": "2026-08-25", "open": 10.5, "high": 11.2, "low": 10.3, "close": 11.0}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_reverse(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            cash=10399.0,
            sellable_shares=1000,
            ref=10.5,
            sell_trig=5.0,
            buy_trig=1.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 1.0,
                "must_cover_same_day": True,
                "lot_size": 100,
                "y_prefix_segment_enabled": False,
            },
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=7.0,
        )
        self.assertTrue(out.get("success"), out)
        bought = int(out.get("bought_qty") or 0)
        self.assertGreater(bought, 0, out)
        self.assertLessEqual(bought, 900, out)
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        self.assertGreaterEqual(10399.0 + float(buy.get("net_cash_delta") or 0), -1e-6)

    def test_long_midday_cover_skips_when_not_self_funded(self):
        """反T盘中追价买回若使现金转负，则本根不成交（与 EOD 闸一致）。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_long

        # 卖@10.5 后追价中点抬到接近/高于卖价，simple_cn 下净现金不够买回
        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 14:00:00", "open": 10.5, "high": 10.55, "low": 10.45, "close": 10.5},
            {"datetime": "2026-08-25 14:10:00", "open": 10.5, "high": 10.55, "low": 10.48, "close": 10.52},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 10.55, "low": 10.4, "close": 10.5},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_long(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            sell_trig=5.0,
            buy_trig=5.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "14:00",
                "t0_pm_chase_interval_min": 10,
            },
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
        )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(int(out.get("sold_qty") or 0), 400)
        # 不得以透支完成盘中追价买回
        if out.get("exit_reason") == "pm_chase":
            self.assertGreaterEqual(float(out.get("cash_delta") or 0), -1e-6, out)
        else:
            self.assertIn(out.get("exit_reason"), ("eod_cover", "abandon_cover_cash"), out)
            self.assertGreaterEqual(float(out.get("cash_delta") or 0), -1e-6, out)

    def test_walk_t0_cash_topup_uses_open_not_close(self):
        """正T研究现金补足不得用 T 日 close（前视）。"""
        from unittest.mock import patch

        from core.t0.backtest import _research_cash_for_reverse, _walk_t0

        captured: list[float] = []
        real_fn = _research_cash_for_reverse

        def spy(shares, px, **kw):
            captured.append(float(px))
            return real_fn(shares, px, **kw)

        bar = {"date": "2026-01-09", "open": 10, "high": 15, "low": 10, "close": 15}
        history = [
            {"date": "2026-01-08", "open": 9, "high": 10, "low": 9, "close": 10},
            {"date": "2026-01-09", "open": 10, "high": 15, "low": 10, "close": 15},
        ]
        mins = {
            "2026-01-09": [
                {"datetime": "2026-01-09 09:35:00", "open": 10, "high": 10.1, "low": 9.9, "close": 10},
                {"datetime": "2026-01-09 09:40:00", "open": 10, "high": 10, "low": 9.5, "close": 9.6},
                {"datetime": "2026-01-09 15:00:00", "open": 9.6, "high": 15, "low": 9.5, "close": 15},
            ]
        }
        rules = {
            "direction": "reverse_t",
            "t0_ratio": 0.4,
            "sell_trigger_pct": 2.0,
            "buy_trigger_pct": 1.0,
            "fill_mode": "trigger",
            "path_mode": "first_touch",
            "use_atr": False,
            "min_range_pct": 0.5,
            "y_prefix_segment_enabled": False,
            "lot_size": 100,
        }
        with patch("core.t0.backtest._research_cash_for_reverse", side_effect=spy):
            _walk_t0(
                [bar],
                initial_shares=1000,
                initial_cost=10,
                initial_cash=100.0,
                rules=rules,
                cost_config=None,
                stock_code="600519",
                minute_by_date=mins,
                bars_history=history,
            )
        self.assertTrue(captured, "应触发研究现金补足")
        self.assertNotIn(15.0, captured, captured)
        self.assertTrue(all(px == 10.0 for px in captured), captured)

    def test_holdings_long_t_exposure_when_shared_cash_blocks_cover(self):
        """共享现金闸掉买回腿时，须按未回补敞口重算 exposure_pnl。"""
        from core.t0.rules import simulate_t0_on_holdings

        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 9.5, "close": 10.0}
        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 09:45:00", "open": 10.5, "high": 10.5, "low": 9.5, "close": 9.6},
        ]
        paper = {
            "cash": -8000.0,
            "holdings": [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 10.0}
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.4,
                    "sell_trigger_pct": 2.0,
                    "buy_trigger_pct": 1.5,
                    "direction": "long_t",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.5,
                    "y_prefix_segment_enabled": False,
                    "must_cover_same_day": False,
                    "lot_size": 100,
                }
            },
        }
        out = simulate_t0_on_holdings(
            paper,
            bars_by_code={"600519": bar},
            minute_bars_by_code={"600519": minute_bars},
            dry_run=True,
        )
        row = (out.get("results") or [None])[0]
        self.assertIsNotNone(row, out)
        self.assertEqual(row.get("exit_reason"), "abandon_cover_cash", row)
        self.assertGreater(int(row.get("sold_qty") or 0), 0, row)
        self.assertEqual(int(row.get("covered_qty") or 0), 0, row)
        exp = float(row.get("exposure_pnl") or 0)
        self.assertNotEqual(exp, 0.0, row)
        self.assertAlmostEqual(float(out.get("exposure_pnl_total") or 0), exp, places=2)


if __name__ == "__main__":
    unittest.main()
