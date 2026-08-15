"""raw_basis 特征编码与影子对照。"""

from __future__ import annotations

import unittest


class TestRawBasis(unittest.TestCase):
    def test_apply_encoding_replaces_three(self):
        from core.signal.factors.raw_basis import apply_feature_encoding

        out = apply_feature_encoding(
            ["momentum", "volatility", "value", "quality"],
            encoding="raw_basis",
        )
        self.assertNotIn("momentum", out)
        self.assertNotIn("volatility", out)
        self.assertNotIn("value", out)
        self.assertIn("mom3_pct", out)
        self.assertIn("atr_pct_sq", out)
        self.assertIn("value_fair", out)
        self.assertIn("quality", out)

    def test_heuristic_keeps_names(self):
        from core.signal.factors.raw_basis import apply_feature_encoding

        names = ("momentum", "volatility", "value")
        self.assertEqual(
            apply_feature_encoding(names, encoding="heuristic"),
            names,
        )

    def test_extract_momentum_overheat(self):
        from core.signal.factors.raw_basis import extract_momentum_basis

        bars = []
        px = 100.0
        for i in range(10):
            # 制造约 +8% / 3日
            px = 100.0 * (1.0 + 0.03 * i)
            bars.append(
                {
                    "date": f"2026-01-{i + 1:02d}",
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": 1e6,
                }
            )
        d = extract_momentum_basis(bars)
        self.assertIsNotNone(d["mom3_pct"])
        if float(d["mom3_pct"]) > 6:
            self.assertEqual(d["mom_overheat"], 1.0)

    def test_panel_raw_encoding_keys(self):
        from core.research.panel import collect_subscore_forward_panel

        bars = []
        px = 10.0
        for i in range(40):
            px *= 1.01
            bars.append(
                {
                    "date": f"2026-02-{(i % 28) + 1:02d}",
                    "open": px,
                    "high": px * 1.01,
                    "low": px * 0.99,
                    "close": px,
                    "volume": 1e6,
                    "amount": 1e7,
                }
            )
        xs, ys, dates = collect_subscore_forward_panel(
            bars,
            horizon_days=1,
            min_history=12,
            feature_encoding="raw_basis",
        )
        self.assertGreater(len(ys), 5)
        keys = set(xs[0].keys())
        self.assertIn("mom3_pct", keys)
        self.assertNotIn("momentum", keys)

    def test_shadow_compare_synthetic(self):
        from core.research.feature_encoding_shadow import compare_feature_encoding_shadow

        def _bars(n=50, drift=0.01):
            out = []
            px = 10.0
            for i in range(n):
                px *= 1.0 + drift + (0.002 if i % 5 == 0 else -0.001)
                out.append(
                    {
                        "date": f"2026-03-{(i % 28) + 1:02d}",
                        "open": px,
                        "high": px * 1.02,
                        "low": px * 0.98,
                        "close": px,
                        "volume": 1e6,
                        "amount": px * 1e6,
                    }
                )
            return out

        stock_bars = [
            {"code": "A", "bars": _bars(55, 0.008)},
            {"code": "B", "bars": _bars(55, 0.012)},
            {"code": "C", "bars": _bars(55, 0.005)},
        ]
        out = compare_feature_encoding_shadow(
            stock_bars, horizon_days=1, ridge_lambda=1.0, train_frac=0.7
        )
        self.assertTrue(out.get("success"), out)
        self.assertIn("heuristic", out.get("arms") or {})
        self.assertIn("raw_basis", out.get("arms") or {})


if __name__ == "__main__":
    unittest.main()
