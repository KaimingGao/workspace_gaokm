"""日报 TopK 口径：含成本 / K 与持有期对齐纸面 / 现金权重 / 止损影子。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import (
    _clip_leg_at_close_stop,
    _stop_shadow_from_trades,
    _weighted_port_return,
    backtest_topk_equal_weight,
)
from quant.research.portfolio_data import (
    resolve_daily_topk_backtest_kwargs,
    summarize_portfolio_backtest,
)
from tests.test_p10_quant import _aligned_bars


class TestCashAwarePortReturn(unittest.TestCase):
    def test_cash_drag_not_renormalized(self):
        legs = [
            {"stock_code": "A", "return_pct": 10.0},
            {"stock_code": "B", "return_pct": 10.0},
            {"stock_code": "C", "return_pct": 10.0},
        ]
        full = _weighted_port_return(
            legs, {"A": 33.33, "B": 33.33, "C": 33.34}
        )
        cash = _weighted_port_return(
            legs, {"A": 25.0, "B": 25.0, "C": 25.0}
        )
        self.assertAlmostEqual(full, 10.0, places=2)
        self.assertAlmostEqual(cash, 7.5, places=2)


class TestDailyTopkDefaults(unittest.TestCase):
    def test_resolve_aligns_k_h_costs(self):
        from core.signal.config import get_scoring_horizon_days
        from core.strategy import backtest_portfolio_defaults

        d = backtest_portfolio_defaults("short_conservative")
        got = resolve_daily_topk_backtest_kwargs()
        self.assertEqual(got["top_k"], d["max_positions"])
        self.assertNotEqual(d["top_k"], d["max_positions"])
        self.assertEqual(got["horizon_days"], d["horizon_days"])
        self.assertTrue(got["apply_costs"])
        self.assertEqual(got["weight_mode"], d["weight_mode"])
        self.assertEqual(got["yhat_horizon_days"], get_scoring_horizon_days())
        self.assertEqual(got["paper_horizon_days"], d["horizon_days"])
        self.assertEqual(got["paper_max_positions"], d["max_positions"])
        self.assertEqual(got["top_k"], got["paper_max_positions"])
        self.assertEqual(got["lookback"], 30)

    def test_daily_bt_option_defaults_align_replay(self):
        from quant.research.portfolio_data import (
            DAILY_BT_UI_LOOKBACK,
            DAILY_BT_UI_RANK_ENTER,
            DAILY_BT_UI_RANK_STRONG,
            DAILY_BT_UI_FUSION_W_CO,
            daily_bt_option_defaults,
            resolve_daily_replay_kwargs,
        )

        d = resolve_daily_replay_kwargs()
        opts = daily_bt_option_defaults()
        self.assertEqual(opts["engine"], "paper_replay")
        self.assertEqual(opts["lookback"], DAILY_BT_UI_LOOKBACK)
        self.assertEqual(opts["fusion_w_co"], DAILY_BT_UI_FUSION_W_CO)
        self.assertAlmostEqual(opts["rank_enter"], DAILY_BT_UI_RANK_ENTER)
        self.assertAlmostEqual(opts["rank_strong"], DAILY_BT_UI_RANK_STRONG)
        self.assertEqual(opts["lookback"], d["lookback"])
        self.assertTrue(opts["apply_costs"])
        self.assertIn("paper_max_positions", opts)

    def test_explicit_overrides(self):
        got = resolve_daily_topk_backtest_kwargs(
            top_k=5, horizon_days=2, apply_costs=False
        )
        self.assertEqual(got["top_k"], 5)
        self.assertEqual(got["horizon_days"], 2)
        self.assertFalse(got["apply_costs"])

    def test_replay_overrides(self):
        from quant.research.portfolio_data import resolve_daily_replay_kwargs

        got = resolve_daily_replay_kwargs(
            lookback=60, fusion_w_co=0.4, rank_enter=0.015, rank_strong=0.03
        )
        self.assertEqual(got["lookback"], 60)
        self.assertEqual(got["fusion_w_co"], 0.4)
        self.assertAlmostEqual(got["rank_enter"], 0.015)
        self.assertAlmostEqual(got["rank_strong"], 0.03)
        self.assertEqual(got["engine"], "paper_replay")

    def test_replay_lookback_10_allowed(self):
        from quant.research.portfolio_data import resolve_daily_replay_kwargs

        got = resolve_daily_replay_kwargs(lookback=10)
        self.assertEqual(got["lookback"], 10)


class TestStopShadow(unittest.TestCase):
    def test_clips_leg_when_close_hits_stop(self):
        dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
        date_maps = {
            "A": {
                "2026-01-01": {"close": 100},
                "2026-01-02": {"close": 100, "open": 100},
                "2026-01-03": {"close": 96},
                "2026-01-04": {"close": 110},
            }
        }
        trades = [
            {
                "entry_date": "2026-01-02",
                "exit_date": "2026-01-04",
                "legs": [
                    {
                        "stock_code": "A",
                        "fill_price": 100.0,
                        "return_pct": 10.0,
                        "weight_pct": 100.0,
                        "entry_date": "2026-01-02",
                        "exit_date": "2026-01-04",
                    }
                ],
            }
        ]
        out = _stop_shadow_from_trades(
            trades, date_maps, dates, stop_pct=0.03, holding_days=2
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["legs_clipped"], 1)
        self.assertAlmostEqual(out["total_return_pct"], -4.0, places=1)

    def test_clip_helper_advances_exit(self):
        dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
        date_i = {d: i for i, d in enumerate(dates)}
        date_maps = {
            "A": {
                "2026-01-01": {"close": 100},
                "2026-01-02": {"close": 100},
                "2026-01-03": {"close": 96},
                "2026-01-04": {"close": 110},
            }
        }
        exit_d, exit_px, ret, hit = _clip_leg_at_close_stop(
            code="A",
            entry_px=100.0,
            entry_date="2026-01-02",
            exit_date="2026-01-04",
            exit_px=110.0,
            date_maps=date_maps,
            dates=dates,
            date_i=date_i,
            stop_pct=0.03,
        )
        self.assertTrue(hit)
        self.assertEqual(exit_d, "2026-01-03")
        self.assertAlmostEqual(exit_px, 96.0)
        self.assertAlmostEqual(ret, -4.0, places=1)


class TestTopkParamsDisclose(unittest.TestCase):
    def test_cash_aware_and_stop_shadow(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            apply_costs=True,
            neutralize=False,
            weight_mode="score_budget",
            max_position_pct=25.0,
            max_sector_pct=40.0,
        )
        self.assertTrue(result.get("success"), result.get("error"))
        params = result.get("params") or {}
        self.assertTrue(params.get("cash_aware_weights"))
        self.assertTrue(params.get("apply_costs"))
        self.assertTrue(params.get("stop_applied"))
        self.assertIn("stop_legs_clipped", params)
        self.assertIn("oos_fail_excluded_models", params)
        shadow = result.get("stop_shadow") or {}
        self.assertIn("ok", shadow)

    def _fake_replay(self, **overrides):
        params = {
            "engine": "paper_replay",
            "rank_enter": 0.012,
            "rank_strong": 0.012,
            "fusion_w_co": 0.0,
            "lookback": 30,
            "cost_model": "simple_cn",
        }
        params.update(overrides)
        return {
            "success": True,
            "params": params,
            "metrics": {
                "total_return_pct": 1.2,
                "win_rate_pct": 55.0,
                "trade_count": 4,
                "max_drawdown_pct": 2.0,
            },
            "equity_curve": [{"date": "2026-01-20", "equity": 1_000_000}],
            "note": "引擎=paper_replay：每个交易日 09:30 rank_lots",
        }

    def test_summarize_uses_replay_defaults(self):
        stock_bars = {
            "600519": _aligned_bars("a", n=50),
            "600036": _aligned_bars("b", n=50, step=0.35),
        }
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            return_value=self._fake_replay(),
        ) as bt_mock:
            out = summarize_portfolio_backtest(codes=["600519", "600036"])
        self.assertTrue(out.get("success"), out.get("error"))
        params = out.get("params") or {}
        self.assertEqual(out.get("engine"), "paper_replay")
        self.assertEqual(params.get("engine"), "paper_replay")
        self.assertTrue(params.get("apply_costs"))
        self.assertEqual(out.get("cost_model"), "simple_cn")
        self.assertAlmostEqual(float(params.get("rank_enter") or 0), 0.001)
        self.assertAlmostEqual(float(params.get("rank_strong") or 0), 0.001)
        self.assertEqual(float(params.get("fusion_w_co")), 0.0)
        self.assertIn("lookback", params)
        self.assertIn("research_next", out)
        self.assertIn("paper_replay", out.get("note") or "")
        self.assertIn("rank_lots", out.get("note") or "")
        kw = bt_mock.call_args.kwargs
        self.assertEqual(kw.get("top_k"), 2)
        self.assertEqual(kw.get("cost_model"), "simple_cn")
        self.assertEqual(kw.get("fusion_w_co"), 0.0)
        self.assertAlmostEqual(float(kw.get("rank_enter") or 0), 0.001)
        self.assertAlmostEqual(float(kw.get("rank_strong") or 0), 0.001)
        self.assertEqual(kw.get("lookback"), 30)

    def test_override_rank_params(self):
        stock_bars = {
            "600519": _aligned_bars("a", n=50),
            "600036": _aligned_bars("b", n=50, step=0.35),
        }
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            return_value=self._fake_replay(
                fusion_w_co=0.5, rank_enter=0.015, rank_strong=0.03
            ),
        ) as bt_mock:
            out = summarize_portfolio_backtest(
                codes=["600519", "600036"],
                fusion_w_co=0.5,
                rank_enter=0.015,
                rank_strong=0.03,
                lookback=30,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        params = out.get("params") or {}
        self.assertEqual(out.get("engine"), "paper_replay")
        self.assertEqual(float(params.get("fusion_w_co")), 0.5)
        self.assertAlmostEqual(float(params.get("rank_enter") or 0), 0.015)
        self.assertAlmostEqual(float(params.get("rank_strong") or 0), 0.03)
        kw = bt_mock.call_args.kwargs
        self.assertEqual(kw.get("fusion_w_co"), 0.5)
        self.assertAlmostEqual(float(kw.get("rank_enter") or 0), 0.015)
        self.assertAlmostEqual(float(kw.get("rank_strong") or 0), 0.03)


if __name__ == "__main__":
    unittest.main()
