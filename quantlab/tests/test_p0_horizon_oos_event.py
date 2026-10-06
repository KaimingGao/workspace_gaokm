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

    def test_insights_and_holdings_use_scoring_horizon(self):
        """数据中心 / 持仓算分 horizon 跟 signal_config，不再写死 3。"""
        import inspect

        from core.watching import insights as insights_mod

        src = inspect.getsource(insights_mod._insight_one)
        self.assertIn("_scoring_horizon_days()", src)
        self.assertNotIn("horizon_days=3", src)
        with patch(
            "core.signal.config.get_scoring_horizon_days", return_value=1
        ):
            self.assertEqual(insights_mod._scoring_horizon_days(), 1)

    def test_cluster_promote_validators_retired(self):
        """_validate_artifact_for_promote 随 core.signal.cluster 删除。"""
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("core.signal.cluster"))


class TestOosFailedExclude(unittest.TestCase):
    def test_cluster_oos_exclude_helpers_retired(self):
        """oos_failed_cluster_labels / filter_primary / codes_in_oos_failed 已删。"""
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("core.signal.cluster"))

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


class TestEventPrior(unittest.TestCase):
    def test_gap_and_soft_hold(self):
        from core.event_prior import (
            build_event_prior,
            gap_pct_from_quote_bars,
            should_soft_hold_for_low_score,
        )

        quote = {
            "success": True,
            "open": "37.90元",
            "prev_close": 37.0,
            "price_raw": 39.6,
            "change_raw": 7.03,
        }
        # gap = 37.90/37−1 ≈ 2.43；昨收须显式给出，禁止用现价涨跌反推
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
