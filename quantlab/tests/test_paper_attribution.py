"""P2 · 纸面简化归因。"""

from __future__ import annotations

import unittest

from core.paper import build_ops_report
from core.paper.attribution import build_paper_attribution_lite


class TestPaperAttributionLite(unittest.TestCase):
    def test_selection_allocation_residual(self):
        paper = {"cash": 20000, "snapshots": [{"equity": 100000}]}
        summary = {
            "equity": 105000,
            "stock_value": 85000,
            "cash": 20000,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "sector": "消费",
                    "market_value": 40000,
                    "pnl_pct": 10.0,
                },
                {
                    "stock_code": "300750",
                    "stock_name": "宁德",
                    "sector": "新能源",
                    "market_value": 30000,
                    "pnl_pct": -5.0,
                },
                {
                    "stock_code": "600036",
                    "stock_name": "招行",
                    "sector": "金融",
                    "market_value": 15000,
                    "pnl_pct": 2.0,
                },
            ],
        }
        out = build_paper_attribution_lite(paper, summary)
        self.assertTrue(out["ok"])
        self.assertIn("selection_pct", out)
        self.assertIn("allocation_pct", out)
        self.assertIn("residual_pct", out)
        self.assertGreater(len(out["by_sector"]), 0)
        self.assertGreater(len(out["top_contributors"]), 0)
        self.assertEqual(out["top_contributors"][0]["stock_code"], "600519")
        # 相对上一快照 100k → 105k
        self.assertAlmostEqual(out["period_return_pct"], 5.0, places=2)

        ops = build_ops_report(
            strategy_id="short_conservative",
            cost_model="simple_cn",
            attribution=out,
        )
        self.assertTrue(ops["attribution"]["ok"])
        self.assertEqual(ops["attribution"]["selection_pct"], out["selection_pct"])

    def test_empty_holdings(self):
        out = build_paper_attribution_lite({}, {"equity": 100000, "holdings": []})
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
