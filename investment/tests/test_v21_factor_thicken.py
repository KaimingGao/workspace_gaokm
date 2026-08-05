"""V2.1 因子库加厚 + 截面去冗。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config
from core.signal.factor_corr import compute_factor_corr_matrix, redundancy_warnings_from_corr
from core.signal.factor_registry import (
    compute_configured_factors,
    list_factors,
    registered_factor_names,
)
from core.signal.factors.amihud import score_amihud
from core.signal.factors.alt_sentiment import score_alt_sentiment
from core.signal.factors.dividend import score_dividend
from core.signal.factors.earnings_yield import score_earnings_yield
from core.signal.factors.growth import score_growth
from core.signal.factors.idio_momentum import score_idio_momentum
from core.signal.factors.money_flow import score_money_flow
from core.signal.factors.quality import score_quality
from core.signal.factors.size import score_size
from core.signal.fundamentals_bridge import normalize_fundamentals_metrics
from core.signal.neutralize import apply_cross_section_neutralization
from core.signal.scorer import score_bars
from core.signal.weight_suggest import suggest_weights_from_ic
from tests.test_signal import _rising_bars


NEW_FACTORS = (
    "size",
    "earnings_yield",
    "growth",
    "dividend",
    "money_flow",
    "amihud",
    "idio_momentum",
)


class TestV21FactorThicken(unittest.TestCase):
    def test_registry_includes_new_factors(self):
        names = set(registered_factor_names())
        for fac in NEW_FACTORS:
            self.assertIn(fac, names)
        self.assertIn("alt_sentiment", names)
        self.assertGreaterEqual(len(names), 20)

    def test_weights_sum_to_one(self):
        w = DEFAULT_SIGNAL_CONFIG["weights"]
        self.assertAlmostEqual(sum(w.values()), 1.0, places=3)
        cfg = load_signal_config(reload=True)
        self.assertAlmostEqual(sum(cfg["weights"].values()), 1.0, places=3)
        for fac in NEW_FACTORS:
            self.assertIn(fac, cfg["weights"])
        self.assertGreater(cfg["weights"]["alt_sentiment"], 0)
        self.assertTrue((cfg.get("cross_section") or {}).get("industry_residual"))
        self.assertTrue((cfg.get("cross_section") or {}).get("size_residual"))

    def test_bridge_passes_market_cap_and_dividend(self):
        out = normalize_fundamentals_metrics(
            {"metrics": {"market_cap": 1e11, "dividend_yield": 2.5, "pe": 15}}
        )
        self.assertEqual(out["market_cap"], 1e11)
        self.assertEqual(out["dividend_yield"], 2.5)

    def test_size_and_fundamentals_neutral_without_data(self):
        self.assertEqual(score_size()[0], 50.0)
        self.assertEqual(score_earnings_yield()[0], 50.0)
        self.assertEqual(score_growth()[0], 50.0)
        self.assertEqual(score_dividend()[0], 50.0)

    def test_size_mid_cap_preferred(self):
        # log(exp(25)) ≈ 25 → 中盘桶
        import math

        cap = math.exp(25.0)
        score, meta = score_size(fundamentals={"market_cap": cap})
        self.assertGreaterEqual(score, 65)
        self.assertIsNotNone(meta["size_log_cap"])

    def test_earnings_yield_reasonable(self):
        score, meta = score_earnings_yield(fundamentals={"pe_ttm": 16.0})
        self.assertGreater(score, 55)
        self.assertAlmostEqual(meta["earnings_yield_pct"], 100.0 / 16.0, places=2)

    def test_growth_split_from_quality(self):
        g_score, g_meta = score_growth(fundamentals={"profit_growth": 25.0})
        self.assertGreater(g_score, 65)
        self.assertEqual(g_meta["growth_used"], 25.0)

        q_score, q_meta = score_quality(
            fundamentals={"roe": 22.0, "profit_growth": 25.0}
        )
        self.assertGreaterEqual(q_score, 64)
        self.assertEqual(q_meta["quality_roe"], 22.0)
        self.assertNotIn("quality_profit_growth", q_meta)

    def test_money_flow_proxy_and_true_inflow(self):
        bars = _rising_bars()
        score, meta = score_money_flow(bars)
        self.assertIn(meta["money_flow_source"], ("mfi_proxy", "none"))
        self.assertGreaterEqual(score, 10)

        score2, meta2 = score_money_flow(bars, money_flow={"net_inflow": 10000})
        self.assertEqual(meta2["money_flow_source"], "net_inflow")
        self.assertGreater(score2, 50)

    def test_amihud_and_idio_without_index(self):
        bars = _rising_bars()
        a_score, a_meta = score_amihud(bars)
        self.assertIsNotNone(a_meta.get("amihud") or a_meta.get("ok") is False)
        self.assertGreaterEqual(a_score, 10)

        i_score, i_meta = score_idio_momentum(bars, index_bars=None)
        self.assertEqual(i_score, 50.0)
        self.assertFalse(i_meta.get("ok"))

    def test_alt_sentiment_no_hardcoded_adj_in_score_bars(self):
        bars = _rising_bars()
        from core.signal.config import signal_config_overlay

        with signal_config_overlay({"sentiment": {"include_in_score": True}}):
            bull = score_bars(
                bars,
                quote={"change_raw": 1.0},
                sentiment={"label": "bullish"},
                config=load_signal_config(reload=True),
            )
        self.assertNotIn("sentiment_adj", bull.get("factor_contrib") or {})
        self.assertIn("alt_sentiment", bull.get("sub_scores") or {})
        self.assertGreater(bull["sub_scores"]["alt_sentiment"], 50)

        s, _ = score_alt_sentiment([], sentiment={"label": "bearish", "score": 0.8})
        self.assertLess(s, 45)

    def test_compute_configured_includes_new(self):
        bars = _rising_bars()
        fund = {
            "pe_ttm": 18.0,
            "roe": 15.0,
            "profit_growth": 10.0,
            "market_cap": 5e10,
            "dividend_yield": 2.0,
        }
        subs, contribs, meta = compute_configured_factors(
            bars,
            weights={k: 0.1 for k in NEW_FACTORS},
            fundamentals=fund,
            index_bars=bars,
            sentiment={"label": "neutral"},
        )
        for fac in NEW_FACTORS:
            self.assertIn(fac, subs)

    def test_size_residual_neutralization(self):
        weights = {"momentum": 1.0}
        items = []
        for i, (sec, cap, mom) in enumerate(
            [
                ("A", 1e9, 40.0),
                ("A", 2e9, 60.0),
                ("B", 1e11, 40.0),
                ("B", 2e11, 60.0),
                ("C", 5e10, 50.0),
                ("C", 6e10, 55.0),
            ]
        ):
            items.append(
                {
                    "stock_code": f"c{i}",
                    "score": mom,
                    "sector": sec,
                    "market_cap": cap,
                    "sub_scores": {"momentum": mom},
                }
            )
        out = apply_cross_section_neutralization(
            items,
            weights=weights,
            industry_residual=True,
            size_residual=True,
            min_samples=3,
        )
        self.assertTrue(out["applied"])
        self.assertTrue(out.get("size_residual") or out.get("size_residual_meta"))

    def test_factor_corr_and_redundancy_warnings(self):
        items = [
            {"sub_scores": {"momentum": 40 + i, "ma_slope": 41 + i, "value": 50}}
            for i in range(8)
        ]
        report = compute_factor_corr_matrix(items)
        self.assertTrue(report["success"])
        self.assertGreaterEqual(report["sample_count"], 8)
        warns = redundancy_warnings_from_corr(
            report,
            threshold=0.7,
            factor_groups={"trend": ["momentum", "ma_slope"]},
            weights={"momentum": 0.3, "ma_slope": 0.25},
        )
        types = {w["type"] for w in warns}
        self.assertIn("high_corr", types)

        exp = {
            "factors": [
                {"factor": "momentum", "ic": 0.1, "sample_count": 20},
            ]
        }
        sug = suggest_weights_from_ic(exp, corr_report=report)
        self.assertTrue(sug["success"])
        self.assertIn("redundancy_warnings", sug)

    def test_list_factors_descriptions(self):
        rows = list_factors()
        by = {r["name"]: r for r in rows}
        for fac in NEW_FACTORS:
            self.assertTrue(by[fac].get("description"))


if __name__ == "__main__":
    unittest.main()
