"""换手计费成本单测。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.costs import (
    DEFAULT_COSTS,
    estimate_slippage,
    load_cost_config,
    rebalance_cost_pct,
    round_trip_cost_pct,
    side_cost_bps,
)


class TestTurnoverCosts(unittest.TestCase):
    def test_defaults_align_paper_stamp_and_slip_cap(self):
        cfg = load_cost_config()
        self.assertEqual(cfg["stamp_duty_bps_sell"], 5.0)
        self.assertEqual(cfg["max_slippage_bps"], 10.0)
        self.assertEqual(DEFAULT_COSTS["stamp_duty_bps_sell"], 5.0)

    def test_slippage_capped_at_max(self):
        # 极高波动 bars → 应封顶 max_slippage_bps
        bars = []
        px = 100.0
        for i in range(25):
            px *= 1.08 if i % 2 == 0 else 0.92
            bars.append({"close": px, "volume": 1e6})
        slip = estimate_slippage(bars)
        self.assertLessEqual(slip, 10.0)
        self.assertGreaterEqual(slip, float(DEFAULT_COSTS["base_slippage_bps"]))

    def test_hold_all_zero_cost(self):
        codes = ["AAA", "BBB", "CCC"]
        cost = rebalance_cost_pct(codes, codes, config={"base_slippage_bps": 3.0})
        self.assertEqual(cost, 0.0)

    def test_full_replace_near_round_trip(self):
        prev = ["A", "B", "C"]
        curr = ["D", "E", "F"]
        # 固定滑点，无 bars → 用 base_slippage
        cfg = {
            "commission_bps": 2.5,
            "stamp_duty_bps_sell": 5.0,
            "transfer_fee_bps": 0.1,
            "base_slippage_bps": 3.0,
            "max_slippage_bps": 10.0,
        }
        cost = rebalance_cost_pct(prev, curr, config=cfg)
        rt = round_trip_cost_pct(config=cfg)
        self.assertAlmostEqual(cost, rt, places=5)

    def test_one_of_three_replace_is_one_third(self):
        prev = ["A", "B", "C"]
        curr = ["A", "B", "D"]
        cfg = {
            "commission_bps": 2.5,
            "stamp_duty_bps_sell": 5.0,
            "transfer_fee_bps": 0.1,
            "base_slippage_bps": 3.0,
            "max_slippage_bps": 10.0,
        }
        cost = rebalance_cost_pct(prev, curr, config=cfg)
        full = rebalance_cost_pct(prev, ["X", "Y", "Z"], config=cfg)
        self.assertAlmostEqual(cost, full / 3.0, places=5)

    def test_first_entry_buy_only(self):
        curr = ["A", "B", "C"]
        cfg = {
            "commission_bps": 2.5,
            "stamp_duty_bps_sell": 5.0,
            "transfer_fee_bps": 0.1,
            "base_slippage_bps": 3.0,
        }
        cost = rebalance_cost_pct([], curr, config=cfg)
        buy = side_cost_bps("buy", config=cfg) / 100.0
        self.assertAlmostEqual(cost, buy, places=5)
        self.assertLess(cost, round_trip_cost_pct(config=cfg))

    def test_topk_uses_turnover_cost_mode(self):
        from core.backtest.topk_backtest import backtest_topk_equal_weight

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
        params = result.get("params") or {}
        self.assertEqual(params.get("cost_mode"), "turnover")
        self.assertGreaterEqual(float(params.get("turnover_cost_sum_pct") or 0), 0)
        # 续持为主时，累计换手成本应明显小于「每期全仓往返」粗估
        trades = result.get("trades") or []
        if len(trades) >= 2:
            naive = round_trip_cost_pct() * len(trades)
            self.assertLess(float(params["turnover_cost_sum_pct"]), naive)


if __name__ == "__main__":
    unittest.main()
