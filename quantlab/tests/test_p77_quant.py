import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_interpret import build_rule_based_interpret, compact_quant_report
from quant.services.quant_report_export import summarize_cross_section_scores


class TestP77InterpretCrossSectionScore(unittest.TestCase):
    def _report_with_cs(self):
        return {
            "cross_section": {
                "success": True,
                "ranking": [
                    {"stock_code": "600519", "stock_name": "贵州茅台", "score": 72.5},
                    {"stock_code": "600036", "stock_name": "招商银行", "score": 61.0},
                ],
                "neutralization": {"applied": True, "method": "zscore"},
            }
        }

    def test_summarize_cross_section_scores(self):
        sm = summarize_cross_section_scores(self._report_with_cs()["cross_section"])
        self.assertIsNotNone(sm)
        self.assertEqual(sm["ranked_count"], 2)
        self.assertIn("72.5", sm["summary_line"])

    def test_compact_includes_cross_section(self):
        compact = compact_quant_report(self._report_with_cs())
        self.assertIn("cross_section", compact)
        self.assertEqual(compact["cross_section"]["ranked_count"], 2)

    def test_rule_based_interpret_mentions_score(self):
        out = build_rule_based_interpret(self._report_with_cs())
        self.assertIn("ŷ", out["interpretation"])
        self.assertIn("不等于买入", out["interpretation"])


if __name__ == "__main__":
    unittest.main()
