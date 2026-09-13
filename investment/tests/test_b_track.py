"""B 轨：回归准确性 — y_spec · 样本指纹 · 共线 · Ridge · demote · OOS regime。"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestB0AnnMissing(unittest.TestCase):
    def test_ann_missing_top_codes(self):
        from core.research.beta_accuracy import ann_missing_top_codes

        cov = {
            "rows": [
                {"code": "A", "ann_missing_points": 3, "history_count": 4},
                {"code": "B", "ann_missing_points": 0},
                {"code": "C", "ann_missing_points": 5, "history_count": 5},
            ]
        }
        top = ann_missing_top_codes(cov, limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["code"], "C")
        self.assertEqual(top[1]["code"], "A")


class TestB1SampleFingerprint(unittest.TestCase):
    def test_fingerprint_promote_gate(self):
        from core.research.beta_accuracy import sample_fingerprint

        ok = sample_fingerprint(n_obs=40, n_names=5, min_obs=24, min_names=3)
        self.assertTrue(ok["promote_ok"])
        bad = sample_fingerprint(n_obs=10, n_names=2, min_obs=24, min_names=3)
        self.assertFalse(bad["promote_ok"])
        # 单票组：min_names 应随组员数降到 1
        solo = sample_fingerprint(n_obs=40, n_names=1, min_obs=24, min_names=1)
        self.assertTrue(solo["promote_ok"])
        pair = sample_fingerprint(n_obs=40, n_names=2, min_obs=24, min_names=2)
        self.assertTrue(pair["promote_ok"])

    def test_group_local_n_obs_blocker_does_not_poison_pool(self):
        from core.research.beta_accuracy import fingerprint_blocker_is_group_local
        from quant.research.factor_ols_clusters import _aggregate_sample_fingerprint

        self.assertTrue(
            fingerprint_blocker_is_group_local("组内：n_obs=20 < min_obs=24")
        )
        self.assertTrue(
            fingerprint_blocker_is_group_local("n_obs=20 < min_obs=24", from_group=True)
        )
        self.assertFalse(
            fingerprint_blocker_is_group_local("n_obs=20 < min_obs=24")
        )
        clusters = [
            {
                "return_model": {
                    "sample_fingerprint": {
                        "n_obs": 80,
                        "n_names": 5,
                        "promote_ok": True,
                        "blockers": [],
                    }
                }
            },
            {
                "return_model": {
                    "sample_fingerprint": {
                        "n_obs": 20,
                        "n_names": 1,
                        "promote_ok": False,
                        "blockers": ["n_obs=20 < min_obs=24"],
                    }
                }
            },
        ]
        fp = _aggregate_sample_fingerprint(clusters)
        self.assertTrue(fp["promote_ok"], fp)
        self.assertFalse(any("n_obs=" in str(b) for b in (fp.get("blockers") or [])))

    def test_universe_sample_gate_fields(self):
        from core.validation_universe import universe_sample_gate

        g = universe_sample_gate(
            universe={"min_codes": 1000, "include_only": ["600000"], "exclude_codes": []}
        )
        self.assertFalse(g["ok"])
        self.assertIn("min_codes", str(g.get("blockers")))


class TestB2YSpec(unittest.TestCase):
    def test_y_spec_fields(self):
        from core.research.beta_accuracy import build_y_spec

        y = build_y_spec(horizon_days=5)
        self.assertEqual(y["horizon_days"], 5)
        self.assertFalse(y["include_cost"])
        self.assertEqual(y["track"], "B2")
        self.assertIn("open", y["formula"])

    def test_fit_gap_mentions_y_spec(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(backtest_params={"horizon_days": 99})
        codes = {h.get("code") for h in out.get("hints") or []}
        self.assertIn("y_spec", codes)
        self.assertIn("horizon_mismatch", codes)


class TestB3CollinearityAndRidge(unittest.TestCase):
    def test_drop_redundant_on_correlated_pair(self):
        from core.research.beta_accuracy import apply_collinearity_policy
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        a, b = TREND_FAMILY[0], TREND_FAMILY[1]
        xs = []
        ys = []
        for i in range(30):
            v = float(i)
            xs.append({a: v, b: v + 0.01 * i, "value": float(i % 3)})
            ys.append(v * 0.1)
        kept, dropped, meta = apply_collinearity_policy(
            xs, [a, b, "value"], policy="drop_redundant", corr_threshold=0.85, ys=ys
        )
        self.assertEqual(len(dropped), 1)
        self.assertIn(dropped[0], (a, b))
        self.assertIn("value", kept)
        self.assertEqual(meta["collinearity_policy"], "drop_redundant")

    def test_keep_all_policy(self):
        from core.research.beta_accuracy import apply_collinearity_policy
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        a, b = TREND_FAMILY[0], TREND_FAMILY[1]
        xs = [{a: float(i), b: float(i)} for i in range(20)]
        kept, dropped, _ = apply_collinearity_policy(
            xs, [a, b], policy="keep_all", corr_threshold=0.5
        )
        self.assertEqual(kept, [a, b])
        self.assertEqual(dropped, [])

    def test_select_ridge_returns_meta(self):
        from core.research.beta_accuracy import select_ridge_lambda

        xs = [{"momentum": float(i), "value": float(i % 5)} for i in range(24)]
        ys = [0.1 * i for i in range(24)]
        meta = select_ridge_lambda(xs, ys, ["momentum", "value"])
        self.assertIn("ridge_lambda_selected", meta)
        self.assertEqual(meta.get("track"), "B3")


class TestB4Demote(unittest.TestCase):
    def test_health_exposes_ic_demote_and_refit(self):
        from core.signal.cluster.live_health import assess_cluster_live_health

        with patch(
            "core.signal.cluster.live.get_cluster_scoring_cfg",
            return_value={
                "mode": "shadow",
                "enabled": True,
                "min_coverage": 0.5,
                "max_age_days": 14,
                "auto_demote_on_stale": True,
                "min_yhat_rolling_ic": 0.5,
                "block_active_on_yhat_ic": True,
            },
        ), patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value=None,
        ):
            h = assess_cluster_live_health(universe=["600000", "000001"])
        self.assertIn("ic_demote", h)
        self.assertIn("refit_suggested", h)
        self.assertEqual(h.get("track"), "B4+FM2")

    def test_cfg_refit_max_age_alias(self):
        from core.signal.cluster.live import get_cluster_scoring_cfg

        cfg = get_cluster_scoring_cfg(
            {"cluster_scoring": {"refit_max_age_days": 7, "mode": "off"}}
        )
        self.assertEqual(cfg["max_age_days"], 7)
        self.assertEqual(cfg["refit_max_age_days"], 7)


class TestB5RespectRegime(unittest.TestCase):
    def test_evaluate_research_oos_default_respect_regime(self):
        from core.signal.weight_oos_gate import evaluate_research_oos

        sig = inspect.signature(evaluate_research_oos)
        self.assertTrue(sig.parameters["respect_regime"].default)

    def test_cluster_report_defaults(self):
        from quant.research.factor_ols_clusters import (
            cluster_speed_policy,
            compute_factor_ols_cluster_report,
        )

        sig = inspect.signature(compute_factor_ols_cluster_report)
        self.assertTrue(sig.parameters["respect_regime"].default)
        self.assertTrue(sig.parameters["select_ridge"].default)
        self.assertEqual(sig.parameters["collinearity_policy"].default, "drop_redundant")
        small = cluster_speed_policy(12)
        self.assertFalse(small["large_universe"])
        self.assertTrue(small["daily_pit"])
        self.assertTrue(small["select_ridge"])
        big = cluster_speed_policy(100)
        self.assertTrue(big["large_universe"])
        self.assertFalse(big["daily_pit"])
        self.assertFalse(big["select_ridge"])
        self.assertIn("快照", str(big.get("note") or ""))
        self.assertIn("选区跳过组权 OOS", str(big.get("note") or ""))

    def test_schema_cluster_request_b_fields(self):
        from web.schemas import FactorOlsClusterRequest

        body = FactorOlsClusterRequest()
        self.assertTrue(body.respect_regime)
        self.assertTrue(body.select_ridge)
        self.assertEqual(body.collinearity_policy, "drop_redundant")
        self.assertEqual(body.watching_limit, 200)


if __name__ == "__main__":
    unittest.main()
