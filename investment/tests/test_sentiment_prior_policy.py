"""个股舆情：仅观察徽章；调仓不读 gate。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.sentiment_prior import (
    PRIOR_REASON,
    apply_prior_restore_hold,
    apply_prior_to_buy,
    apply_prior_to_hold,
    build_sentiment_prior,
    prior_wants_scale_hold,
)
from core.signal.config import signal_config_overlay
from core.signal.return_score import ReturnScoreModel
from core.signal.score_stock import score_stock
from core.signal.scorer import score_bars


class TestSentimentPriorPolicy(unittest.TestCase):
    def test_off_mode_hints_no_block_action(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "role": "prior",
                    "prior": {"mode": "off"},
                }
            }
        ):
            prior = build_sentiment_prior(
                {"label": "bearish", "score": 0.85},
                stock_code="600000",
            )
        self.assertTrue(prior.get("active"))
        self.assertEqual(prior.get("mode"), "off")
        self.assertTrue(prior.get("risk_hints"))
        types = {a.get("type") for a in prior.get("actions") or []}
        self.assertNotIn("block_new_buy", types)
        self.assertNotIn("scale_buy", types)

    def test_file_gate_is_ignored_badge_only(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {
                        "mode": "gate",
                        "block_new_buys": True,
                        "scale_buy_pct": 0.5,
                    },
                }
            }
        ):
            prior = build_sentiment_prior(
                {"label": "bearish", "score": 0.9},
                stock_code="600000",
            )
        self.assertEqual(prior.get("mode"), "off")
        self.assertTrue(prior.get("active"))
        self.assertTrue(prior.get("risk_hints"))
        applied = apply_prior_to_buy(prior, position_ratio=0.15)
        self.assertFalse(applied.get("skip"))
        self.assertAlmostEqual(float(applied.get("ratio") or 0), 0.15, places=6)

    def test_apply_block_still_works_on_synthetic_pack(self):
        prior = {
            "active": True,
            "actions": [{"type": "block_new_buy", "reason": PRIOR_REASON}],
        }
        applied = apply_prior_to_buy(prior, position_ratio=0.15)
        self.assertTrue(applied.get("skip"))
        self.assertEqual(applied.get("reason"), PRIOR_REASON)
        self.assertTrue(applied.get("predicted_score_unchanged"))

    def test_apply_scale_buy_on_synthetic_pack(self):
        prior = {
            "active": True,
            "actions": [{"type": "scale_buy", "scale": 0.5, "reason": PRIOR_REASON}],
        }
        applied = apply_prior_to_buy(prior, position_ratio=0.20)
        self.assertFalse(applied.get("skip"))
        self.assertAlmostEqual(float(applied.get("ratio") or 0), 0.10, places=6)

    def test_apply_scale_hold_on_synthetic_pack(self):
        prior = {
            "active": True,
            "actions": [
                {"type": "scale_hold", "scale": 0.6, "reason": PRIOR_REASON}
            ],
        }
        types = {a.get("type") for a in prior.get("actions") or []}
        self.assertIn("scale_hold", types)
        applied = apply_prior_to_hold(prior, shares=1000)
        self.assertTrue(applied.get("trim"))
        self.assertAlmostEqual(float(applied.get("keep_shares") or 0), 600.0)
        self.assertAlmostEqual(float(applied.get("sell_shares") or 0), 400.0)
        self.assertTrue(applied.get("predicted_score_unchanged"))

    def test_no_trim_without_scale_hold_action(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {
                        "mode": "gate",
                        "scale_buy_pct": 0.6,
                        "scale_holds": True,
                    },
                }
            }
        ):
            prior = build_sentiment_prior(
                {"label": "bearish", "score": 0.9},
                stock_code="600000",
            )
        applied = apply_prior_to_hold(prior, shares=1000)
        self.assertFalse(applied.get("trim"))

    def test_yhat_unchanged_when_prior_active(self):
        """开/关 prior.mode 不改变 score_bars 产出的特征进 ŷ（闸关）。"""
        bars = []
        px = 10.0
        for i in range(40):
            px *= 1.01
            bars.append(
                {
                    "date": f"2024-02-{(i % 28) + 1:02d}",
                    "open": px,
                    "high": px * 1.01,
                    "low": px * 0.99,
                    "close": px,
                    "volume": 1e6,
                }
            )
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": 10.0,
            "change": 0.0,
        }
        global_m = ReturnScoreModel(
            intercept=1.0,
            coefficients={"momentum": 0.2},
            standardized=False,
        )
        scored = {
            "score": 50.0,
            "hard_reject": False,
            "sub_scores": {"momentum": 60.0},
            "factors": {},
            "reasons": [],
            "factor_contrib": {},
        }

        def _run(mode: str):
            with patch(
                "core.signal.score_stock.fetch_daily_bars",
                return_value=(bars, "akshare"),
            ), patch(
                "core.signal.score_stock.allows_production_score",
                return_value=(True, ""),
            ), patch(
                "core.signal.score_stock.score_bars", return_value=scored
            ), patch(
                "core.sentiment.fetch_stock_headlines",
                return_value={
                    "ok": True,
                    "sentiment": {"label": "bearish", "score": 0.9},
                    "items": [],
                },
            ), patch(
                "core.signal.return_score_store.load_return_model",
                return_value=(global_m, {}),
            ), patch(
                "core.signal.score_stock.load_signal_config",
                return_value={
                    "fundamentals": {"enabled": False},
                    "sentiment": {
                        "include_in_score": False,
                        "role": "prior",
                        "prior": {
                            "mode": mode,
                            "block_new_buys": True,
                        },
                    },
                    "cluster_scoring": {"enabled": False, "mode": "off"},
                },
            ):
                return score_stock(
                    "600000",
                    quote=quote,
                    skip_fundamentals=True,
                    bypass_quality_gate=True,
                    cluster_mode="off",
                )

        out_off = _run("off")
        out_gate = _run("gate")
        y0 = (out_off.get("signal_item") or {}).get("predicted_score")
        y1 = (out_gate.get("signal_item") or {}).get("predicted_score")
        self.assertIsNotNone(y0)
        self.assertEqual(y0, y1)
        prior = (out_gate.get("signal_item") or {}).get("sentiment_prior") or {}
        self.assertEqual(prior.get("mode"), "off")
        self.assertTrue(prior.get("active"))

    def test_sub_scores_no_analysis_and_no_alt_when_gated(self):
        bars = [
            {
                "date": "2024-01-01",
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10 + i * 0.1,
                "volume": 1e6,
            }
            for i in range(30)
        ]
        from core.signal.config import load_signal_config

        cfg = load_signal_config(reload=True)
        out = score_bars(
            bars,
            quote={"change_raw": 0.0},
            sentiment={"label": "bearish", "score": 0.9, "analysis": "叙事"},
            config=cfg,
        )
        subs = out.get("sub_scores") or {}
        self.assertNotIn("alt_sentiment", subs)
        self.assertNotIn("analysis", subs)
        for v in subs.values():
            self.assertIsInstance(v, (int, float))

    def test_restore_when_prior_no_longer_scales(self):
        bearish = {
            "active": True,
            "actions": [{"type": "scale_hold", "scale": 0.6}],
        }
        self.assertTrue(prior_wants_scale_hold(bearish))
        blocked = apply_prior_restore_hold(
            bearish, current_shares=600, base_shares=1000
        )
        self.assertFalse(blocked.get("restore"))

        neutral = {"active": False, "actions": []}
        restored = apply_prior_restore_hold(
            neutral, current_shares=600, base_shares=1000
        )
        self.assertTrue(restored.get("restore"))
        self.assertEqual(float(restored.get("buy_shares") or 0), 400.0)
        self.assertTrue(restored.get("clear_base"))

        cleared = apply_prior_restore_hold(
            neutral, current_shares=1000, base_shares=1000
        )
        self.assertFalse(cleared.get("restore"))
        self.assertTrue(cleared.get("clear_base"))


if __name__ == "__main__":
    unittest.main()
