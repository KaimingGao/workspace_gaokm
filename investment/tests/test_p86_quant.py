import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.mock_context import apply_case_mocks, rising_bars
from quant.research.factor_ols import compute_factor_ols_report
from quant.skill.engine import QuantEngine


class TestP86FactorOls(unittest.TestCase):
    def test_compute_factor_ols_on_mock_bars(self):
        bars = rising_bars(45)
        report = compute_factor_ols_report(bars, horizon_days=3, min_history=12)
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("task"), "factor_ols")
        self.assertIsInstance(report.get("coefficients"), dict)
        self.assertGreater(report.get("sample_count") or 0, 0)
        self.assertIn("current_weights", report)
        self.assertIn("不自动写 signal_config", report.get("note") or "")

    def test_quant_skill_factor_ols_task(self):
        engine = QuantEngine()
        bars = rising_bars(45)
        with apply_case_mocks(
            {
                "quotes": {
                    "600519": {
                        "success": True,
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "market": "CN",
                    }
                },
                "daily_bars": "rising_45",
                "resolve_market_code": ["CN", "600519"],
            }
        ):
            out = engine.run(
                {
                    "task": "factor_ols",
                    "stock_code": "600519",
                    "lookback": 45,
                    "horizon_days": 3,
                }
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("task"), "factor_ols")
        self.assertIn("coefficients", out)

    def test_factor_ols_cli_import(self):
        import research.factor_ols_run as cli

        self.assertTrue(callable(cli.main))

    def test_prepare_drops_constant_on_complete_panel(self):
        from quant.research.factor_ols import _prepare_complete_panel

        # a 在全样本有波动；完整行上 a 常数 → 应剔除
        xs = [
            {"a": 1.0, "b": 1.0, "c": 2.0},
            {"a": 9.0, "b": None, "c": 3.0},
            {"a": 1.0, "b": 2.0, "c": 4.0},
            {"a": 1.0, "b": 3.0, "c": 5.0},
            {"a": 1.0, "b": 4.0, "c": 6.0},
            {"a": 1.0, "b": 5.0, "c": 7.0},
            {"a": 1.0, "b": 6.0, "c": 8.0},
            {"a": 1.0, "b": 7.0, "c": 9.0},
            {"a": 1.0, "b": 8.0, "c": 1.0},
            {"a": 1.0, "b": 9.0, "c": 2.0},
        ]
        ys = [float(i) for i in range(len(xs))]
        xs_c, ys_c, active, excluded, meta = _prepare_complete_panel(
            xs, ys, ["a", "b", "c"], min_samples_over_p=2
        )
        self.assertIsNotNone(xs_c)
        self.assertNotIn("a", active)
        self.assertIn("a", meta["dropped_constant"])
        self.assertIn("b", active)
        self.assertIn("c", active)


if __name__ == "__main__":
    unittest.main()
