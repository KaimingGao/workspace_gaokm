import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import (
    _cross_section_ranked_list,
    build_report_executive_summary,
)


class TestP75ExecutiveSummaryScoreStats(unittest.TestCase):
    def test_cross_section_ranking_key(self):
        cs = {
            "success": True,
            "ranking": [
                {"stock_code": "600519", "score": 72.5},
                {"stock_code": "600036", "score": 61.0},
            ],
        }
        self.assertEqual(len(_cross_section_ranked_list(cs)), 2)

    def test_summary_includes_score_stats(self):
        report = {
            "cross_section": {
                "success": True,
                "ranking": [
                    {"stock_code": "600519", "score": 72.5},
                    {"stock_code": "600036", "score": 61.0},
                ],
            }
        }
        out = build_report_executive_summary(report)
        self.assertTrue(any("横截面 score" in b for b in out["bullets"]))
        self.assertTrue(any("72.5" in b or "72" in b for b in out["bullets"]))


if __name__ == "__main__":
    unittest.main()
