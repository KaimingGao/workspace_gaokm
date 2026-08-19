"""概念图谱与 prior 策略补强测试。"""

from __future__ import annotations

import unittest

from core.market_prior_policy import apply_market_priors_to_buy, apply_market_priors_to_hold
from core.regulatory_prior import build_ipo_drain_prior, build_regulatory_prior
from core.signal.regime import assess_regime
from skills.announcement.concept_graph import (
    enrich_regulatory_with_concepts,
    stock_in_penalty_concepts,
)


class TestMarketContextEnhancements(unittest.TestCase):
    def test_concept_graph_membership(self):
        reg = enrich_regulatory_with_concepts(
            {
                "active": True,
                "count": 1,
                "penalty_codes": ["600001"],
                "concept_tags": ["机器人"],
                "hits": [{"stock_code": "600001", "title": "停牌核查"}],
            },
            concept_hints=["机器人"],
            max_concepts=0,
        )
        reg["code_concepts"] = {
            "600001": ["机器人"],
            "600002": ["机器人", "半导体"],
        }
        reg["penalty_concept_graph"] = {"600001": ["机器人"]}
        reg["concept_graph_built"] = True
        self.assertTrue(stock_in_penalty_concepts("600002", reg, fallback_tags=["机器人"]))

    def test_regulatory_prior_graph_hit(self):
        ann = {
            "regulatory": {
                "active": True,
                "hits": [{"stock_code": "600001", "title": "停牌核查"}],
                "concept_tags": ["机器人"],
                "penalty_codes": ["600001"],
                "code_concepts": {"600002": ["机器人"]},
                "penalty_concept_graph": {"600001": ["机器人"]},
                "concept_graph_built": True,
            }
        }
        prior = build_regulatory_prior(
            ann,
            config={"regulatory_prior": {"mode": "gate"}},
            stock_code="600002",
        )
        self.assertTrue(prior.get("active"))

    def test_ipo_drain_concept_hit(self):
        ann = {
            "concept_index": {"600519": ["机器人"]},
            "ipo": {
                "extreme_ipo_day": True,
                "liquidity_drain_ratio_proxy": 4.0,
                "drain_ratios_by_concept": {"机器人": 4.5},
            },
        }
        prior = build_ipo_drain_prior(
            ann,
            config={"ipo_drain_prior": {"mode": "gate"}},
            stock_code="600519",
        )
        self.assertTrue(prior.get("active"))

    def test_market_prior_min_scale_merge(self):
        item = {
            "cross_market_prior": {
                "active": True,
                "warnings": ["a"],
                "actions": [{"type": "scale_buy", "scale": 0.5}],
            },
            "market_sentiment_prior": {
                "active": True,
                "warnings": ["b"],
                "actions": [{"type": "scale_buy", "scale": 0.55}],
            },
        }
        out = apply_market_priors_to_buy(
            item,
            position_ratio=1.0,
            config={"market_prior_policy": {"merge_mode": "min_scale"}},
        )
        self.assertAlmostEqual(out["ratio"], 0.5)

    def test_market_prior_hold_min_scale(self):
        item = {
            "cross_market_prior": {
                "active": True,
                "actions": [{"type": "scale_hold", "scale": 0.5}],
            },
            "regulatory_prior": {
                "active": True,
                "actions": [{"type": "scale_hold", "scale": 0.4}],
            },
        }
        out = apply_market_priors_to_hold(
            item,
            shares=1000,
            config={"market_prior_policy": {"merge_mode": "min_scale"}},
        )
        self.assertTrue(out.get("trim"))
        self.assertEqual(out.get("keep_shares"), 400.0)

    def test_regime_defer_macro_to_cross_market(self):
        bars = [{"date": f"2026-08-{i:02d}", "close": 100 + i} for i in range(1, 25)]
        macro = {"overseas_tech_1d_pct": -2.5, "liquidity_stress_score": 0, "series": {}}
        info = assess_regime(
            bars,
            {
                "enabled": True,
                "macro_overlay": {
                    "enabled": True,
                    "defer_tech_drag_to_cross_market_prior": True,
                },
            },
            macro=macro,
        )
        overlay = info.get("macro_overlay") or {}
        self.assertTrue(overlay.get("deferred_to_cross_market_prior"))
        self.assertFalse(overlay.get("applied"))


if __name__ == "__main__":
    unittest.main()
