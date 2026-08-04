"""权重坐标网格搜索 / 三臂对照（研究探针）。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import load_signal_config
from core.signal.weight_coordinate_search import (
    compare_weight_arms,
    coordinate_grid_search,
    set_coordinate_weight,
)


class TestSetCoordinateWeight(unittest.TestCase):
    def test_renorm_keeps_simplex_and_freeze(self):
        base = {"momentum": 0.4, "value": 0.4, "money_flow": 0.0, "quality": 0.2}
        out = set_coordinate_weight(
            base,
            "momentum",
            0.5,
            free_keys=["momentum", "value", "quality"],
            frozen_keys=["money_flow"],
            all_keys=list(base.keys()),
        )
        self.assertAlmostEqual(sum(out.values()), 1.0, places=3)
        self.assertEqual(out["money_flow"], 0.0)
        self.assertAlmostEqual(out["momentum"], 0.5, places=3)


class TestCoordinateGridSearch(unittest.TestCase):
    def test_search_prefers_high_momentum_on_is(self):
        bars = {
            "a": [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 40)],
            "b": [{"date": f"2024-01-{i:02d}", "close": 20.0} for i in range(1, 40)],
            "c": [{"date": f"2024-01-{i:02d}", "close": 30.0} for i in range(1, 40)],
        }
        base = {"momentum": 0.2, "value": 0.8, "money_flow": 0.0}

        def fake_bt(stock_bars, **kwargs):
            cfg = load_signal_config()
            mom = float((cfg.get("weights") or {}).get("momentum") or 0)
            # 高 momentum → IS 段更强；OOS 故意偏弱，验证目标用 IS
            if mom >= 0.35:
                curve = [
                    {"date": "d1", "equity": 100},
                    {"date": "d2", "equity": 110},
                    {"date": "d3", "equity": 120},
                    {"date": "d4", "equity": 118},
                ]
            else:
                curve = [
                    {"date": "d1", "equity": 100},
                    {"date": "d2", "equity": 101},
                    {"date": "d3", "equity": 102},
                    {"date": "d4", "equity": 108},
                ]
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": curve[-1]["equity"] - 100,
                    "max_drawdown_pct": 2.0,
                    "win_rate_pct": 50.0,
                    "trade_count": 3,
                },
                "equity_curve": curve,
                "trades": [],
            }

        with patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            side_effect=fake_bt,
        ):
            out = coordinate_grid_search(
                bars,
                base,
                top_k=2,
                horizon_days=3,
                min_score=0.0,
                grid=(0.1, 0.2, 0.4, 0.5),
                n_sweeps=1,
                max_evals=20,
                apply_caps=False,
            )
        self.assertTrue(out["success"])
        self.assertGreaterEqual(float(out["weights"]["momentum"]), 0.35)
        self.assertEqual(out["weights"]["money_flow"], 0.0)
        self.assertFalse(out["promote_ready"])


class TestCompareWeightArms(unittest.TestCase):
    def test_three_arms_table(self):
        def fake_load(codes, lookback=90, fetch_fundamentals=False):
            bars = {
                "a": [{"date": f"2024-01-{i:02d}", "close": 10 + i} for i in range(1, 50)],
                "b": [{"date": f"2024-01-{i:02d}", "close": 20 + i} for i in range(1, 50)],
                "c": [{"date": f"2024-01-{i:02d}", "close": 30 + i} for i in range(1, 50)],
            }
            return bars, [], {}

        def fake_bt(stock_bars, **kwargs):
            cfg = load_signal_config()
            w = cfg.get("weights") or {}
            mom = float(w.get("momentum") or 0)
            base_eq = 100 + mom * 20
            curve = [
                {"date": "d1", "equity": 100},
                {"date": "d2", "equity": 100 + (base_eq - 100) * 0.5},
                {"date": "d3", "equity": base_eq},
                {"date": "d4", "equity": base_eq - 1},
            ]
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": curve[-1]["equity"] - 100,
                    "max_drawdown_pct": 1.0,
                    "win_rate_pct": 50.0,
                    "trade_count": 2,
                },
                "equity_curve": curve,
                "trades": [],
            }

        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            side_effect=fake_load,
        ), patch(
            "core.watching_store.read_watching",
            return_value={"watchlist": ["a", "b", "c"]},
        ), patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            side_effect=fake_bt,
        ), patch(
            "core.signal.weight_coordinate_search.load_signal_config",
            return_value={
                "weights": {"momentum": 0.3, "value": 0.7, "money_flow": 0.0},
                "factor_groups": {},
            },
        ):
            out = compare_weight_arms(
                lookback=60,
                top_k=2,
                min_score=0.0,
                max_evals=12,
                n_sweeps=1,
                grid=(0.2, 0.4, 0.6),
                ols_suggested_weights={"momentum": 0.45, "value": 0.55, "money_flow": 0.0},
            )
        self.assertTrue(out["success"])
        self.assertFalse(out["promote_ready"])
        labels = [a["label"] for a in out["arms"]]
        self.assertEqual(labels, ["global", "ols_ic_suggest", "coordinate_search"])
        self.assertTrue(all(a.get("success") for a in out["arms"]))


if __name__ == "__main__":
    unittest.main()
