"""P0/P1：horizon 对齐、OOS 失败组排除、事件先验 soft hold。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestScoringHorizon(unittest.TestCase):
    def test_default_horizon_is_one(self):
        from core.signal.config import DEFAULT_SIGNAL_CONFIG, get_scoring_horizon_days

        self.assertEqual(int(DEFAULT_SIGNAL_CONFIG["scoring"]["horizon_days"]), 1)
        with patch(
            "core.signal.config.load_signal_config",
            return_value={"scoring": {"horizon_days": 1}},
        ):
            self.assertEqual(get_scoring_horizon_days(), 1)

    def test_promote_blocks_horizon_mismatch(self):
        from core.signal.cluster_live import _validate_artifact_for_promote

        art = {
            "horizon_days": 3,
            "code_map": {
                "600519": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                        "horizon_days": 3,
                    },
                },
                "000001": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                        "horizon_days": 3,
                    },
                },
            },
        }
        with patch(
            "core.signal.config.get_scoring_horizon_days", return_value=1
        ), patch(
            "core.validation_universe.universe_sample_gate",
            return_value={"ok": True},
        ):
            err = _validate_artifact_for_promote(art)
        self.assertIsNotNone(err)
        self.assertIn("horizon_days", str(err))

    def test_promote_allows_matching_horizon(self):
        from core.signal.cluster_live import _validate_artifact_for_promote

        art = {
            "horizon_days": 1,
            "code_map": {
                "600519": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                        "horizon_days": 1,
                    },
                },
                "000001": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                        "horizon_days": 1,
                    },
                },
            },
        }
        with patch(
            "core.signal.config.get_scoring_horizon_days", return_value=1
        ), patch(
            "core.validation_universe.universe_sample_gate",
            return_value={"ok": True},
        ):
            err = _validate_artifact_for_promote(art)
        self.assertIsNone(err)

    def test_legacy_artifact_without_horizon_still_ok(self):
        from core.signal.cluster_live import _validate_artifact_for_promote

        art = {
            "code_map": {
                "600519": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                    },
                },
                "000001": {
                    "cluster_label": "G1",
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "intercept": 0.0,
                        "z_means": {"momentum": 50.0},
                        "z_stds": {"momentum": 10.0},
                    },
                },
            },
        }
        with patch(
            "core.signal.config.get_scoring_horizon_days", return_value=1
        ), patch(
            "core.validation_universe.universe_sample_gate",
            return_value={"ok": True},
        ):
            err = _validate_artifact_for_promote(art)
        self.assertIsNone(err)


class TestOosFailedExclude(unittest.TestCase):
    def test_oos_failed_cluster_labels(self):
        from core.signal.cluster_live import oos_failed_cluster_labels

        clusters = [
            {"label": "G6", "oos_gate": {"ok": True, "passed": False}},
            {"label": "G5", "oos_gate": {"ok": True, "passed": True}},
            {"label": "G7", "oos_gate": {"skipped": True, "passed": False}},
            {"cluster_label": "G8", "oos_passed": False},
        ]
        failed = oos_failed_cluster_labels(clusters)
        self.assertIn("G6", failed)
        self.assertIn("G8", failed)
        self.assertNotIn("G5", failed)
        self.assertNotIn("G7", failed)

    def test_filter_primary_cluster_models_always_skips_oos_failed(self):
        from core.signal.cluster_live import filter_primary_cluster_models_by_code
        from core.signal.return_score import ReturnScoreModel

        m = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 0.1},
            z_means={"momentum": 0.0},
            z_stds={"momentum": 1.0},
            standardized=True,
        )
        active = {
            "clusters": [
                {"label": "G_ok", "oos_gate": {"passed": True, "ok": True}},
                {"label": "G_bad", "oos_gate": {"passed": False, "ok": True}},
            ],
            "code_map": {
                "600000": {"cluster_label": "G_ok"},
                "600001": {"cluster_label": "G_bad"},
            },
        }
        models = {"600000": m, "600001": m}
        out = filter_primary_cluster_models_by_code(models, active=active)
        self.assertIn("600000", out)
        self.assertNotIn("600001", out)
        # 遗留开关失效：即使配置写 false 仍剔失败组
        still = filter_primary_cluster_models_by_code(
            models,
            config={"cluster_scoring": {"exclude_oos_failed_groups": False}},
            active=active,
        )
        self.assertEqual(set(still), {"600000"})

    def test_codes_in_oos_failed_clusters(self):
        from core.signal.cluster_oos_labels import codes_in_oos_failed_clusters

        active = {
            "clusters": [
                {"label": "G_ok", "oos_gate": {"passed": True, "ok": True}},
                {"label": "G_bad", "oos_gate": {"passed": False, "ok": True}},
            ],
            "code_map": {
                "600000": {"cluster_label": "G_ok"},
                "600001": {"cluster_label": "G_bad"},
                "600002": {"label": "G_bad"},
            },
        }
        codes = codes_in_oos_failed_clusters(active=active)
        self.assertEqual(codes, {"600001", "600002"})

    def test_score_and_rank_excludes_oos_failed_from_top(self):
        from unittest.mock import patch

        from core.signal.cross_section_batch import score_and_rank_watching

        entries = [
            {
                "stock_code": "600000",
                "score": 1.0,
                "predicted_score": 1.5,
                "sub_scores": {"momentum": 0.1},
            },
            {
                "stock_code": "600001",
                "score": 80.0,
                "predicted_score": 2.0,
                "sub_scores": {"momentum": 0.2},
            },
        ]
        with patch(
            "core.signal.cluster_oos_labels.codes_in_oos_failed_clusters",
            return_value={"600001"},
        ):
            picks, meta = score_and_rank_watching(
                entries,
                min_score=0.0,
                neutralize=False,
                apply_tau_buy_gate=False,
                exclude_oos_failed=True,
            )
        codes = [c for c, _ in picks]
        self.assertIn("600000", codes)
        self.assertNotIn("600001", codes)
        self.assertGreaterEqual(int(meta.get("oos_failed_excluded") or 0), 1)

    def test_resolve_eod_rejects_heuristic_score_fallback(self):
        from core.signal.dual_score import resolve_predicted_score_eod

        self.assertIsNone(
            resolve_predicted_score_eod({"score": 75.7, "predicted_score": None})
        )
        self.assertAlmostEqual(
            float(resolve_predicted_score_eod({"predicted_score": 0.85}) or 0),
            0.85,
            places=5,
        )

    def test_legacy_exclude_oos_key_stripped_from_cfg(self):
        from core.signal.cluster_live import get_cluster_scoring_cfg

        cfg = get_cluster_scoring_cfg(
            {
                "cluster_scoring": {
                    "enabled": True,
                    "mode": "active",
                    "exclude_oos_failed_groups": False,
                }
            }
        )
        self.assertNotIn("exclude_oos_failed_groups", cfg)


class TestEventPrior(unittest.TestCase):
    def test_gap_and_soft_hold(self):
        from core.event_prior import (
            build_event_prior,
            gap_pct_from_quote_bars,
            should_soft_hold_for_low_score,
        )

        quote = {"success": True, "open": "37.90元", "price_raw": 39.6, "change_raw": 7.03}
        # prev ≈ 39.6 / 1.0703 ≈ 37.0；gap ≈ (37.9/37-1)*100 ≈ 2.43
        gap = gap_pct_from_quote_bars(quote)
        self.assertIsNotNone(gap)
        self.assertGreater(gap, 2.0)

        prior = build_event_prior(
            gap_pct=gap,
            config={
                "event_prior": {
                    "mode": "gate",
                    "gap_trigger_pct": 2.0,
                    "soft_hold_on_theme": True,
                }
            },
            stock_code="000938",
        )
        self.assertTrue(prior.get("theme"))
        self.assertTrue(should_soft_hold_for_low_score(prior))
        self.assertTrue(prior.get("predicted_score_unchanged"))

    def test_gap_strips_today_bar_for_prev_close(self):
        from core.event_prior import gap_pct_from_quote_bars

        quote = {
            "success": True,
            "date": "2026-08-15",
            "open": 10.3,
            "price_raw": 10.5,
            "change_raw": 2.0,
        }
        bars = [
            {"date": "2026-08-14", "open": 10.0, "close": 10.0},
            {"date": "2026-08-15", "open": 10.3, "close": 10.5},
        ]
        gap = gap_pct_from_quote_bars(quote, bars)
        self.assertAlmostEqual(gap, 3.0, places=4)
        quote2 = {"open": 10.3}
        gap2 = gap_pct_from_quote_bars(quote2, bars)
        self.assertAlmostEqual(gap2, 3.0, places=4)

    def test_off_mode_no_soft_hold(self):
        from core.event_prior import build_event_prior, should_soft_hold_for_low_score

        prior = build_event_prior(
            gap_pct=5.0,
            config={"event_prior": {"mode": "off"}},
        )
        self.assertFalse(should_soft_hold_for_low_score(prior))


if __name__ == "__main__":
    unittest.main()
