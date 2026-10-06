"""运营收尾：sector_map 覆盖 · risk_block 标注 · risk_parity_lite。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestSectorMapCoverage(unittest.TestCase):
    def test_watching_codes_mapped(self):
        from core.portfolio_optimize import load_sector_map
        from core.sector_map_sync import coverage_report

        smap = load_sector_map()
        for code in ("688256", "600150", "600938", "302132", "601328", "600519"):
            self.assertIn(code, smap, msg=f"missing {code}")
        cov = coverage_report(
            ["688256", "600150", "600938", "302132", "601328", "600519", "999999"]
        )
        self.assertEqual(cov["mapped"], 6)
        self.assertIn("999999", cov["missing"])


class TestRiskBlockAnnotate(unittest.TestCase):
    def test_annotate_and_effectiveness(self):
        from core.north_star import summarize_risk_blocks
        from core.risk.block_outcome import annotate_risk_block

        paper = {
            "operation_log": [
                {
                    "type": "risk_block",
                    "ts": "2026-07-29T10:00:00",
                    "detail": "行业超限",
                    "meta": {"codes": ["max_sector"]},
                },
                {
                    "type": "risk_block",
                    "ts": "2026-07-29T11:00:00",
                    "detail": "单票超限",
                    "meta": {"codes": ["max_position"]},
                },
            ]
        }
        a = annotate_risk_block(paper, index=0, outcome="true_positive")
        self.assertTrue(a["ok"])
        b = annotate_risk_block(paper, index=1, outcome="fp")
        self.assertTrue(b["ok"])
        self.assertEqual(paper["operation_log"][1]["meta"]["outcome"], "false_positive")
        out = summarize_risk_blocks(paper["operation_log"])
        self.assertEqual(out["labeled_count"], 2)
        self.assertAlmostEqual(out["effectiveness_rate"], 0.5)


class TestRiskParityLite(unittest.TestCase):
    def test_equal_when_no_vol(self):
        from core.portfolio_optimize import optimize_weights

        out = optimize_weights(
            [
                {"stock_code": "A", "score": 90, "sector": "X"},
                {"stock_code": "B", "score": 80, "sector": "Y"},
                {"stock_code": "C", "score": 70, "sector": "X"},
            ],
            max_position_pct=40.0,
            max_sector_pct=50.0,
            max_positions=5,
            min_score=50,
            sector_map={},
            weight_mode="risk_parity_lite",
            apply_market_vol=False,
            vol_scale=1.0,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["solver"], "risk_parity_lite")
        self.assertGreaterEqual(out["count"], 2)

    def test_inverse_vol(self):
        from core.risk.budget import risk_parity_lite_weights

        w, sec, _ = risk_parity_lite_weights(
            [
                {"stock_code": "A", "score": 80, "sector": "X", "vol": 0.02},
                {"stock_code": "B", "score": 80, "sector": "Y", "vol": 0.04},
            ],
            max_position_pct=50.0,
            max_sector_pct=60.0,
            max_positions=5,
        )
        self.assertGreater(w["A"], w["B"])


if __name__ == "__main__":
    unittest.main()
