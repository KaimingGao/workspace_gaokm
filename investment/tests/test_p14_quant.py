import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.threshold_suggest import suggest_stance_thresholds_from_watching_oos
from quant.services.quant_report_export import export_quant_report_markdown, render_quant_report_markdown
from quant.skill.engine import QuantEngine
from tests.test_p10_quant import _aligned_bars


class TestQuantSkill(unittest.TestCase):
    def test_missing_task(self):
        out = QuantEngine().run({})
        self.assertFalse(out["success"])
        self.assertIn("available_tasks", out)

    def test_daily_summary_mocked(self):
        engine = QuantEngine()
        mock_svc = MagicMock()
        mock_svc.load_last_daily.return_value = {
            "success": True,
            "weight_suggest": {"success": True, "current_weights": {"momentum": 0.4}},
        }
        engine._svc = mock_svc
        out = engine.run({"task": "daily_summary"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "daily_summary")


class TestMarkdownExport(unittest.TestCase):
    def test_render_markdown(self):
        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 2.5,
                "win_rate_pct": 55,
                "trade_count": 4,
                "loaded_stocks": ["600519"],
            },
            "weight_suggest": {
                "success": True,
                "current_weights": {"momentum": 0.4},
                "suggested_weights": {"momentum": 0.43},
                "rationale": ["momentum IC 偏高"],
            },
        }
        md = render_quant_report_markdown(report)
        self.assertIn("# 量化研究日报", md)
        self.assertIn("Top-K 回测摘要", md)
        out = export_quant_report_markdown(report)
        self.assertTrue(out["success"])
        self.assertIn("content", out)


class TestWatchingThreshold(unittest.TestCase):
    def test_watching_aggregate_offline(self):
        oos_rows = [
            {
                "success": True,
                "best_params": {"min_score": 55, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 52.0, "trade_count": 5}},
            },
            {
                "success": True,
                "best_params": {"min_score": 60, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 50.0, "trade_count": 4}},
            },
            {
                "success": True,
                "best_params": {"min_score": 65, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 48.0, "trade_count": 4}},
            },
        ]
        idx = {"i": 0}

        def fake_oos(*_args, **_kwargs):
            row = oos_rows[min(idx["i"], len(oos_rows) - 1)]
            idx["i"] += 1
            return row

        with patch("skills.common.history.fetch_daily_bars", return_value=(_aligned_bars("x"), "mock")):
            with patch("skills.common.quote_api.StockAPI.query", return_value={"success": True, "stock_code": "600519"}):
                with patch("core.backtest.engine.scan_signal_parameters_oos", side_effect=fake_oos):
                    out = suggest_stance_thresholds_from_watching_oos(
                        ["600519", "600036", "300750"],
                        lookback=80,
                        max_stocks=3,
                    )
        self.assertTrue(out["success"])
        self.assertEqual(out["watching_aggregate"]["stock_count"], 3)
        self.assertEqual(out["watching_aggregate"]["median_best_min_score"], 60.0)


if __name__ == "__main__":
    unittest.main()
