"""基本面 IC 实验（原 P50）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import load_signal_config
from core.signal.factor_registry import run_factor_experiment
from core.signal.factors.value import score_value
from quant.research.factor_report import compute_factor_ic_report
from tests.test_signal import _rising_bars

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
def _long_bars(n: int = 35):
    bars = _rising_bars()
    while len(bars) < n:
        last = dict(bars[-1])
        last["date"] = f"extra-{len(bars)}"
        last["close"] = last["close"] + 0.3
        last["volume"] = last.get("volume", 1000) + 20
        bars.append(last)
    return bars

# --- test_p50_quant.py::TestP50FundamentalsIcExperiment ---
class TestP50FundamentalsIcExperiment(unittest.TestCase):
    def test_config_enables_ic_fundamentals(self):
        cfg = load_signal_config(reload=True)
        self.assertTrue(cfg.get("fundamentals", {}).get("use_in_ic_experiment"))

    def test_run_factor_experiment_with_fundamentals(self):
        bars = _long_bars()
        fundamentals = {"pe": 16.0, "pb": 1.4, "roe": 18.0, "profit_growth": 12.0}
        # 快照 fundamentals 非 PIT：显式关闭 pit，否则 fundamentals_used 只看 PIT hits
        out = run_factor_experiment(
            bars, min_history=10, fundamentals=fundamentals, pit_fundamentals=False
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["fundamentals_used"])
        names = {r["factor"] for r in out["factors"]}
        self.assertIn("value", names)
        self.assertIn("quality", names)
        value_row = next(r for r in out["factors"] if r["factor"] == "value")
        self.assertGreater(value_row["sample_count"], 5)

    def test_value_factor_non_neutral_with_fundamentals(self):
        score, _ = score_value(fundamentals={"pe": 18.0, "pb": 1.2})
        self.assertGreater(score, 50.0)

    def test_factor_ic_report_covers_registered_factors(self):
        from core.signal.factor_registry import registered_factor_names

        bars = _long_bars()
        fundamentals = {"pe": 20.0, "pb": 2.0, "roe": 15.0}
        report = compute_factor_ic_report(bars, min_history=10, fundamentals=fundamentals)
        self.assertTrue(report["success"])
        n_reg = len(registered_factor_names())
        self.assertEqual(report["factor_count"], n_reg)
        self.assertTrue(report["fundamentals_used"])
        factor_names = {r["factor"] for r in report["factors"] if r["factor"] != "score"}
        self.assertEqual(len(factor_names), n_reg)
        self.assertIn("exclusion_reasons", report)
        self.assertIsInstance(report["exclusion_reasons"], dict)

    def test_pearson_with_reason_codes(self):
        from core.signal.factor_corr import pearson_with_reason

        corr, reason = pearson_with_reason([1, 2, 3], [2, 4, 6])
        self.assertIsNotNone(corr)
        self.assertIsNone(reason)
        _, reason = pearson_with_reason([1, 1], [1, 2])
        self.assertEqual(reason, "sparse")
        _, reason = pearson_with_reason([1, 1, 1], [1, 2, 3])
        self.assertEqual(reason, "constant")
        _, reason = pearson_with_reason([1, 2, 3], [5, 5, 5])
        self.assertEqual(reason, "flat")


if __name__ == "__main__":
    unittest.main()
