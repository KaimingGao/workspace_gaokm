import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.threshold_suggest import (
    format_threshold_config_diff,
    suggest_stance_thresholds_from_oos,
)
from quant.services.quant_interpret import compact_quant_report, interpret_quant_report


class TestThresholdSuggest(unittest.TestCase):
    def test_suggest_from_oos_best_min_score(self):
        oos = {
            "success": True,
            "best_params": {"min_score": 60, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 5, "win_rate_pct": 52.0}},
            "split": {"train_end": 20, "valid_end": 30, "test_end": 40},
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": 45, "wait": 55, "probe": 68},
            max_delta=3.0,
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out.get("score_scale"), "heuristic")
        self.assertGreaterEqual(out["suggested_thresholds"]["wait"], 55)

    def test_predicted_scale_skips_heuristic_oos(self):
        oos = {
            "success": True,
            "score_scale": "heuristic_0_100",
            "best_params": {"min_score": 50, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 10, "win_rate_pct": 58.66}},
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": -0.5, "wait": 0.0, "probe": 0.3},
            max_delta=3.0,
        )
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped_apply"))
        self.assertEqual(out.get("score_scale"), "predicted")
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.0)
        self.assertEqual(out["suggested_thresholds"]["probe"], 0.3)
        self.assertEqual(out["deltas"]["wait"], 0.0)
        self.assertIn("ŷ%", (out.get("rationale") or [""])[0])
        diff = format_threshold_config_diff(out)
        self.assertTrue(diff["success"])
        self.assertTrue(diff.get("skipped_apply"))
        self.assertEqual(diff.get("patch"), {})
        self.assertEqual(diff.get("changes"), {})

    def test_yhat_oos_suggests_wait_step(self):
        oos = {
            "success": True,
            "score_scale": "predicted_yhat",
            "best_params": {"wait": 0.5, "min_score": 0.5, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 8, "win_rate_pct": 55.0}},
            "model_source": "fit_train",
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": -0.5, "wait": 0.0, "probe": 0.3},
            max_delta=0.5,
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.5)
        self.assertGreater(out["deltas"]["wait"], 0)
        diff = format_threshold_config_diff(out)
        self.assertTrue(diff["success"])
        self.assertFalse(diff.get("skipped_apply"))
        self.assertIn("stance_thresholds", diff["patch"])

    def test_format_threshold_diff(self):
        suggestion = suggest_stance_thresholds_from_oos(
            {
                "success": True,
                "best_params": {"min_score": 62},
                "test": {"metrics": {"trade_count": 6, "win_rate_pct": 48}},
            },
            current_thresholds={"avoid": 45, "wait": 55, "probe": 68},
        )
        diff = format_threshold_config_diff(suggestion)
        self.assertTrue(diff["success"])
        self.assertIn("stance_thresholds", diff["patch"])


class TestQuantInterpret(unittest.TestCase):
    def test_compact_report(self):
        report = {
            "success": True,
            "weight_suggest": {
                "success": True,
                "current_weights": {"momentum": 0.4},
                "suggested_weights": {"momentum": 0.43},
                "rationale": ["x"],
            },
            "portfolio_backtest_summary": {"success": True, "total_return_pct": 1.2},
        }
        compact = compact_quant_report(report)
        self.assertIn("weight_suggest", compact)
        self.assertIn("portfolio_backtest_summary", compact)

    def test_interpret_mocked_llm(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {
            "choices": [{"message": {"content": "1. 因子 IC 平稳\n2. 纸面略低于回测"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        llm.get_response_content.return_value = "1. 因子 IC 平稳\n2. 纸面略低于回测"

        out = interpret_quant_report({"success": True}, llm_client=llm)
        self.assertTrue(out["success"])
        self.assertIn("interpretation", out)


if __name__ == "__main__":
    unittest.main()
