import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config
from core.signal.factors.meta.registry import compute_configured_factors, list_factors
from core.signal.factors.quality import score_quality
from core.signal.factors.value import score_value
from core.signal.fundamentals_bridge import normalize_fundamentals_metrics
from core.signal.scorer import score_bars
from tests.test_signal import _rising_bars


class TestP46FundamentalFactors(unittest.TestCase):
    def test_registry_has_fundamental_factors(self):
        names = {f["name"] for f in list_factors()}
        self.assertGreaterEqual(len(names), 8)
        self.assertIn("value", names)
        self.assertIn("quality", names)
        self.assertIn("growth", names)
        self.assertIn("size", names)

    def test_default_weights_include_fundamentals(self):
        weights = DEFAULT_SIGNAL_CONFIG["weights"]
        self.assertIn("value", weights)
        self.assertIn("quality", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)

    def test_value_scores_reasonable_pe(self):
        score, meta = score_value(fundamentals={"pe": 18.0, "pb": 1.5})
        self.assertGreater(score, 60)
        self.assertEqual(meta["value_pe"], 18.0)

    def test_value_neutral_without_data(self):
        score, meta = score_value(fundamentals=None)
        self.assertEqual(score, 50.0)
        self.assertIsNone(meta["value_pe"])
        self.assertTrue(meta.get("omit_sub_score"))

    def test_fund_missing_omitted_from_sub_scores(self):
        bars = _rising_bars()
        subs, _c, meta = compute_configured_factors(
            bars,
            weights={"value": 0.05, "quality": 0.04, "growth": 0.03, "momentum": 0.2},
            fundamentals={},
            required_keys=["value", "quality", "growth", "momentum"],
        )
        self.assertNotIn("value", subs)
        self.assertNotIn("quality", subs)
        self.assertNotIn("growth", subs)
        self.assertIn("momentum", subs)
        self.assertTrue(meta.get("omit_sub_score"))

    def test_quality_scores_high_roe(self):
        score, meta = score_quality(fundamentals={"roe": 22.0, "profit_growth": 15.0})
        self.assertGreaterEqual(score, 64)
        self.assertEqual(meta["quality_roe"], 22.0)
        self.assertNotIn("quality_profit_growth", meta)

    def test_score_bars_includes_fundamental_sub_scores(self):
        fundamentals = {"pe": 16.0, "pb": 1.2, "roe": 18.0, "profit_growth": 12.0}
        result = score_bars(_rising_bars(), quote={"change_raw": 1.0}, fundamentals=fundamentals)
        subs = result["sub_scores"]
        self.assertIn("value", subs)
        self.assertIn("quality", subs)
        self.assertGreater(subs["value"], 50)
        self.assertGreater(subs["quality"], 50)

    def test_normalize_fundamentals_metrics(self):
        raw = {"metrics": {"pe_ttm": 12.5, "roe": 15.0, "total_mv": 1.2e11}}
        out = normalize_fundamentals_metrics(raw)
        self.assertEqual(out["pe_ttm"], 12.5)
        self.assertEqual(out["roe"], 15.0)
        self.assertEqual(out["market_cap"], 1.2e11)

    @patch("core.signal.live_features.resolve_live_fundamentals")
    @patch("core.signal.score_stock.fetch_daily_bars")
    @patch("core.signal.score_stock.query_quote")
    def test_score_stock_passes_fundamentals(self, mock_quote, mock_bars, mock_fund):
        from core.signal.score_stock import score_stock

        mock_quote.return_value = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "price": "1800",
            "change": "+1%",
            "change_raw": 1.0,
            "price_raw": 1800.0,
        }
        mock_bars.return_value = (_rising_bars(), "mock")
        mock_fund.return_value = {
            "metrics": {"pe": 20.0, "pb": 2.0, "roe": 25.0, "profit_growth": 10.0},
            "fundamentals_pit": {"ok": True, "mode": "as_of"},
        }

        with patch(
            "core.valuation_em.merge_cached_valuation", side_effect=lambda c, m, **k: m
        ), patch(
            "core.fundamentals_pit.merge_local_fundamentals_snapshot",
            side_effect=lambda c, m=None: m,
        ), patch(
            "core.signal.live_features.fetch_live_index_bars",
            return_value={"ok": False, "bars": [], "reason": "test"},
        ):
            out = score_stock("600519", bypass_quality_gate=True, skip_sentiment=True)
        self.assertTrue(out["success"])
        subs = out["signal_item"]["sub_scores"]
        self.assertIn("value", subs)
        self.assertIn("quality", subs)
        self.assertIn("sector", out["signal_item"])

    def test_live_config_has_fundamental_weights(self):
        cfg = load_signal_config(reload=True)
        self.assertGreaterEqual(len(cfg["weights"]), 8)
        self.assertIn("value", cfg["weights"])
        self.assertIn("growth", cfg["weights"])
        self.assertTrue(cfg.get("fundamentals", {}).get("enabled"))


if __name__ == "__main__":
    unittest.main()
