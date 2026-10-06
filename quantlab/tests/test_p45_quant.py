import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config
from core.signal.factors.meta.registry import (
    compute_configured_factors,
    compute_factor,
    list_factors,
    run_factor_experiment,
)
from core.signal.factors.liquidity import score_liquidity
from core.signal.factors.reversal import score_reversal
from core.signal.scorer import score_bars
from tests.test_signal import _rising_bars


class TestP45NewFactors(unittest.TestCase):
    def test_registry_has_six_factors(self):
        names = {f["name"] for f in list_factors()}
        self.assertIn("reversal", names)
        self.assertIn("liquidity", names)

    def test_factor_descriptions(self):
        rows = list_factors()
        self.assertTrue(all(f.get("description") for f in rows))
        by_name = {f["name"]: f for f in rows}
        self.assertIn("涨跌幅", by_name["momentum"]["description"])
        self.assertIn("舆情", by_name["alt_sentiment"]["description"])

    def test_default_weights_sum_to_one(self):
        weights = DEFAULT_SIGNAL_CONFIG["weights"]
        self.assertIn("reversal", weights)
        self.assertIn("liquidity", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)

    def test_reversal_prefers_mild_pullback(self):
        bars = _rising_bars()
        for i, b in enumerate(bars[-4:]):
            b["close"] = bars[-5]["close"] * (1 - 0.01 * (i + 1))
        score_pullback, _ = score_reversal(bars)
        score_rising, _ = score_reversal(_rising_bars())
        self.assertGreater(score_pullback, score_rising)

    def test_liquidity_scores_active_turnover(self):
        bars = _rising_bars()
        score, meta = score_liquidity(bars)
        self.assertGreaterEqual(score, 55)
        self.assertIsNotNone(meta.get("turnover_ratio"))

    def test_score_bars_includes_new_sub_scores(self):
        result = score_bars(_rising_bars(), quote={"change_raw": 1.0})
        subs = result["sub_scores"]
        self.assertIn("reversal", subs)
        self.assertIn("liquidity", subs)
        self.assertIn("reversal", result["factor_contrib"])

    def test_factor_experiment_includes_new_factors(self):
        bars = _rising_bars()
        while len(bars) < 35:
            last = dict(bars[-1])
            last["date"] = f"extra-{len(bars)}"
            last["close"] = last["close"] + 0.2
            bars.append(last)
        out = run_factor_experiment(bars, min_history=12)
        names = {r["factor"] for r in out["factors"]}
        self.assertIn("reversal", names)
        self.assertIn("liquidity", names)

    def test_compute_configured_factors_respects_weights(self):
        bars = _rising_bars()
        subs, contribs, _ = compute_configured_factors(
            bars,
            weights={"reversal": 1.0},
            last_change=1.0,
        )
        self.assertEqual(list(subs.keys()), ["reversal"])
        self.assertAlmostEqual(contribs["reversal"], subs["reversal"], places=1)

    def test_live_config_has_six_weights(self):
        cfg = load_signal_config(reload=True)
        self.assertIn("reversal", cfg["weights"])
        self.assertIn("liquidity", cfg["weights"])


if __name__ == "__main__":
    unittest.main()
