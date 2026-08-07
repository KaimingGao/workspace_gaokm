"""舆情先验（ŷ 外）契约：不改 predicted_score；gate 可拦/缩买。"""

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
    apply_prior_to_buy,
    apply_prior_to_hold,
    build_sentiment_prior,
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

    def test_gate_block_new_buy(self):
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
        applied = apply_prior_to_buy(prior, position_ratio=0.15)
        self.assertTrue(applied.get("skip"))
        self.assertEqual(applied.get("reason"), PRIOR_REASON)
        self.assertTrue(applied.get("predicted_score_unchanged"))

    def test_gate_scale_buy(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {
                        "mode": "gate",
                        "block_new_buys": False,
                        "scale_buy_pct": 0.5,
                    },
                }
            }
        ):
            prior = build_sentiment_prior(
                {"label": "bearish", "score": 0.8},
                stock_code="600000",
            )
        applied = apply_prior_to_buy(prior, position_ratio=0.20)
        self.assertFalse(applied.get("skip"))
        self.assertAlmostEqual(float(applied.get("ratio") or 0), 0.10, places=6)

    def test_gate_scale_hold(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {
                        "mode": "gate",
                        "block_new_buys": False,
                        "scale_buy_pct": 0.6,
                        "scale_holds": True,
                    },
                }
            }
        ):
            prior = build_sentiment_prior(
                {"label": "bearish", "score": 0.8},
                stock_code="600000",
            )
        types = {a.get("type") for a in prior.get("actions") or []}
        self.assertIn("scale_hold", types)
        applied = apply_prior_to_hold(prior, shares=1000)
        self.assertTrue(applied.get("trim"))
        self.assertAlmostEqual(float(applied.get("keep_shares") or 0), 600.0)
        self.assertAlmostEqual(float(applied.get("sell_shares") or 0), 400.0)
        self.assertTrue(applied.get("predicted_score_unchanged"))

    def test_gate_scale_hold_off_no_trim(self):
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {
                        "mode": "gate",
                        "scale_buy_pct": 0.6,
                        "scale_holds": False,
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
        self.assertEqual(prior.get("mode"), "gate")
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


if __name__ == "__main__":
    unittest.main()
