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

    def test_cfg_exclude_oos_default_true(self):
        from core.signal.cluster_live import get_cluster_scoring_cfg

        cfg = get_cluster_scoring_cfg({"cluster_scoring": {"enabled": True, "mode": "active"}})
        self.assertTrue(cfg.get("exclude_oos_failed_groups"))


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

    def test_off_mode_no_soft_hold(self):
        from core.event_prior import build_event_prior, should_soft_hold_for_low_score

        prior = build_event_prior(
            gap_pct=5.0,
            config={"event_prior": {"mode": "off"}},
        )
        self.assertFalse(should_soft_hold_for_low_score(prior))


if __name__ == "__main__":
    unittest.main()
