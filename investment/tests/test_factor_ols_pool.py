import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.mock_context import rising_bars
from quant.research.factor_ols import (
    compute_factor_ols_pooled_report,
    compute_factor_ols_report,
    fit_factor_ols_from_panel,
)


class TestFactorOlsPool(unittest.TestCase):
    def test_pooled_report_stacks_two_stocks(self):
        bars_a = rising_bars(45)
        bars_b = rising_bars(45)
        # 轻微扰动第二只，避免完全共线
        for i, row in enumerate(bars_b):
            row["close"] = float(row["close"]) * (1.0 + 0.001 * (i % 7))
        out = compute_factor_ols_pooled_report(
            [
                {"code": "600519", "bars": bars_a},
                {"code": "000001", "bars": bars_b},
            ],
            horizon_days=3,
        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out.get("task"), "factor_ols_pool")
        self.assertEqual(out.get("mode"), "watching_pooled")
        self.assertEqual(out.get("stock_count"), 2)
        self.assertIn("coefficients", out)
        self.assertIn("堆叠", out.get("note") or "")

    def test_pooled_needs_two_stocks(self):
        out = compute_factor_ols_pooled_report(
            [{"code": "600519", "bars": rising_bars(45)}],
            horizon_days=3,
        )
        self.assertFalse(out.get("success"))
        self.assertEqual(out.get("task"), "factor_ols_pool")

    def test_single_report_still_works(self):
        report = compute_factor_ols_report(rising_bars(45), horizon_days=3, min_history=12)
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("mode"), "single")
        self.assertEqual(report.get("task"), "factor_ols")
        self.assertIn("exclusion_reasons", report)
        self.assertIsInstance(report["exclusion_reasons"], dict)

    def test_exclusion_reasons_for_constant_factor(self):
        from quant.research.factor_ols import _exclusion_reasons_map

        reasons = _exclusion_reasons_map(
            {
                "dropped_sparse": ["money_flow"],
                "dropped_constant": ["volatility"],
                "dropped_for_coverage": ["growth"],
            },
            dropped_collinear=["momentum"],
            excluded=["other_factor"],
        )
        self.assertEqual(reasons["money_flow"], "sparse")
        self.assertEqual(reasons["volatility"], "constant")
        self.assertEqual(reasons["growth"], "coverage")
        self.assertEqual(reasons["momentum"], "collinear")
        self.assertEqual(reasons["other_factor"], "other")

    def test_fit_from_empty_panel(self):
        out = fit_factor_ols_from_panel([], [], horizon_days=3)
        self.assertFalse(out.get("success"))

    def test_api_factor_ols_pool_mocked(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "task": "factor_ols_pool",
            "mode": "watching_pooled",
            "stock_count": 3,
            "r_squared": 0.15,
            "sample_count": 80,
            "coefficients": {"momentum": 0.1},
            "current_weights": {"momentum": 0.28},
        }
        with patch.object(
            deps.quant,
            "run_factor_ols_pool_experiment",
            return_value=mock,
        ):
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/factor-ols-pool",
                json={"lookback": 120, "horizon_days": 3, "watching_limit": 8},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data.get("mode"), "watching_pooled")

    def test_research_panel_computes_beyond_regime_core(self):
        """全量注册因子应出现在面板（不因 regime 白名单整列变 None）。"""
        from quant.research.factor_ols import collect_subscore_forward_panel

        xs, ys = collect_subscore_forward_panel(rising_bars(50), horizon_days=3)
        self.assertGreater(len(ys), 10)
        # 扩展因子在价量 mock 上应有观测（非 regime 导致的全缺测）
        for name in ("technical_pattern", "ma_slope", "gap_risk", "amihud"):
            present = sum(1 for row in xs if row.get(name) is not None)
            self.assertGreater(present, 0, f"{name} should not be all-missing")

    def test_fit_marks_standardized(self):
        report = compute_factor_ols_report(rising_bars(50), horizon_days=3, min_history=12)
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertTrue(report.get("standardized"))
        self.assertIn("z-score", (report.get("note") or "").lower())
        self.assertIn("regime", (report.get("note") or "").lower())

    def test_panel_has_pool_button(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-ols-pool-run", html)
        self.assertIn("quant-ridge-lambda", html)
        self.assertIn("研究池 OLS", html)
        self.assertIn("堆叠", html)

    def test_js_calls_pool_api(self):
        path = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("/api/quant/factor-ols-pool", js)
        self.assertIn("runFactorOlsPoolSuggest", js)


if __name__ == "__main__":
    unittest.main()
