"""R3 · 行业/风格暴露矩阵 · 拦截原因码与有效率。"""

from __future__ import annotations

import unittest


class TestExposureMatrix(unittest.TestCase):
    def test_sector_over_limit_and_style(self):
        from core.risk.exposure import board_style_for, build_exposure_matrix

        self.assertEqual(board_style_for("300750"), "创业板")
        self.assertEqual(board_style_for("600519"), "主板沪")

        paper = {"cash": 0, "strategy_id": "short"}
        summary = {
            "equity": 100000,
            "holdings": [
                {
                    "stock_code": "300750",
                    "shares": 100,
                    "market_value": 30000,
                    "sector": "新能源",
                },
                {
                    "stock_code": "002594",
                    "shares": 100,
                    "market_value": 25000,
                    "sector": "新能源",
                },
                {
                    "stock_code": "600519",
                    "shares": 10,
                    "market_value": 20000,
                    "sector": "消费",
                },
            ],
        }
        risk = {"max_sector_pct": 40, "max_position_pct": 35}
        out = build_exposure_matrix(paper, summary, risk=risk, sector_map={})
        self.assertTrue(out["ok"])
        neo = next(r for r in out["sectors"] if r["name"] == "新能源")
        self.assertAlmostEqual(neo["weight_pct"], 55.0, places=1)
        self.assertTrue(neo["over_limit"])
        self.assertIn("新能源", out["over_limit_sectors"])
        styles = {r["name"]: r["weight_pct"] for r in out["styles"]}
        self.assertIn("创业板", styles)
        self.assertIn("主板沪", styles)

    def test_check_account_risk_codes(self):
        from core.risk.checks import check_account_risk

        paper = {"cash": 0, "strategy_id": "short", "holdings": []}
        summary = {
            "equity": 100000,
            "max_drawdown_pct": 1.0,
            "holdings": [
                {
                    "stock_code": "300750",
                    "shares": 100,
                    "market_value": 30000,
                    "sector": "新能源",
                },
                {
                    "stock_code": "002594",
                    "shares": 100,
                    "market_value": 25000,
                    "sector": "新能源",
                },
            ],
        }
        risk = {
            "max_drawdown_pct": 50,
            "max_position_pct": 40,
            "max_sector_pct": 40,
            "max_positions": 10,
        }
        out = check_account_risk(paper, summary, risk=risk)
        self.assertFalse(out["ok"])
        self.assertIn("max_sector", out["block_codes"])
        self.assertTrue(any(i["code"] == "max_sector" for i in out["block_items"]))
        self.assertTrue(out["exposure"]["over_limit_sectors"])


class TestRiskBlockEffectiveness(unittest.TestCase):
    def test_summarize_by_code_and_rates(self):
        from core.north_star import summarize_risk_blocks

        logs = [
            {
                "type": "risk_block",
                "ts": "2026-07-28T10:00:00",
                "detail": "风控拦截",
                "meta": {
                    "codes": ["max_sector"],
                    "outcome": "true_positive",
                },
            },
            {
                "type": "risk_block",
                "ts": "2026-07-29T10:00:00",
                "detail": "风控拦截",
                "meta": {
                    "codes": ["max_position"],
                    "outcome": "false_positive",
                },
            },
            {
                "type": "risk_block",
                "ts": "2026-07-29T11:00:00",
                "detail": "行业 新能源 敞口 55.0% > 上限 40%",
                "meta": {},
            },
        ]
        out = summarize_risk_blocks(logs)
        self.assertEqual(out["block_count"], 3)
        self.assertEqual(out["by_reason"].get("max_sector"), 2)
        self.assertEqual(out["by_reason"].get("max_position"), 1)
        self.assertEqual(out["labeled_count"], 2)
        self.assertAlmostEqual(out["effectiveness_rate"], 0.5)
        self.assertAlmostEqual(out["false_block_rate"], 0.5)
        self.assertTrue(out["by_day"])
        self.assertEqual(out["status"], "ok")


if __name__ == "__main__":
    unittest.main()
