"""ŷ_oo_rank LambdaRank 截面名次测试。

未安装 lightgbm 时全部 skip。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _pack_day(date_s, codes, xs, ys):
    names = list(xs[0].keys()) if xs else []
    X = np.array(
        [
            [float(row[n]) if row.get(n) is not None else np.nan for n in names]
            for row in xs
        ],
        dtype=np.float64,
    )
    return {"date": date_s, "codes": codes, "ys": ys, "X": X, "names": names}


def _synth_days(n_days: int = 30, n_names: int = 20, seed: int = 0):
    """特征与 y 正相关的合成截面日（mom3 强正、vol_penalty 负）。"""
    days = []
    day = date(2024, 1, 2)
    made = 0
    d = 0
    while made < n_days:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        xs = []
        ys = []
        codes = []
        for i in range(n_names):
            signal = (i - (n_names - 1) / 2.0) / float(n_names)
            noise = (((i + 1) * (d + 3 + seed) * 17) % 11) / 100.0 - 0.05
            y = signal * 8.0 + noise * 3.0
            xs.append(
                {
                    "mom3": 50.0 + signal * 40.0,
                    "volume_ratio": 50.0 + signal * 20.0,
                    "vol_penalty_score": 50.0 - signal * 15.0,
                }
            )
            ys.append(y)
            codes.append(f"{i:06d}")
        days.append(_pack_day(day.isoformat(), codes, xs, ys))
        day += timedelta(days=1)
        made += 1
        d += 1
    return days


@unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
class TestOoRankLightgbmLambda(unittest.TestCase):
    def test_fit_lambdarank_success(self):
        from core.research.oo_rank_lambdarank import fit_lambdarank

        days = _synth_days(30, 20)
        fit = fit_lambdarank(
            days,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit.get("error"))
        self.assertEqual(fit.get("solver"), "lambdarank")
        self.assertIn("booster_b64", fit)
        self.assertTrue(fit.get("booster_b64"))
        # coefficients 为 feature importance
        coefs = fit.get("coefficients") or {}
        self.assertIn("mom3", coefs)
        active = fit.get("active_features") or []
        self.assertIn("mom3", active)

    def test_predict_round_trip(self):
        """训练后 predict_oo_rank_from_features 对高/低 mom3 的排序方向正确。"""
        from core.research.oo_rank_lambdarank import (
            fit_lambdarank,
            predict_oo_rank_from_features,
        )

        days = _synth_days(30, 20, seed=1)
        fit = fit_lambdarank(
            days,
            n_estimators=40,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit.get("error"))
        from core.research.oo_rank_lambdarank import apply_oo_rank_scores

        ranked = apply_oo_rank_scores(
            [
                {
                    "stock_code": "hi",
                    "sub_scores": {
                        "mom3": 80.0,
                        "volume_ratio": 70.0,
                        "vol_penalty_score": 30.0,
                    },
                },
                {
                    "stock_code": "lo",
                    "sub_scores": {
                        "mom3": 20.0,
                        "volume_ratio": 40.0,
                        "vol_penalty_score": 60.0,
                    },
                },
            ],
            fit=fit,
            assign_day_ranks=False,
        )
        hi = ranked[0].get("y_oo_rank")
        lo = ranked[1].get("y_oo_rank")
        self.assertIsNotNone(hi)
        self.assertIsNotNone(lo)
        self.assertGreater(float(hi), float(lo))

    def test_predict_handles_missing_features(self):
        """缺特征时按 z=0 填补，不返回 None。"""
        from core.research.oo_rank_lambdarank import (
            fit_lambdarank,
            predict_oo_rank_from_features,
        )

        days = _synth_days(30, 20, seed=2)
        fit = fit_lambdarank(
            days,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit.get("error"))
        # 缺 volume_ratio
        out = predict_oo_rank_from_features({"mom3": 50.0}, fit=fit)
        self.assertIsNotNone(out)

    def test_day_scores_use_booster(self):
        from core.research.oo_rank_lambdarank import (
            _day_scores,
            fit_lambdarank,
            predict_oo_rank_from_features,
        )

        days = _synth_days(30, 20, seed=4)
        fit = fit_lambdarank(
            days,
            n_estimators=30,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit.get("error"))
        day = days[-1]
        preds, _ys = _day_scores(day, fit)
        names = list(day.get("names") or [])
        X = day.get("X")
        row = {
            names[j]: float(X[0, j])
            for j in range(len(names))
            if X is not None and names
        }
        boost = predict_oo_rank_from_features(row, fit=fit)
        self.assertIsNotNone(preds[0])
        self.assertIsNotNone(boost)
        self.assertAlmostEqual(float(preds[0]), float(boost), places=5)


@unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
class TestOoRankReportLightgbmBackend(unittest.TestCase):
    def test_fit_oo_rank_report_backend_lightgbm(self):
        """fit_oo_rank_report(backend='lambdarank') 全流程 smoke。"""
        from core.research.oo_rank_lambdarank import fit_oo_rank_report

        days = _synth_days(40, 24, seed=3)
        report = fit_oo_rank_report(
            [],  # stock_bars 空，用 day_panels
            day_panels=days,
            horizon_days=1,
            min_names=8,
            holdout_trading_days=8,
            backend="lambdarank",
            lightgbm_params={
                "n_estimators": 20,
                "max_depth": 3,
                "learning_rate": 0.08,
                "subsample": 1.0,
                "min_child_samples": 2,
            },
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("backend"), "lambdarank")
        self.assertIn(report.get("recommended_backend"), {"lambdarank", "ridge"})
        self.assertIn("lambdarank", (report.get("backends") or {}))
        self.assertEqual(report.get("solver"), "lambdarank")


if __name__ == "__main__":
    unittest.main()
