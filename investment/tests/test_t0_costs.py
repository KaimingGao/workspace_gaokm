"""做 T 成本与纸面调仓对齐。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stb_cfg(**extra):
    base = {
        "t0_ratio": 0.4,
        "must_cover_same_day": True,
        "lot_size": 100,
        "t0_close_band_delta_pct": 0.01,
        "y_tau_exit_price_skip_sell_then_buy": False,
    }
    base.update(extra)
    return base


def _bts_cfg(**extra):
    base = {
        "t0_ratio": 1.0,
        "must_cover_same_day": True,
        "lot_size": 100,
        "t0_close_band_delta_pct": 0.01,
        "y_tau_exit_price_skip_buy_then_sell": False,
    }
    base.update(extra)
    return base


class TestT0Costs(unittest.TestCase):
    def test_simple_cn_pnl_matches_cash_delta(self):
        from core.t0.minute_path import _first_touch_sell_then_buy

        minute_bars = [
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 09:45:00", "open": 10.5, "high": 10.5, "low": 9.8, "close": 10.0},
            {"datetime": "2026-08-25 15:00:00", "open": 10.0, "high": 10.1, "low": 9.8, "close": 10.0},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 9.8, "close": 10.0}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_sell_then_buy(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            lot=100,
            fill_mode="trigger",
            cfg=_stb_cfg(),
            cost_model="simple_cn",
            cost_params=__import__("core.paper.costs", fromlist=["cost_params"]).cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
            session_bars=minute_bars,
            session_bar=bar,
            defer_eod=False,
            y_tau=-0.5,
            leg1_gate_at=lambda i: i == 0,
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

    def test_default_research_cost_config_is_simple_cn(self):
        from core.t0.costs import (
            default_t0_research_cost_config,
            resolve_t0_cost_context,
        )

        cfg = default_t0_research_cost_config()
        self.assertGreater(float(cfg.get("commission_bps") or 0), 0)
        self.assertGreater(float(cfg.get("stamp_duty_bps_sell") or 0), 0)
        model, params = resolve_t0_cost_context(cost_config=cfg)
        self.assertEqual(model, "simple_cn")
        self.assertGreater(float(params.get("commission_rate") or 0), 0)

    def test_eod_cover_abandons_when_account_cash_short(self):
        """反T must_cover：账户余额（含卖出净得）不够买回 → abandon_cover_cash。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_sell_then_buy

        minute_bars = [
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 14:55:00", "open": 10.5, "high": 10.5, "low": 10.2, "close": 10.5},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 10.5, "low": 10.2, "close": 10.5},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        kwargs = dict(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            lot=100,
            fill_mode="trigger",
            cfg=_stb_cfg(),
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
            session_bars=minute_bars,
            session_bar=bar,
            defer_eod=False,
            y_tau=-0.5,
            leg1_gate_at=lambda i: i == 0,
        )
        # 无账户余额：卖出净得不够覆盖含费买回 → 放弃
        out = _first_touch_sell_then_buy(**kwargs, cash=0.0)
        self.assertTrue(out.get("success"), out)
        self.assertEqual(int(out.get("sold_qty") or 0), 400)
        self.assertEqual(int(out.get("covered_qty") or 0), 0)
        self.assertEqual(out.get("exit_reason"), "abandon_cover_cash", out)
        self.assertEqual(len(out.get("trades") or []), 1)

        # 账户有余额：即使卖出净得不够，也强制买回
        out2 = _first_touch_sell_then_buy(**kwargs, cash=50.0)
        self.assertTrue(out2.get("success"), out2)
        self.assertEqual(int(out2.get("sold_qty") or 0), 400)
        self.assertEqual(int(out2.get("covered_qty") or 0), 400)
        self.assertIn(out2.get("exit_reason"), ("eod_cover", "trigger"), out2)
        self.assertEqual(len(out2.get("trades") or []), 2)

    def test_eod_cover_uses_last_5m_close_not_daily(self):
        """反T：日线收盘高于末根 5m 时，强平买回仍按 5m 收，避免把 PnL 打负。"""
        from core.t0.minute_path import _first_touch_sell_then_buy

        minute_bars = [
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 14:55:00", "open": 10.8, "high": 10.9, "low": 10.7, "close": 10.85},
            {"datetime": "2026-08-25 15:00:00", "open": 10.85, "high": 10.9, "low": 10.7, "close": 10.80},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 12.0, "low": 10.0, "close": 11.80}
        out = _first_touch_sell_then_buy(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            lot=100,
            fill_mode="trigger",
            cfg=_stb_cfg(
                t0_pm_degrade="",
                t0_stop_pct_sell_then_buy=0,
            ),
            cost_model="zero",
            cost_params={},
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
            cash=20000.0,
            session_bars=minute_bars,
            session_bar=bar,
            defer_eod=False,
            y_tau=None,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(int(out.get("sold_qty") or 0), 400)
        self.assertEqual(int(out.get("covered_qty") or 0), 400)
        self.assertEqual(out.get("exit_reason"), "eod_cover", out)
        buys = [t for t in (out.get("trades") or []) if str(t.get("side") or "").endswith("buy")]
        self.assertEqual(len(buys), 1, out)
        self.assertAlmostEqual(float(buys[0].get("price") or 0), 10.80)
        self.assertNotAlmostEqual(float(buys[0].get("price") or 0), 11.80)

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

    def test_buy_then_sell_leg1_shrinks_for_fees(self):
        """正T低吸：含佣金后缩量，现金不转负。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_buy_then_sell

        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.5, "high": 10.6, "low": 10.3, "close": 10.45},
            {"datetime": "2026-08-25 09:40:00", "open": 10.45, "high": 10.5, "low": 10.35, "close": 10.4},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 11.2, "low": 10.4, "close": 11.0},
        ]
        bar = {"date": "2026-08-25", "open": 10.5, "high": 11.2, "low": 10.3, "close": 11.0}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        out = _first_touch_buy_then_sell(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            cash=10399.0,
            sellable_shares=1000,
            ref=10.5,
            lot=100,
            fill_mode="trigger",
            cfg=_bts_cfg(),
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=7.0,
            session_bars=minute_bars,
            session_bar=bar,
            defer_eod=False,
            y_tau=0.5,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        bought = int(out.get("bought_qty") or 0)
        self.assertGreater(bought, 0, out)
        self.assertLessEqual(bought, 900, out)
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        self.assertGreaterEqual(10399.0 + float(buy.get("net_cash_delta") or 0), -1e-6)

    def test_sell_then_buy_midday_cover_uses_account_cash(self):
        """反T盘中追价买回：账户余额够则成交；不够则本根不成交。"""
        from core.paper.costs import cost_params
        from core.t0.minute_path import _first_touch_sell_then_buy

        minute_bars = [
            {"datetime": "2026-08-25 09:40:00", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5},
            {"datetime": "2026-08-25 14:00:00", "open": 10.5, "high": 10.55, "low": 10.45, "close": 10.5},
            {"datetime": "2026-08-25 14:10:00", "open": 10.5, "high": 10.55, "low": 10.48, "close": 10.52},
            {"datetime": "2026-08-25 15:00:00", "open": 10.5, "high": 10.55, "low": 10.4, "close": 10.5},
        ]
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.5}
        paper = {"cost_model": "simple_cn", "cost_params": {}}
        kwargs = dict(
            minute_bars=minute_bars,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=10.0,
            lot=100,
            fill_mode="trigger",
            cfg=_stb_cfg(t0_pm_degrade="14:00", t0_pm_chase_interval_min=10),
            cost_model="simple_cn",
            cost_params=cost_params(paper),
            stock_code="600519",
            atr_pct=None,
            range_pct=6.0,
            t0_ratio=0.4,
            session_bars=minute_bars,
            session_bar=bar,
            defer_eod=False,
            y_tau=-0.5,
            leg1_gate_at=lambda i: i == 0,
        )
        out = _first_touch_sell_then_buy(**kwargs, cash=0.0)
        self.assertTrue(out.get("success"), out)
        self.assertEqual(int(out.get("sold_qty") or 0), 400)
        if out.get("exit_reason") == "pm_chase":
            self.assertGreaterEqual(float(out.get("cash_delta") or 0), -1e-6, out)
        else:
            self.assertIn(out.get("exit_reason"), ("eod_cover", "abandon_cover_cash"), out)
            self.assertGreaterEqual(float(out.get("cash_delta") or 0), -1e-6, out)

        out2 = _first_touch_sell_then_buy(**kwargs, cash=50.0)
        self.assertTrue(out2.get("success"), out2)
        self.assertEqual(int(out2.get("covered_qty") or 0), 400, out2)
        self.assertIn(out2.get("exit_reason"), ("pm_chase", "eod_cover", "trigger"), out2)

    def test_walk_t0_cash_topup_uses_open_not_close(self):
        """正T研究现金补足不得用 T 日 close（前视）。"""
        from unittest.mock import patch

        from core.t0.backtest import _research_cash_for_buy_then_sell, _walk_t0

        captured: list[float] = []
        real_fn = _research_cash_for_buy_then_sell

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
            "direction": "buy_then_sell",
            "t0_ratio": 0.4,
            "fill_mode": "trigger",
            "path_mode": "first_touch",
            "use_atr": False,
            "min_range_pct": 0.5,
            "y_tau_exit_price_skip_buy_then_sell": False,
            "lot_size": 100,
            "t0_slots_max_rounds": 0,
        }
        with patch("core.t0.backtest._research_cash_for_buy_then_sell", side_effect=spy):
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

    def test_holdings_sell_then_buy_exposure_when_account_cash_blocks_cover(self):
        """账户现金不够买回时，引擎内跳过买腿，按未回补敞口记账。"""
        from unittest.mock import patch

        from core.t0.rules import simulate_t0_on_holdings

        # ŷ_oc=-0.5×默认 2 → C_τ=9.9；C≈9.95>upper 反T。低点压在 C_τ 之上，避免触到买回。
        bar = {"date": "2026-08-25", "open": 10.0, "high": 10.6, "low": 9.90, "close": 10.0}
        minute_bars = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.0, "high": 10.1, "low": 9.90, "close": 9.95},
            {"datetime": "2026-08-25 09:40:00", "open": 9.95, "high": 10.0, "low": 9.90, "close": 9.92},
            {"datetime": "2026-08-25 09:45:00", "open": 9.92, "high": 9.95, "low": 9.90, "close": 9.91},
            {"datetime": "2026-08-25 09:50:00", "open": 9.91, "high": 9.95, "low": 9.90, "close": 9.92},
        ]
        scores = {
            "y_tau": -0.5,
            "y_tau_oc": -0.5,
            "y_τ30": 0.2,
            "y_t30": 0.2,
            "y_τ45": 0.2,
            "y_t45": 0.2,
            "y_τ60": 0.2,
            "y_t60": 0.2,
            "y_τ75": 0.2,
            "y_t75": 0.2,
            "y_τ90": 0.2,
            "y_t90": 0.2,
        }
        paper = {
            "cash": 0.0,
            "cost_model": "simple_cn",
            "holdings": [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 10.0}
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.4,
                    "t0_round_ratio": 0.4,
                    "direction": "sell_then_buy",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.5,
                    "t0_close_band_delta_pct": 0.01,
                    "t0_lock_win_pct_sell_then_buy": 0,
                    "t0_stop_pct_sell_then_buy": 0,
                    "t0_pm_degrade": "",
                    "y_tau_exit_price_skip_sell_then_buy": False,
                    "lot_size": 100,
                    "t0_slots_max_rounds": 1,
                    "y_tw_enter": 0,
                    "y_τc_enter": 0,
                    "y_τc_strong": 0,
                    "y_τc_enter_amount": 0,
                    "y_τc_strong_amount": 0,
                    "y_tw_vote_margin": 2,
                    "y_tpd_max": 1.0,
                }
            },
        }

        def _freeze_snap(**kwargs):
            snap = kwargs.get("open_snap")
            return dict(snap) if isinstance(snap, dict) else dict(scores)

        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            side_effect=_freeze_snap,
        ):
            out = simulate_t0_on_holdings(
                paper,
                bars_by_code={"600519": bar},
                minute_bars_by_code={"600519": minute_bars},
                dry_run=True,
                scores_by_code={"600519": scores},
            )
        self.assertTrue(out.get("success"), out)
        row = (out.get("results") or [{}])[0]
        self.assertGreater(int(row.get("sold_qty") or 0), 0, row)
        self.assertEqual(int(row.get("covered_qty") or 0), 0, row)
        self.assertGreater(int(row.get("uncovered_qty") or 0), 0, row)


if __name__ == "__main__":
    unittest.main()
