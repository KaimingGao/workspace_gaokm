import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.mock_context import rising_bars
from quant.research.factor_ols import compute_factor_ols_report
from quant.services.quant_interpret import build_rule_based_interpret, compact_quant_report


class TestP90FactorOlsInterpret(unittest.TestCase):
    def _ols_report(self):
        bars = rising_bars(45)
        return compute_factor_ols_report(bars, horizon_days=3, min_history=12)

    def test_compact_includes_factor_ols(self):
        ols = self._ols_report()
        self.assertTrue(ols.get("success"))
        compact = compact_quant_report({"success": True, "factor_ols": ols})
        self.assertIn("factor_ols", compact)
        self.assertIn("summary_line", compact["factor_ols"])

    def test_rule_interpret_mentions_ols(self):
        ols = self._ols_report()
        out = build_rule_based_interpret({"success": True, "factor_ols": ols})
        self.assertTrue(out.get("success"))
        text = out.get("interpretation") or ""
        self.assertIn("因子 OLS", text)
        self.assertIn("不自动写 signal_config", text)


if __name__ == "__main__":
    unittest.main()
