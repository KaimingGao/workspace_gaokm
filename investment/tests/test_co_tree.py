"""ŷ_co_tree 影子头：隔夜缺口面板 Holdout vs Ridge。"""

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


class TestCoTree(unittest.TestCase):
    def test_fit_co_tree_report_smoke(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.co_tree import fit_co_tree_report

        stock_bars = [
            {"code": "AAA", "bars": _synthetic_bars(90, seed=1)},
            {"code": "BBB", "bars": _synthetic_bars(90, seed=2)},
            {"code": "CCC", "bars": _synthetic_bars(90, seed=3)},
            {"code": "DDD", "bars": _synthetic_bars(90, seed=4)},
        ]
        report = fit_co_tree_report(
            stock_bars,
            ridge_lambda=1.0,
            holdout_trading_days=8,
            include_alpha158=False,
            backend="lightgbm",
        )
        self.assertTrue(report.get("success"), report)
        self.assertEqual(report.get("task"), "co_tree")
        self.assertEqual(report.get("head"), "y_co_tree")
        self.assertFalse(report.get("live_hook"))
        self.assertFalse(report.get("backtest_hook"))
        self.assertIn("oos", report)
        self.assertIn("ridge_oos", report)
        self.assertIn("delta_vs_ridge", report)
        self.assertGreaterEqual(int(report.get("sample_count") or 0), 20)
        self.assertEqual((report.get("oos") or {}).get("label"), "open[T+1]/close[T]-1")

    def test_save_load_last_report(self):
        import tempfile

        from core.research import co_tree as mod

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "co_tree_last_report.json")
            with patch.object(mod, "co_tree_last_report_path", return_value=path):
                mod.save_co_tree_last_report(
                    {
                        "success": True,
                        "task": "co_tree",
                        "head": "y_co_tree",
                        "sample_count": 42,
                    }
                )
                loaded = mod.load_co_tree_last_report()
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.get("sample_count"), 42)


if __name__ == "__main__":
    unittest.main()
