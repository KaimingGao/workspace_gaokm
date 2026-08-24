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
    def test_watching_aggregate_yhat_offline(self):
        oos_rows = [
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 0.0, "min_score": 0.0, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 52.0, "trade_count": 5}},
                "model_source": "mock",
            },
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 0.5, "min_score": 0.5, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 50.0, "trade_count": 4}},
                "model_source": "mock",
            },
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 1.0, "min_score": 1.0, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 48.0, "trade_count": 4}},
                "model_source": "mock",
            },
        ]
        idx = {"i": 0}

        def fake_yhat(*_args, **_kwargs):
            row = oos_rows[min(idx["i"], len(oos_rows) - 1)]
            idx["i"] += 1
            return row

        yhat_th = {"avoid": -0.5, "wait": 0.0, "probe": 0.3}
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(_aligned_bars("x", n=80), "mock"),
        ):
            with patch(
                "core.ports.market.query_quote",
                return_value={"success": True, "stock_code": "600519"},
            ):
                with patch(
                    "core.signal.threshold_suggest.get_stance_thresholds",
                    return_value=yhat_th,
                ):
                    with patch(
                        "core.signal.cluster.live.load_cluster_return_models_by_code",
                        return_value={},
                    ):
                        with patch(
                            "core.signal.threshold_suggest.scan_yhat_wait_oos",
                            side_effect=fake_yhat,
                        ):
                            out = suggest_stance_thresholds_from_watching_oos(
                                ["600519", "600036", "300750"],
                                lookback=80,
                                max_stocks=3,
                            )
        self.assertTrue(out["success"], out.get("error") or out)
        self.assertEqual(out["watching_aggregate"]["stock_count"], 3)
        self.assertEqual(out["watching_aggregate"]["median_best_wait"], 0.5)
        self.assertEqual(out["watching_aggregate"]["score_scale"], "predicted_yhat")
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.5)

    def test_watching_aggregate_heuristic_offline(self):
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

        heuristic_th = {"avoid": 45.0, "wait": 55.0, "probe": 68.0}
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(_aligned_bars("x", n=80), "mock"),
        ):
            with patch(
                "core.ports.market.query_quote",
                return_value={"success": True, "stock_code": "600519"},
            ):
                with patch(
                    "core.signal.threshold_suggest.get_stance_thresholds",
                    return_value=heuristic_th,
                ):
                    with patch(
                        "core.backtest.engine.scan_signal_parameters_oos",
                        side_effect=fake_oos,
                    ):
                        out = suggest_stance_thresholds_from_watching_oos(
                            ["600519", "600036", "300750"],
                            lookback=80,
                            max_stocks=3,
                        )
        self.assertTrue(out["success"], out.get("error") or out)
        self.assertEqual(out["watching_aggregate"]["stock_count"], 3)
        self.assertEqual(out["watching_aggregate"]["median_best_min_score"], 60.0)
        self.assertEqual(out["watching_aggregate"]["score_scale"], "heuristic_0_100")


if __name__ == "__main__":
    unittest.main()
