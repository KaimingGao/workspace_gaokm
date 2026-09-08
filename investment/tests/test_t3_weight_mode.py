"""T3 · TopK 权重模式单测。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.costs import rebalance_cost_pct
from core.backtest.topk_backtest import allocate_topk_weights, backtest_topk_equal_weight


class TestT3WeightMode(unittest.TestCase):
    def test_score_budget_not_equal_when_scores_differ(self):
        legs = [
            {"stock_code": "A", "score": 90.0, "sector": "消费"},
            {"stock_code": "B", "score": 60.0, "sector": "金融"},
            {"stock_code": "C", "score": 30.0, "sector": "科技"},
        ]
        w_eq, mode_eq = allocate_topk_weights(legs, weight_mode="equal")
        w_sb, mode_sb = allocate_topk_weights(
            legs, weight_mode="score_budget", max_position_pct=80.0, max_sector_pct=100.0
        )
        self.assertEqual(mode_eq, "equal")
        self.assertEqual(mode_sb, "score_budget")
        self.assertAlmostEqual(sum(w_eq.values()), 100.0, places=2)
        self.assertAlmostEqual(sum(w_sb.values()), 100.0, places=2)
        self.assertGreater(w_sb["A"], w_sb["B"])
        self.assertGreater(w_sb["B"], w_sb["C"])
        self.assertNotEqual(round(w_sb["A"], 2), round(w_eq["A"], 2))

    def test_weight_delta_cost_when_codes_unchanged(self):
        cfg = {
            "commission_bps": 2.5,
            "stamp_duty_bps_sell": 5.0,
            "transfer_fee_bps": 0.1,
            "base_slippage_bps": 3.0,
            "max_slippage_bps": 10.0,
        }
        codes = ["A", "B"]
        # 等权续持 → 0
        hold = rebalance_cost_pct(
            codes,
            codes,
            config=cfg,
            prev_weights={"A": 50.0, "B": 50.0},
            curr_weights={"A": 50.0, "B": 50.0},
        )
        self.assertEqual(hold, 0.0)
        # 同票权重漂移仍计费
        drift = rebalance_cost_pct(
            codes,
            codes,
            config=cfg,
            prev_weights={"A": 50.0, "B": 50.0},
            curr_weights={"A": 70.0, "B": 30.0},
        )
        self.assertGreater(drift, 0.0)

    def test_topk_params_disclose_weight_mode(self):
        def bars(step: float):
            out = []
            for i in range(40):
                close = 100 + i * step
                out.append(
                    {
                        "date": f"2026-01-{i + 1:02d}",
                        "open": close - 0.1,
                        "high": close + 0.5,
                        "low": close - 0.5,
                        "close": close,
                        "volume": 1_000_000,
                    }
                )
            return out

        stock_bars = {
            "600519": bars(0.4),
            "600036": bars(0.35),
            "300750": bars(0.45),
        }
        equal = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            apply_costs=False,
            neutralize=False,
            weight_mode="equal",
        )
        budget = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            apply_costs=False,
            neutralize=False,
            weight_mode="score_budget",
        )
        self.assertTrue(equal.get("success"), equal.get("error"))
        self.assertTrue(budget.get("success"), budget.get("error"))
        self.assertEqual((equal.get("params") or {}).get("weight_mode"), "equal")
        self.assertEqual((budget.get("params") or {}).get("weight_mode"), "score_budget")
        sample = (budget.get("trades") or [None])[0]
        if sample:
            legs = sample.get("legs") or []
            self.assertTrue(all("weight_pct" in leg for leg in legs))
            self.assertEqual(sample.get("weight_mode"), "score_budget")

    def test_portfolio_request_accepts_weight_mode(self):
        from web.schemas import PortfolioBacktestRequest

        body = PortfolioBacktestRequest(weight_mode="score_budget")
        self.assertEqual(body.weight_mode, "score_budget")
        self.assertEqual(body.max_position_pct, 25.0)
        self.assertEqual(body.engine, "paper_replay")
        self.assertEqual(body.y_on_alpha, 0.0)
        self.assertEqual(body.fusion_w_trade, 0.6)
        self.assertEqual(body.fusion_w_nowcast, 0.4)
        self.assertEqual(body.rank_enter, 0.012)
        self.assertEqual(body.rank_strong, 0.012)

    def test_portfolio_request_y_on_alpha_range(self):
        from pydantic import ValidationError
        from web.schemas import PortfolioBacktestRequest

        self.assertEqual(PortfolioBacktestRequest(y_on_alpha=0.5).y_on_alpha, 0.5)
        self.assertEqual(PortfolioBacktestRequest(y_on_alpha=1).y_on_alpha, 1.0)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(y_on_alpha=1.1)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(y_on_alpha=-0.1)

    def test_portfolio_request_fusion_weights(self):
        from pydantic import ValidationError
        from web.schemas import PortfolioBacktestRequest

        body = PortfolioBacktestRequest(fusion_w_trade=0.7, fusion_w_nowcast=0.3)
        self.assertEqual(body.fusion_w_trade, 0.7)
        self.assertEqual(body.fusion_w_nowcast, 0.3)
        self.assertEqual(PortfolioBacktestRequest(fusion_w_trade=0).fusion_w_trade, 0.0)
        self.assertEqual(PortfolioBacktestRequest(fusion_w_nowcast=1).fusion_w_nowcast, 1.0)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(fusion_w_trade=1.1)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(fusion_w_nowcast=-0.1)

    def test_portfolio_request_rank_thresholds(self):
        from pydantic import ValidationError
        from web.schemas import PortfolioBacktestRequest

        body = PortfolioBacktestRequest(rank_enter=0.015, rank_strong=0.03)
        self.assertEqual(body.rank_enter, 0.015)
        self.assertEqual(body.rank_strong, 0.03)
        # 旧乘数 1.01 仍可进请求体，服务层 coerce 成 0.01
        self.assertEqual(PortfolioBacktestRequest(rank_enter=1.01).rank_enter, 1.01)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(rank_enter=-0.01)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(rank_strong=10.1)
        self.assertEqual(PortfolioBacktestRequest(lookback=10).lookback, 10)
        with self.assertRaises(ValidationError):
            PortfolioBacktestRequest(lookback=9)


if __name__ == "__main__":
    unittest.main()
