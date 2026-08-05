"""FH0：cluster_scoring.mode 硬门禁 predicted_score / primary ŷ。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.cluster_live import (
    cluster_yhat_primary_allowed,
    cluster_yhat_shadow_compute_allowed,
    get_cluster_scoring_cfg,
    normalize_cluster_scoring_mode,
)
from core.signal.return_score import ReturnScoreModel
from core.signal.score_stock import score_stock


def _model(intercept: float, beta: float) -> ReturnScoreModel:
    return ReturnScoreModel(
        intercept=intercept,
        coefficients={"momentum": beta},
        z_means={"momentum": 0.0},
        z_stds={"momentum": 1.0},
        standardized=True,
    )


class TestClusterModeHelpers(unittest.TestCase):
    def test_normalize_and_gates(self):
        self.assertEqual(normalize_cluster_scoring_mode("ACTIVE"), "active")
        self.assertEqual(
            normalize_cluster_scoring_mode("active", enabled=False), "off"
        )
        self.assertFalse(cluster_yhat_primary_allowed("off"))
        self.assertFalse(cluster_yhat_primary_allowed("shadow"))
        self.assertTrue(cluster_yhat_primary_allowed("active"))
        self.assertFalse(cluster_yhat_shadow_compute_allowed("off"))
        self.assertTrue(cluster_yhat_shadow_compute_allowed("shadow"))
        self.assertTrue(cluster_yhat_shadow_compute_allowed("active"))

    def test_cfg_default_max_oos_fail_rate(self):
        cfg = get_cluster_scoring_cfg({"cluster_scoring": {"enabled": False}})
        self.assertEqual(cfg["mode"], "off")
        self.assertAlmostEqual(float(cfg["max_oos_fail_rate"]), 0.5)


class TestScoreStockModeYhatGate(unittest.TestCase):
    def _run(self, mode: str):
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": 10.0,
            "change": 0.1,
        }
        bars = [{"date": "2024-01-01", "close": 10.0}] * 30
        global_m = _model(1.0, 0.0)  # ŷ=1
        group_m = _model(9.0, 0.0)  # ŷ=9
        mapped = {
            "cluster_label": "G1",
            "cluster_id": 0,
            "version": 1,
            "weight_source": "cluster:G1",
            "return_model": group_m.to_dict(),
        }
        scored = {
            "score": 50.0,
            "hard_reject": False,
            "reject_reason": None,
            "factors": {},
            "reasons": [],
            "invalidation": None,
            "sub_scores": {"momentum": 0.0},
            "factor_contrib": {},
            "regime": None,
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars", return_value=(bars, "akshare")
        ), patch(
            "core.signal.score_stock.allows_production_score", return_value=(True, "")
        ), patch(
            "core.signal.score_stock.fetch_score_fundamentals", return_value=None
        ), patch(
            "core.signal.score_stock.score_bars", return_value=scored
        ), patch(
            "core.sentiment.fetch_stock_headlines",
            return_value={"ok": False},
        ), patch(
            "core.signal.cluster_live.lookup_code_weights", return_value=mapped
        ), patch(
            "core.signal.cluster_live.lookup_code_return_model", return_value=group_m
        ), patch(
            "core.signal.return_score_store.load_return_model",
            return_value=(global_m, {}),
        ), patch(
            "core.signal.score_stock.load_signal_config",
            return_value={
                "fundamentals": {"enabled": False},
                "cluster_scoring": {"enabled": True, "mode": mode},
            },
        ):
            return score_stock(
                "600000",
                quote=quote,
                skip_fundamentals=True,
                bypass_quality_gate=True,
                cluster_mode=mode,
            )

    def test_off_no_cluster_yhat(self):
        out = self._run("off")
        item = out["signal_item"]
        self.assertEqual(item["cluster_mode"], "off")
        self.assertIsNone(item.get("score_cluster"))
        self.assertEqual(item["return_model_source"], "global")
        self.assertAlmostEqual(float(item["predicted_score"]), 1.0)
        self.assertAlmostEqual(float(item["score"]), 1.0)

    def test_shadow_cluster_contrast_only(self):
        out = self._run("shadow")
        item = out["signal_item"]
        self.assertEqual(item["cluster_mode"], "shadow")
        self.assertAlmostEqual(float(item["score_cluster"]), 9.0)
        self.assertAlmostEqual(float(item["score_global"]), 1.0)
        self.assertEqual(item["return_model_source"], "global")
        self.assertAlmostEqual(float(item["predicted_score"]), 1.0)
        self.assertAlmostEqual(float(item["score"]), 1.0)
        self.assertEqual(item["weight_source"], "global+shadow")

    def test_active_primary_uses_cluster(self):
        out = self._run("active")
        item = out["signal_item"]
        self.assertEqual(item["cluster_mode"], "active")
        self.assertAlmostEqual(float(item["score_cluster"]), 9.0)
        self.assertEqual(item["return_model_source"], "cluster_group_beta")
        self.assertAlmostEqual(float(item["predicted_score"]), 9.0)
        self.assertAlmostEqual(float(item["score"]), 9.0)


if __name__ == "__main__":
    unittest.main()
