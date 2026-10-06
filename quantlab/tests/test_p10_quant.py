import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.weight_suggest import suggest_weights_from_ic
from tests.test_signal import _rising_bars


def _aligned_bars(prefix: str, n: int = 40, step: float = 0.4):
    bars = []
    for i in range(n):
        close = 100 + i * step
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 30,
            }
        )
    return bars


class TestPortfolioCrossSection(unittest.TestCase):
    def test_cross_section_portfolio_backtest(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
            use_live_cluster_models=False,
        )
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["trade_count"], 1)
        self.assertIn("metrics", result)
        self.assertEqual(result["params"]["top_k"], 2)
        self.assertIn(result["strategy"], ("cross_section_topk", "cross_section_topk_neutral"))
        self.assertGreaterEqual(len(result.get("equity_curve") or []), 2)

    def test_insufficient_common_dates(self):
        a = _rising_bars()[:10]
        b = _rising_bars()
        b[0]["date"] = "2099-01-01"
        result = backtest_topk_equal_weight(
            {"600519": a, "600036": b},
            min_history=8,
        )
        self.assertFalse(result["success"])

    def test_drops_thin_series_instead_of_collapsing_calendar(self):
        stock_bars = {
            "600519": _aligned_bars("a", n=40),
            "600036": _aligned_bars("b", n=40, step=0.35),
            "300750": _aligned_bars("c", n=40, step=0.45),
            "688825": _aligned_bars("thin", n=3),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            neutralize=False,
        )
        self.assertTrue(result["success"], result.get("error"))
        dropped = {d.get("stock_code") for d in (result.get("dropped_stocks") or [])}
        self.assertIn("688825", dropped)
        self.assertGreaterEqual(result.get("params", {}).get("common_dates") or 0, 16)


class TestWeightSuggest(unittest.TestCase):
    def test_suggest_from_positive_ic(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.12, "sample_count": 20},
                {"factor": "volume_price", "ic": -0.08, "sample_count": 20},
                {"factor": "score", "ic": 0.05, "sample_count": 20},
            ],
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "relative_strength": 0.2,
                "volatility": 0.1,
            },
            ic_mode="single",
        )
        self.assertTrue(out["success"])
        self.assertGreater(out["suggested_weights"]["momentum"], out["current_weights"]["momentum"])
        self.assertLess(out["suggested_weights"]["volume_price"], out["current_weights"]["volume_price"])
        self.assertAlmostEqual(sum(out["suggested_weights"].values()), 1.0, places=2)

    def test_weak_ic_uses_ols_then_decay(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.01, "sample_count": 20},
                {"factor": "volume_price", "ic": 0.005, "sample_count": 20},
                {"factor": "liquidity", "ic": None, "sample_count": 20},
            ],
        }
        ols = {
            "success": True,
            "coefficients": {"momentum": 0.25, "liquidity": -0.18},
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "liquidity": 0.2,
                "volatility": 0.1,
            },
            ols_report=ols,
            weak_ic_decay=0.015,
            ols_delta=0.02,
            ic_mode="single",
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["delta_sources"]["momentum"], "ols")
        self.assertEqual(out["deltas"]["momentum"], 0.02)
        self.assertEqual(out["delta_sources"]["volume_price"], "weak_ic_decay")
        self.assertEqual(out["deltas"]["volume_price"], -0.015)
        self.assertEqual(out["delta_sources"]["liquidity"], "ols")
        self.assertEqual(out["deltas"]["liquidity"], -0.02)
        self.assertNotIn("volatility", out["delta_sources"])

    def test_cs_ic_requires_icir_and_scales(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.08, "icir": 0.6, "sample_count": 40},
                {"factor": "volume_price", "ic": 0.08, "icir": 0.1, "sample_count": 40},
                {"factor": "liquidity", "ic": -0.05, "icir": -0.5, "sample_count": 40},
            ],
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "liquidity": 0.2,
                "volatility": 0.1,
            },
            ic_mode="cs_ic",
            max_delta=0.03,
            min_icir=0.25,
        )
        self.assertEqual(out["ic_mode"], "cs_ic")
        self.assertEqual(out["delta_sources"]["momentum"], "cs_ic")
        self.assertAlmostEqual(out["deltas"]["momentum"], 0.036, places=3)
        self.assertEqual(out["delta_sources"]["volume_price"], "weak_ic_decay")
        self.assertEqual(out["delta_sources"]["liquidity"], "cs_ic")
        self.assertLess(out["deltas"]["liquidity"], 0)

    def test_group_cap_and_freeze_zero(self):
        from core.signal.weight_suggest import apply_group_caps

        capped, warns = apply_group_caps(
            {
                "momentum": 0.35,
                "ma_slope": 0.2,
                "technical_pattern": 0.1,
                "weekly_confirm": 0.05,
                "value": 0.3,
            },
            {
                "trend": ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
                "value_quality": ["value"],
            },
            max_group_share=0.45,
        )
        self.assertTrue(warns)
        trend_sum = sum(
            capped[k]
            for k in ("momentum", "ma_slope", "technical_pattern", "weekly_confirm")
        )
        self.assertLessEqual(trend_sum, 0.451)
        out = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [
                    {"factor": "momentum", "ic": 0.2, "sample_count": 20},
                    {"factor": "money_flow", "ic": 0.2, "sample_count": 20},
                ],
            },
            current_weights={"momentum": 0.5, "money_flow": 0.0, "value": 0.5},
            ic_mode="single",
        )
        self.assertEqual(out["suggested_weights"]["money_flow"], 0.0)
        self.assertIn("money_flow", out["frozen_zero_factors"])


if __name__ == "__main__":
    unittest.main()
