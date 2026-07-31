import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.position.engine import PositionEngine, evaluate_holding, load_rules


class TestPositionRules(unittest.TestCase):
    def test_reduce_on_profit_weak_day(self):
        r = evaluate_holding(
            stock_code="600519",
            stock_name="贵州茅台",
            shares=100,
            cost=1000,
            price=1100,  # +10%
            change_pct=-2.5,
            market_value=110000,
            total_equity=200000,
            horizon_days=3,
        )
        self.assertEqual(r["action"], "减仓锁定")
        self.assertIn("suggested_weight_pct_range", r)

    def test_custom_rules_concentration(self):
        rules = load_rules()
        rules["concentration_high"] = 50
        r = evaluate_holding(
            stock_code="1",
            stock_name="x",
            shares=100,
            cost=100,
            price=100,
            change_pct=0,
            market_value=40000,
            total_equity=100000,
            rules=rules,
        )
        # 40% < 50，不应再是降集中度
        self.assertNotEqual(r["action"], "降集中度")

    def test_inline_holdings(self):
        def fake_quote(code):
            return {
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "price_raw": 1100.0,
                "change_raw": -2.5,
            }

        engine = PositionEngine()
        result = engine.advise(
            {
                "cash": 10000,
                "holdings": [{"stock_code": "600519", "shares": 10, "cost": 1000}],
                "horizon_days": 2,
            },
            quote_fn=fake_quote,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["portfolio_path"], "(inline)")
        self.assertEqual(result["source"], "inline")
        self.assertEqual(result["advice"][0]["action"], "减仓锁定")

    def test_inline_holdings_with_stance(self):
        def fake_quote(code):
            return {
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "price_raw": 1100.0,
                "change_raw": -2.5,
            }

        mock_signal = {
            "success": True,
            "signal_item": {
                "score": 40,
                "hard_reject": False,
                "data_source": "mock",
            },
        }
        mock_stance = {
            "stance_code": "wait",
            "stance_label": "建议观望（暂不买入）",
        }

        from unittest.mock import patch

        engine = PositionEngine()
        with patch("core.position.score_stock", return_value=mock_signal), patch(
            "core.position.compute_buy_stance", return_value=mock_stance
        ):
            result = engine.advise(
                {
                    "cash": 10000,
                    "holdings": [{"stock_code": "600519", "shares": 10, "cost": 1000}],
                    "include_stance": True,
                },
                quote_fn=fake_quote,
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["advice"][0]["stance_label"], "建议观望（暂不买入）")
        self.assertEqual(result["advice"][0]["signal_score"], 40)

    def test_stop_loss_reference(self):
        r = evaluate_holding(
            stock_code="1",
            stock_name="x",
            shares=100,
            cost=100,
            price=90,  # -10%
            change_pct=-3,
            market_value=9000,
            total_equity=100000,
        )
        self.assertEqual(r["action"], "止损参考")

    def test_concentration(self):
        r = evaluate_holding(
            stock_code="1",
            stock_name="x",
            shares=100,
            cost=100,
            price=100,
            change_pct=0,
            market_value=40000,
            total_equity=100000,  # 40%
        )
        self.assertEqual(r["action"], "降集中度")

    def test_default_reads_paper(self):
        paper = {
            "cash": 20000,
            "holdings": [{"stock_code": "600519", "shares": 10, "cost": 1000}],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(paper, f)
            path = f.name

        def fake_quote(code):
            return {
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "price_raw": 1100.0,
                "change_raw": -2.5,
            }

        try:
            engine = PositionEngine()
            result = engine.advise({"paper_path": path, "horizon_days": 2}, quote_fn=fake_quote)
            self.assertTrue(result["success"])
            self.assertEqual(result["source"], "paper")
            self.assertEqual(result["count"], 1)
            self.assertIn("模拟账户", result["summary"])
        finally:
            os.unlink(path)

    def test_missing_paper(self):
        engine = PositionEngine()
        result = engine.advise({"paper_path": "/tmp/not-exists-paper-xyz.json"})
        self.assertFalse(result["success"])
        self.assertIn("模拟账户", result["error"])


if __name__ == "__main__":
    unittest.main()
