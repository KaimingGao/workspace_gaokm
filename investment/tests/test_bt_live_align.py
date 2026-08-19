"""回测/实盘口径对齐与空仓可解释等迭代修复。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestBacktestDefaultsAlign(unittest.TestCase):
    def test_defaults_match_short_spec(self):
        from core.strategy import backtest_portfolio_defaults, get_strategy_spec

        d = backtest_portfolio_defaults("short")
        spec = get_strategy_spec("short")
        risk = spec["risk"]
        self.assertEqual(d["weight_mode"], "score_budget")
        self.assertAlmostEqual(d["max_position_pct"], float(risk["max_position_pct"]))
        self.assertAlmostEqual(d["max_sector_pct"], float(risk["max_sector_pct"]))
        self.assertEqual(d["top_k"], 3)
        self.assertEqual(d["max_positions"], int(risk["max_positions"]))
        self.assertTrue(d["exclude_st"])
        self.assertIn("weight_mode", risk)


class TestAllocateNoRenorm(unittest.TestCase):
    def test_score_budget_keeps_cash(self):
        from core.backtest.topk_backtest import allocate_topk_weights

        legs = [
            {"stock_code": "A", "score": 90, "sector": "银行", "return_pct": 1},
            {"stock_code": "B", "score": 80, "sector": "银行", "return_pct": 1},
            {"stock_code": "C", "score": 70, "sector": "白酒", "return_pct": 1},
        ]
        w, mode = allocate_topk_weights(
            legs,
            weight_mode="score_budget",
            max_position_pct=25.0,
            max_sector_pct=40.0,
        )
        self.assertEqual(mode, "score_budget")
        self.assertLessEqual(sum(w.values()) + 1e-6, 100.0)

    def test_port_return_uses_cash_base(self):
        from core.backtest.topk_backtest import _weighted_port_return

        legs = [
            {"stock_code": "A", "return_pct": 4.0},
            {"stock_code": "B", "return_pct": 4.0},
        ]
        ret = _weighted_port_return(legs, {"A": 25.0, "B": 25.0})
        self.assertAlmostEqual(ret, 2.0, places=4)


class TestForceTrimSellable(unittest.TestCase):
    def test_skips_blocked_picks_next(self):
        from core.paper_rebalance import select_force_trim_codes_sellable

        holdings = [
            {"stock_code": "600001"},
            {"stock_code": "600002"},
            {"stock_code": "600003"},
        ]
        scores = {"600001": 10, "600002": 20, "600003": 30}

        def block(code, quote):
            return "limit_down" if code == "600001" else None

        codes, blocked = select_force_trim_codes_sellable(
            holdings,
            score_by_code=scores,
            top_codes=set(),
            trim_count=2,
            quote_cache={},
            sell_block_fn=block,
        )
        self.assertEqual(codes, ["600002", "600003"])
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["stock_code"], "600001")


class TestHysteresisTracks(unittest.TestCase):
    def test_hold_clamped_to_buy(self):
        from core.signal.rebalance_tracks import predicted_floors

        buy, hold = predicted_floors(
            {
                "predicted_buy_floor": 1.0,
                "predicted_hold_floor": 2.0,
            }
        )
        self.assertEqual(buy, 1.0)
        self.assertEqual(hold, 1.0)


class TestInteractionNoFakeNeutral(unittest.TestCase):
    def test_missing_factors_no_bonus(self):
        from core.signal.scorer import _interaction_bonus

        self.assertEqual(_interaction_bonus({}), 0.0)
        # only momentum present → no mom×vp
        self.assertEqual(_interaction_bonus({"momentum": 80}), 0.0)
        self.assertGreater(
            _interaction_bonus({"momentum": 80, "volume_price": 80}), 0.0
        )


class TestDualScoreHeadMeta(unittest.TestCase):
    def test_single_head_tag(self):
        from core.signal.dual_score import fuse_remaining_heads_meta

        m = fuse_remaining_heads_meta(1.0, None)
        self.assertEqual(m["dual_score_head"], "single_eod")
        self.assertTrue(m["single_head"])
        m2 = fuse_remaining_heads_meta(1.0, 0.5)
        self.assertEqual(m2["dual_score_head"], "blend")

    def test_book_fields_exposes_head(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "predicted_score_eod_rem": 1.0,
                "predicted_score_tau": None,
                "dual_score_head": "single_eod",
                "dual_score_single_head": True,
            }
        )
        self.assertEqual(out.get("dual_score_head"), "single_eod")
        self.assertTrue(out.get("dual_score_single_head"))


class TestStableRank(unittest.TestCase):
    def test_code_breaks_ties(self):
        from core.signal.rank_stable import stable_rank_key

        rows = [("B", 1.0), ("A", 1.0), ("C", 2.0)]
        rows.sort(key=lambda x: stable_rank_key(x[1], x[0]))
        self.assertEqual([r[0] for r in rows], ["C", "A", "B"])


class TestOosGateSymmetry(unittest.TestCase):
    def test_predicted_floor_not_zero(self):
        from core.signal.weight_oos_gate import (
            _predicted_oos_floor,
            _heuristic_oos_floor,
            _shared_oos_backtest_kwargs,
        )

        self.assertGreater(_predicted_oos_floor(), 0.0)
        self.assertGreaterEqual(_heuristic_oos_floor(55.0), 55.0)
        shared = _shared_oos_backtest_kwargs(top_k=3, horizon_days=3)
        self.assertFalse(shared["apply_tau_buy_gate"])
        self.assertEqual(shared["weight_mode"], "equal")


class TestRegimeBlend(unittest.TestCase):
    def test_near_threshold_blends(self):
        from core.signal.regime import assess_regime

        # 造约 -3.05% 的 20 日收益（贴 weak 阈值）
        bars = []
        p = 100.0
        daily = (1.0 - 0.0305) ** (1 / 20) - 1.0
        for i in range(21):
            if i:
                p *= 1.0 + daily
            bars.append({"date": f"2026-01-{i+1:02d}", "close": p})
        soft = assess_regime(
            bars,
            {
                "enabled": True,
                "weak_trend_threshold_pct": -3.0,
                "blend_width_pct": 1.0,
            },
        )
        hard = assess_regime(
            bars,
            {
                "enabled": True,
                "weak_trend_threshold_pct": -3.0,
                "blend_width_pct": 0.0,
            },
        )
        self.assertIn(soft["regime"], ("weak", "neutral", "bear"))
        if soft.get("blend", {}).get("blended"):
            self.assertEqual(soft["adjustments"].get("disabled_factors"), [])
            # 软插值罚分应介于硬档之间或等于
            self.assertIsInstance(soft["score_penalty"], float)
        # 硬模式关闭混合
        self.assertFalse(hard.get("blend", {}).get("blended"))
