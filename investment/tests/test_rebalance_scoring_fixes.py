"""打分粗筛 / 分池 force_trim / 行业减仓 / 回撤下舆情恢复 — 回归。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestSelectForceTrimCodes(unittest.TestCase):
    def test_prefers_mid_band_over_low_score_book(self):
        from core.paper.rebalance import select_force_trim_codes

        holdings = [
            {"stock_code": "A"},
            {"stock_code": "B"},
            {"stock_code": "M1"},
            {"stock_code": "M2"},
        ]
        scores = {"A": 0.2, "B": 2.0, "M1": 1.0, "M2": 0.9}
        out = select_force_trim_codes(
            holdings,
            score_by_code=scores,
            top_codes={"A", "B"},
            trim_count=2,
        )
        self.assertEqual(out, ["M2", "M1"])


class TestScoreUniversePrefilter(unittest.TestCase):
    def test_uses_prior_yhat_not_day_change(self):
        from core.signal.cluster.rank import select_score_universe

        codes = [f"{i:06d}" for i in range(1, 301)]
        prior = {c: float(i) for i, c in enumerate(codes)}  # higher index = higher ŷ
        # 当日涨跌：低序号动量更高（若误用涨跌幅会选错）
        with patch(
            "core.signal.cluster.rank._prior_yhat_by_code",
            return_value=(prior, "ledger_yhat"),
        ), patch("core.ports.market.batch_query_quotes") as mock_q:
            selected, ranked, axis = select_score_universe(codes, cap=240)
        mock_q.assert_not_called()
        self.assertTrue(ranked)
        # S2: axis 后缀带 +unknown{X}% 配额标记（默认 20%）；语义仍以先验 ŷ 为主
        self.assertTrue(axis.startswith("ledger_yhat"), f"axis={axis}")
        self.assertIn("unknown", axis)
        self.assertEqual(len(selected), 240)
        # 先验最高的票必须入选；动量最高的低分票可被挤出
        self.assertIn("000300", selected)
        self.assertNotIn("000001", selected)


class TestEodResolveNoBlendFallback(unittest.TestCase):
    def test_score_matching_blend_is_not_eod(self):
        from core.signal.dual_score import (
            eod_gate_score_for_item,
            resolve_predicted_score_eod,
        )

        item = {
            "score": 0.525,  # ŷ_trade after align
            "predicted_score_blend": 0.525,
            "predicted_score_tau": 0.35,
        }
        self.assertIsNone(resolve_predicted_score_eod(item))
        self.assertIsNone(eod_gate_score_for_item(item))

    def test_heuristic_score_with_tau_still_gates(self):
        """仅有 0–100 ``score`` 时不得当作 ŷ_EOD（避免 OOS 失败票漏进 Top）。"""
        from core.signal.dual_score import resolve_predicted_score_eod

        self.assertIsNone(
            resolve_predicted_score_eod({"score": 72.0, "predicted_score_tau": 1.0})
        )

    def test_explicit_eod_wins_over_blend_score(self):
        from core.signal.dual_score import resolve_predicted_score_eod

        self.assertAlmostEqual(
            resolve_predicted_score_eod(
                {
                    "score": 0.525,
                    "predicted_score_blend": 0.525,
                    "predicted_score": 0.7,
                    "predicted_score_eod": 0.7,
                }
            ),
            0.7,
        )

    def test_yhat_pct_ge_10_with_scale_is_not_heuristic(self):
        """涨停板 ŷ%≥10 且标明 predicted_yhat 时，回退 score 不得丢 ŷ_EOD。"""
        from core.signal.dual_score import resolve_predicted_score_eod
        from core.signal.score_display import looks_like_legacy_heuristic_score

        item = {
            "score": 12.5,
            "score_scale": "predicted_yhat",
        }
        self.assertFalse(looks_like_legacy_heuristic_score(12.5, item=item))
        self.assertAlmostEqual(resolve_predicted_score_eod(item), 12.5)
        # 未标明尺的大数仍按遗留 0–100 拒绝
        self.assertIsNone(resolve_predicted_score_eod({"score": 12.5}))
        from quant.services.quant_report_export import _fmt_yhat, _yhat_from_row

        self.assertEqual(_fmt_yhat(12.5), "12.500%")
        self.assertAlmostEqual(_yhat_from_row(item), 12.5)
        self.assertIsNone(_yhat_from_row({"score": 55.0}))


class TestOptimizeEligibilityUsesEod(unittest.TestCase):
    def test_low_blend_high_eod_still_gets_weight(self):
        from core.portfolio_optimize import optimize_weights

        candidates = [
            {
                "stock_code": "A",
                "score": 0.525,  # blend
                "predicted_score": 0.7,
                "predicted_score_eod": 0.7,
                "predicted_score_tau": 0.35,
                "predicted_score_blend": 0.525,
                "sector": "其他",
            },
            {
                "stock_code": "B",
                "score": 0.9,
                "predicted_score": 0.9,
                "predicted_score_eod": 0.9,
                "predicted_score_tau": 0.9,
                "predicted_score_blend": 0.9,
                "sector": "其他",
            },
        ]
        out = optimize_weights(
            candidates,
            max_position_pct=50.0,
            max_sector_pct=100.0,
            max_positions=5,
            min_score=0.6,
            weight_mode="score_budget",
            apply_market_vol=False,
            apply_regime_scale=False,
        )
        weights = out.get("weights_pct") or {}
        self.assertIn("A", weights, msg="EOD≥floor 即使 blend<floor 也应入目标仓")
        self.assertIn("B", weights)


class TestTurnoverBudgetClip(unittest.TestCase):
    def test_clips_to_remaining_buy_budget(self):
        from core.paper.rebalance import clip_shares_to_turnover_budget

        # equity=100k, max_to=2% → single-side 1k；已买 800 → 剩 200
        # px=10 → 最多 20 股，取整 0 手？ 200/10=20 → 0*100
        # use px=1 → 200 股
        sh = clip_shares_to_turnover_budget(
            shares=1000,
            fill_px=1.0,
            buy_amt_so_far=800.0,
            buy_budget_amt=1000.0,
            sell_amt=0.0,
            equity_before=100000.0,
            max_turnover_pct=2.0,
        )
        self.assertEqual(sh, 200)

    def test_zero_when_budget_exhausted(self):
        from core.paper.rebalance import clip_shares_to_turnover_budget

        sh = clip_shares_to_turnover_budget(
            shares=500,
            fill_px=10.0,
            buy_amt_so_far=1000.0,
            buy_budget_amt=1000.0,
            sell_amt=0.0,
            equity_before=100000.0,
            max_turnover_pct=2.0,
        )
        self.assertEqual(sh, 0)


if __name__ == "__main__":
    unittest.main()
