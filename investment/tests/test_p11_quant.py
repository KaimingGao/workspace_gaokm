import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.paper.rebalance import compute_turnover_stats
from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic
from tests.test_p10_quant import _aligned_bars


class TestEquityCurve(unittest.TestCase):
    def test_portfolio_backtest_has_equity_curve(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
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
        curve = result.get("equity_curve") or []
        self.assertGreaterEqual(len(curve), 2)
        self.assertEqual(curve[0]["equity"], 100.0)
        self.assertIn("date", curve[-1])

    def test_portfolio_backtest_sim_trades_legs(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
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
        sim = result.get("sim_trades") or []
        self.assertGreater(len(sim), 0)
        self.assertEqual(result.get("sim_trade_count"), len(sim))
        filled = [r for r in sim if r.get("status") == "filled"]
        self.assertGreater(len(filled), 0)
        row = filled[0]
        for key in (
            "stock_code",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "return_pct",
            "factor_weights_note",
            "score_formula",
            "score_formula_terms",
            "factor_coefficients",
            "return_model_source",
        ):
            self.assertIn(key, row)
        self.assertNotIn("predicted_score_cal", row)
        self.assertNotIn("score_calibration_applied", row)
        self.assertIsNotNone(row.get("entry_price"))
        self.assertTrue(str(row.get("factor_weights_note") or ""))
        # next_open：意图价→开盘价应还原缺口，ŷ_oo_rem 不再等于裸 ŷ_oo
        if row.get("intent_price") and row.get("entry_price") and row.get("predicted_score") is not None:
            self.assertIsNotNone(row.get("realized_t1_to_tau"))
            self.assertIsNotNone(row.get("predicted_score_eod_rem"))
            if abs(float(row["realized_t1_to_tau"])) > 1e-9:
                self.assertNotAlmostEqual(
                    float(row["predicted_score_eod_rem"]),
                    float(row["predicted_score"]),
                    places=5,
                )
        # walk-forward 拟合成功后应有分项拆解；样本过短时公式可空
        if row.get("score_formula_terms"):
            self.assertIn("terms", row["score_formula_terms"])
            self.assertTrue(row.get("factor_coefficients"))
        elif row.get("score_formula"):
            self.assertIn("ŷ", str(row.get("score_formula")))
        self.assertNotIn("weight_pct", row)


class TestWeightDiff(unittest.TestCase):
    def test_format_weight_config_diff(self):
        suggestion = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [
                    {"factor": "momentum", "ic": 0.1, "sample_count": 20},
                    {"factor": "volume_price", "ic": -0.06, "sample_count": 20},
                ],
            },
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "relative_strength": 0.2,
                "volatility": 0.1,
            },
        )
        diff = format_weight_config_diff(suggestion)
        self.assertTrue(diff["success"])
        self.assertIn("patch", diff)
        self.assertIn("weights", diff["patch"])
        self.assertIn("apply_note", diff)


class TestTurnoverStats(unittest.TestCase):
    def test_two_way_turnover_and_over_limit(self):
        sell = [{"amount": 10000}]
        buy = [{"amount": 10000}, {"amount": 10000}]
        t = compute_turnover_stats(
            sell, buy, equity_before=100000.0, max_turnover_pct=10.0
        )
        self.assertEqual(t["turnover_pct"], 15.0)
        self.assertTrue(t["over_limit"])
        self.assertEqual(t["buy_amount"], 20000.0)
        self.assertEqual(t["sell_amount"], 10000.0)

    def test_rebalance_floors_are_json_safe(self):
        """未设 ŷ 门槛时内部用 -inf，API 落盘必须是 null（Starlette allow_nan=False）。"""
        import json
        from core.signal.score_display import json_safe, json_safe_number

        self.assertIsNone(json_safe_number(float("-inf")))
        self.assertIsNone(json_safe_number(float("nan")))
        payload = json_safe(
            {
                "min_score": float("-inf"),
                "min_hold_score": float("inf"),
                "last_optimize": {"limits": {"min_score": float("-inf")}},
            }
        )
        json.dumps(payload, allow_nan=False)
        self.assertIsNone(payload["min_score"])
        self.assertIsNone(payload["last_optimize"]["limits"]["min_score"])


if __name__ == "__main__":
    unittest.main()
