import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.portfolio_neutral_compare import summarize_portfolio_neutral_compare
from quant.services.quant_report_export import build_report_executive_summary
from tests.test_p10_quant import _aligned_bars


class TestP54DailyNeutralCompareSummary(unittest.TestCase):
    def test_summarize_portfolio_neutral_compare(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ):
            out = summarize_portfolio_neutral_compare(
                codes=["600519", "600036", "300750"],
                lookback=80,
                top_k=2,
                min_score=40,
                fetch_fundamentals=False,
            )
        self.assertTrue(out["success"])
        self.assertIn(out["winner"], ("neutralized", "absolute", "tie"))
        self.assertIn("delta", out)
        self.assertIsNotNone(out.get("interpretation"))

    def test_build_daily_report_includes_neutral_summary(self):
        import quant.services.quant_service as qs_mod

        mock_summary = {
            "success": True,
            "winner": "neutralized",
            "delta": {"total_return_pct": 1.5},
            "neutralized_total_return_pct": 5.0,
            "absolute_total_return_pct": 3.5,
            "interpretation": "中性化更优",
        }
        svc = qs_mod.QuantService()
        with patch.object(svc, "config_summary", return_value={"success": True}), patch.object(
            svc, "run_factor_report", return_value={"success": True, "factors": []}
        ), patch.object(
            svc, "run_factor_experiment", return_value={"success": True, "factors": []}
        ), patch.object(
            svc, "suggest_thresholds", return_value={"success": False}
        ), patch.object(
            svc, "portfolio_daily_summary", return_value={"success": True, "total_return_pct": 4.0}
        ), patch.object(
            svc, "portfolio_neutral_compare_summary", return_value=mock_summary
        ), patch.object(
            svc, "list_strategies", return_value={"success": True, "strategies": []}
        ):
            report = svc.build_daily_report(include_portfolio_backtest=True)

        self.assertIn("portfolio_neutral_compare_summary", report)
        self.assertTrue(report["portfolio_neutral_compare_summary"]["success"])

    def test_executive_summary_mentions_neutral_compare(self):
        summary = build_report_executive_summary(
            {
                "portfolio_neutral_compare_summary": {
                    "success": True,
                    "interpretation": "中性化更优",
                    "delta": {"total_return_pct": 1.2},
                }
            }
        )
        self.assertTrue(summary["success"])
        joined = " ".join(summary["bullets"])
        self.assertIn("中性化对照", joined)


if __name__ == "__main__":
    unittest.main()
