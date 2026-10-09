"""Tree 训练日滑窗 + LightGBM init_model 增量。"""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestTreeSlidingWindows(unittest.TestCase):
    def test_cross_section_zscore_flag_switches_scope(self):
        """关日截面 Z 时走训练窗全局 μ/σ（不依赖 lightgbm）。"""
        from unittest.mock import patch

        from core.research.tc_tree import fit_lgb_on_matrices

        rng = np.random.default_rng(1)
        dates = ["2024-01-01"] * 10 + ["2024-01-02"] * 10
        x = rng.normal(size=(20, 3))
        y = rng.normal(size=20)
        x_te = rng.normal(size=(4, 3))
        names = ["a", "b", "c"]

        class _Stub:
            best_iteration = None

            def num_trees(self):
                return 4

        with patch(
            "core.research.tc_tree._fit_lightgbm",
            return_value=(_Stub(), np.zeros(3)),
        ), patch(
            "core.research.tc_tree._predict_lightgbm",
            return_value=np.zeros(4),
        ):
            _m, _g, hp_cs, _means, _p, _s = fit_lgb_on_matrices(
                x,
                y,
                x_te,
                names,
                n_estimators=4,
                max_depth=2,
                learning_rate=0.2,
                subsample=1.0,
                feature_zscore=True,
                cross_section_zscore=True,
                train_dates=dates,
                test_dates=["2024-01-03"] * 4,
                window_days=0,
            )
            _m2, _g2, hp_g, means_g, _p2, _s2 = fit_lgb_on_matrices(
                x,
                y,
                x_te,
                names,
                n_estimators=4,
                max_depth=2,
                learning_rate=0.2,
                subsample=1.0,
                feature_zscore=True,
                cross_section_zscore=False,
                train_dates=dates,
                test_dates=["2024-01-03"] * 4,
                window_days=0,
            )
        self.assertEqual(hp_cs.get("zscore_scope"), "cross_section")
        self.assertTrue(hp_cs.get("cross_section_zscore"))
        self.assertFalse(hp_cs.get("zscore_means"))
        self.assertNotEqual(hp_g.get("zscore_scope"), "cross_section")
        self.assertFalse(hp_g.get("cross_section_zscore"))
        self.assertTrue(hp_g.get("zscore_means"))
        self.assertEqual(int(means_g.shape[0]), 3)

    def test_iter_sliding_day_windows_order_and_fallback(self):
        from core.research.tc_tree import iter_sliding_day_windows

        # 两票 × 5 日交错
        dates = []
        for d in ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]:
            dates.append(d)
            dates.append(d)
        wins = iter_sliding_day_windows(dates, window_days=3, step_days=2)
        self.assertGreaterEqual(len(wins), 2)
        # 首窗应覆盖前 3 日（6 行）
        self.assertEqual(int(wins[0].size), 6)
        self.assertTrue(np.all(wins[0] == np.sort(wins[0])))
        # window_days=0 → 单窗全样本
        one = iter_sliding_day_windows(dates, window_days=0, step_days=2)
        self.assertEqual(len(one), 1)
        self.assertEqual(int(one[0].size), len(dates))
        # 窗宽 ≥ 日数 → 单窗
        fat = iter_sliding_day_windows(dates, window_days=100, step_days=1)
        self.assertEqual(len(fat), 1)

    def test_split_boost_rounds(self):
        from core.research.tc_tree import _split_boost_rounds

        self.assertEqual(_split_boost_rounds(300, 3), [100, 100, 100])
        parts = _split_boost_rounds(300, 7)
        self.assertEqual(sum(parts), 300)
        self.assertEqual(len(parts), 7)
        self.assertEqual(parts[-1], max(parts))

    def test_fit_lgb_sliding_total_trees(self):
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        from core.research.tc_tree import fit_lgb_on_matrices

        rng = np.random.default_rng(0)
        n_days = 100
        n_per = 4
        dates = []
        for i in range(n_days):
            d = f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
            dates.extend([d] * n_per)
        n = len(dates)
        x = rng.normal(size=(n, 5))
        y = x[:, 0] * 0.3 + rng.normal(scale=0.1, size=n)
        x_te = rng.normal(size=(20, 5))
        names = [f"f{i}" for i in range(5)]

        _m0, _g0, hp0, _means0, preds0, _s0 = fit_lgb_on_matrices(
            x,
            y,
            x_te,
            names,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.2,
            subsample=0.9,
            feature_zscore=True,
            train_dates=dates,
            test_dates=["2024-06-01"] * 20,
            window_days=0,
            step_days=20,
        )
        self.assertEqual(int(hp0.get("window_days") or 0), 0)
        self.assertEqual(int(hp0.get("n_windows") or 0), 1)
        self.assertEqual(len(preds0), 20)

        _m1, _g1, hp1, _means1, preds1, _s1 = fit_lgb_on_matrices(
            x,
            y,
            x_te,
            names,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.2,
            subsample=0.9,
            feature_zscore=True,
            train_dates=dates,
            test_dates=["2024-06-01"] * 20,
            window_days=40,
            step_days=20,
        )
        self.assertEqual(int(hp1.get("window_days") or 0), 40)
        self.assertGreaterEqual(int(hp1.get("n_windows") or 0), 2)
        self.assertEqual(int(hp1.get("total_trees") or 0), 30)
        self.assertEqual(sum(hp1.get("rounds_per_window") or []), 30)
        self.assertEqual(len(preds1), 20)


if __name__ == "__main__":
    unittest.main()
