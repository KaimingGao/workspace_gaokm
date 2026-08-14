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

    def test_qr_ols_recovers_known_beta(self):
        from quant.research.factor_ols import _fit_ols_once

        xs = []
        ys = []
        for i in range(30):
            a = float(i)
            b = float(i % 7) - 3.0
            xs.append({"a": a, "b": b})
            ys.append(2.0 + 3.0 * a - 1.5 * b)
        fit = _fit_ols_once(xs, ys, ["a", "b"])
        self.assertIsNotNone(fit)
        assert fit is not None
        self.assertEqual(fit.get("solver"), "qr")
        self.assertAlmostEqual(fit["intercept"], 2.0, places=4)
        self.assertAlmostEqual(fit["beta"][1], 3.0, places=4)
        self.assertAlmostEqual(fit["beta"][2], -1.5, places=4)
        self.assertAlmostEqual(fit["r_squared"] or 0.0, 1.0, places=4)

    def test_qr_ols_rejects_exact_collinear(self):
        from quant.research.factor_ols import _fit_ols_once, _ols_with_intercept

        xs = [{"a": float(i), "b": float(2 * i)} for i in range(20)]
        ys = [1.0 + 0.5 * float(i) for i in range(20)]
        self.assertIsNone(_fit_ols_once(xs, ys, ["a", "b"]))
        fit = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[])
        self.assertIsNotNone(fit)
        assert fit is not None
        self.assertEqual(fit.get("solver"), "qr")
        self.assertEqual(fit.get("active_features"), ["a"])
        self.assertIn("b", fit.get("dropped_collinear") or [])

    def test_ridge_keeps_collinear_and_shrinks(self):
        from quant.research.factor_ols import _fit_ols_once, _ols_with_intercept

        xs = []
        ys = []
        for i in range(40):
            a = float(i) + 0.01 * (i % 3)
            b = 2.0 * a + 0.02 * ((i % 5) - 2)
            xs.append({"a": a, "b": b})
            ys.append(1.0 + 0.4 * a + 0.1 * b)
        ols = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[], ridge_lambda=0.0)
        ridge = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[], ridge_lambda=5.0)
        self.assertIsNotNone(ridge)
        assert ridge is not None
        self.assertEqual(ridge.get("solver"), "ridge")
        self.assertEqual(ridge.get("ridge_lambda"), 5.0)
        self.assertEqual(set(ridge.get("active_features") or []), {"a", "b"})
        self.assertEqual(ridge.get("dropped_collinear") or [], [])
        # 精确共线时纯 OLS 会剔列；Ridge 保留两列
        exact = [{"a": float(i), "b": float(2 * i)} for i in range(20)]
        y_exact = [1.0 + 0.5 * float(i) for i in range(20)]
        self.assertIsNone(_fit_ols_once(exact, y_exact, ["a", "b"], ridge_lambda=0.0))
        ridge_exact = _fit_ols_once(exact, y_exact, ["a", "b"], ridge_lambda=1.0)
        self.assertIsNotNone(ridge_exact)
        assert ridge_exact is not None
        self.assertEqual(ridge_exact.get("solver"), "ridge")
        # 相对无正则 OLS（若可拟合），Ridge 斜率 L2 范数应更小
        if ols is not None and set(ols.get("active_features") or []) == {"a", "b"}:
            def slope_l2(fit):
                return sum(float(fit["coefficients"][k]) ** 2 for k in ("a", "b"))

            self.assertLess(slope_l2(ridge), slope_l2(ols) + 1e-9)

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
        # 测试数据量级小；关闭 min_std 只验常数列逻辑
        xs_c, ys_c, active, excluded, meta = _prepare_complete_panel(
            xs, ys, ["a", "b", "c"], min_samples_over_p=2, min_std=0.0
        )
        self.assertIsNotNone(xs_c)
        self.assertNotIn("a", active)
        self.assertIn("a", meta["dropped_constant"])
        self.assertIn("b", active)
        self.assertIn("c", active)

    def test_prepare_drops_low_variance(self):
        from quant.research.factor_ols import _prepare_complete_panel

        # low: 准常数（σ≪5）；hi1/hi2: 正常波动（保留≥2 个以免触发 relax）
        xs = []
        for i in range(20):
            xs.append(
                {
                    "low": 50.0 + (0.1 if i % 2 else -0.1),
                    "hi1": 40.0 + float(i),
                    "hi2": 60.0 - float(i) * 1.2,
                }
            )
        ys = [float(i) for i in range(len(xs))]
        _xs_c, _ys_c, active, _excl, meta = _prepare_complete_panel(
            xs, ys, ["low", "hi1", "hi2"], min_samples_over_p=2, min_std=5.0
        )
        self.assertNotIn("low", active)
        self.assertIn("hi1", active)
        self.assertIn("hi2", active)
        self.assertFalse(meta.get("min_std_relaxed"))
        names = [
            d["name"] if isinstance(d, dict) else d
            for d in (meta.get("dropped_low_variance") or [])
        ]
        self.assertIn("low", names)


if __name__ == "__main__":
    unittest.main()
