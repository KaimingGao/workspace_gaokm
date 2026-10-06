"""T4 · CostPort 单源与对齐闸门。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestT4CostPort(unittest.TestCase):
    def test_assert_aligned(self):
        from core.backtest.cost_port import assert_cost_port_aligned

        snap = assert_cost_port_aligned()
        self.assertTrue(snap["ok"])
        self.assertEqual(snap["simple_cn_fee"]["stamp_duty_bps_sell"], 5.0)
        self.assertEqual(snap["simple_cn_fee"]["commission_bps"], 2.5)

    def test_paper_and_backtest_same_stamp(self):
        from core.backtest.costs import load_cost_config
        from core.paper.costs import calc_trade_fees, cost_params

        p = cost_params({})
        bt = load_cost_config()
        self.assertAlmostEqual(p["commission_rate"] * 10000, bt["commission_bps"], places=6)
        self.assertAlmostEqual(p["stamp_duty_sell"] * 10000, bt["stamp_duty_bps_sell"], places=6)
        fee = calc_trade_fees("sell", 100_000.0, model="simple_cn")
        self.assertAlmostEqual(fee["stamp_duty"], 50.0, places=2)  # 万 5

    def test_factor_calculator_stamp_not_old_10bps(self):
        from core.signal.factors.cost import DEFAULT_COST_CONFIG, TransactionCostCalculator

        self.assertAlmostEqual(DEFAULT_COST_CONFIG["stamp_tax_rate"], 0.0005, places=6)
        calc = TransactionCostCalculator()
        _, details = calc.calculate_sell_cost(10.0, 1000, "600519")
        # 10000 * 0.0005 = 5
        self.assertAlmostEqual(details["stamp_tax"], 5.0, places=2)

    def test_topk_cost_mode_turnover(self):
        from core.backtest.cost_port import PORTFOLIO_COST_MODE
        from core.backtest.topk_backtest import backtest_topk_equal_weight

        def bars(step: float):
            out = []
            for i in range(36):
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

        result = backtest_topk_equal_weight(
            {
                "600519": bars(0.4),
                "600036": bars(0.35),
                "300750": bars(0.45),
            },
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            apply_costs=True,
            neutralize=False,
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertEqual((result.get("params") or {}).get("cost_mode"), PORTFOLIO_COST_MODE)

    def test_eval_path_cost_port(self):
        from evals.core_golden_paths import path_cost_port_aligned

        out = path_cost_port_aligned()
        self.assertTrue(out["ok"])
        self.assertEqual(out["id"], "cost_port_aligned")


if __name__ == "__main__":
    unittest.main()
