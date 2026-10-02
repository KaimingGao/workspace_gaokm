"""ŷ_oo_tree 影子头：日线面板 Holdout vs Ridge。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _synthetic_bars(n: int = 80, seed: int = 1):
    bars = []
    px = 10.0 + seed
    for i in range(n):
        d = f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}"
        o = px
        c = px * (1.0 + ((i % 7) - 3) * 0.002)
        h = max(o, c) * 1.01
        l = min(o, c) * 0.99
        bars.append(
            {
                "date": d,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
                "amount": 1e7 + i * 1e4,
            }
        )
        px = c
    return bars


class TestOoTree(unittest.TestCase):
    def test_fit_oo_tree_report_smoke(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.oo_tree import fit_oo_tree_report

        stock_bars = [
            {"code": "AAA", "bars": _synthetic_bars(90, seed=1)},
            {"code": "BBB", "bars": _synthetic_bars(90, seed=2)},
            {"code": "CCC", "bars": _synthetic_bars(90, seed=3)},
        ]
        # 因子面板依赖真实注册因子；合成 K 可能样本仍够跑通或明确失败
        report = fit_oo_tree_report(
            stock_bars,
            horizon_days=1,
            ridge_lambda=1.0,
            holdout_trading_days=8,
            include_alpha158=False,
            n_estimators=16,
            max_depth=2,
        )
        self.assertIsInstance(report, dict)
        self.assertEqual(report.get("task"), "oo_tree")
        self.assertEqual(report.get("head"), "y_oo_tree")
        if report.get("success"):
            self.assertIn("oos", report)
            self.assertIn("ridge_oos", report)
            self.assertIn("delta_vs_ridge", report)
            self.assertFalse(report.get("live_hook"))
            self.assertTrue(report.get("backtest_hook"))
            rm = report.get("tree_return_model") or {}
            self.assertTrue(rm.get("feature_names"))
            self.assertEqual(rm.get("head_kind"), "return")
            oos = report["oos"]
            self.assertGreaterEqual(int(oos.get("n_test") or 0), 1)
        else:
            # 合成数据因子覆盖不足时允许失败，但错误应可读
            self.assertTrue(report.get("error"))

    def test_save_load_last_report(self):
        import tempfile

        from core.research import oo_tree as mod

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "oo_tree_last_report.json")
            with patch.object(mod, "oo_tree_last_report_path", return_value=path):
                mod.save_oo_tree_last_report(
                    {
                        "success": True,
                        "task": "oo_tree",
                        "head": "y_oo_tree",
                        "oos": {"ic": 0.1, "n_test": 12},
                    }
                )
                loaded = mod.load_oo_tree_last_report()
                self.assertIsNotNone(loaded)
                self.assertEqual((loaded or {}).get("oos", {}).get("ic"), 0.1)


if __name__ == "__main__":
    unittest.main()
