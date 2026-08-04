import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.paper import init_from_example, load_paper
from core.paper_rebalance import (
    compute_turnover_stats,
    simulate_cross_section_rebalance,
)
from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic
from tests.test_p10_quant import _aligned_bars


class TestEquityCurve(unittest.TestCase):
    def test_portfolio_backtest_has_equity_curve(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(result["success"])
        curve = result.get("equity_curve") or []
        self.assertGreaterEqual(len(curve), 2)
        self.assertEqual(curve[0]["equity"], 100.0)
        self.assertIn("date", curve[-1])

    def test_portfolio_backtest_sim_trades_legs(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(result["success"])
        sim = result.get("sim_trades") or []
        self.assertGreater(len(sim), 0)
        self.assertEqual(result.get("sim_trade_count"), len(sim))
        filled = [r for r in sim if r.get("status") == "filled"]
        self.assertGreater(len(filled), 0)
        row = filled[0]
        for key in (
            "stock_code",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "return_pct",
            "factor_weights_note",
            "score_formula",
            "score_formula_terms",
            "factor_coefficients",
            "return_model_source",
        ):
            self.assertIn(key, row)
        self.assertIsNotNone(row.get("entry_price"))
        self.assertTrue(str(row.get("factor_weights_note") or ""))
        # walk-forward 拟合成功后应有分项拆解；样本过短时公式可空
        if row.get("score_formula_terms"):
            self.assertIn("terms", row["score_formula_terms"])
            self.assertTrue(row.get("factor_coefficients"))
        elif row.get("score_formula"):
            self.assertIn("ŷ", str(row.get("score_formula")))
        self.assertNotIn("weight_pct", row)


class TestWeightDiff(unittest.TestCase):
    def test_format_weight_config_diff(self):
        suggestion = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [
                    {"factor": "momentum", "ic": 0.1, "sample_count": 20},
                    {"factor": "volume_price", "ic": -0.06, "sample_count": 20},
                ],
            },
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "relative_strength": 0.2,
                "volatility": 0.1,
            },
        )
        diff = format_weight_config_diff(suggestion)
        self.assertTrue(diff["success"])
        self.assertIn("patch", diff)
        self.assertIn("weights", diff["patch"])
        self.assertIn("apply_note", diff)


class TestPaperRebalance(unittest.TestCase):
    def test_cross_section_rebalance_sells_and_buys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "600036",
                    "stock_name": "招商银行",
                    "shares": 100,
                    "cost": 30.0,
                    "bought_at": "2026-01-01T10:00:00",
                }
            ]
            paper["cash"] = 50000.0
            # 示例账本 initial_cash=100万，现金改小会触发回撤拦买
            paper["initial_cash"] = 50000.0
            paper["snapshots"] = []

            ranking = [
                {"stock_code": "600519", "stock_name": "茅台", "score": 72},
                {"stock_code": "300750", "stock_name": "宁德", "score": 68},
            ]

            def fake_query(code):
                prices = {"600519": 50.0, "300750": 40.0, "600036": 28.0}
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": prices.get(str(code), 100.0),
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ):
                result = simulate_cross_section_rebalance(paper, ranking, top_k=2)

            self.assertTrue(result["success"])
            self.assertEqual(len(result["sell_trades"]), 1)
            self.assertEqual(result["sell_trades"][0]["stock_code"], "600036")
            self.assertGreaterEqual(len(result["buy_trades"]), 1)
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertIn("600519", held)
            self.assertIn("cash_impact", result)
            self.assertIn("turnover_pct", result["cash_impact"])
            self.assertIsNotNone(result["cash_impact"]["turnover_pct"])


class TestTurnoverStats(unittest.TestCase):
    def test_two_way_turnover_and_over_limit(self):
        sell = [{"amount": 10000}]
        buy = [{"amount": 10000}, {"amount": 10000}]
        # (10k+20k)/2 / 100k * 100 = 15%
        t = compute_turnover_stats(
            sell, buy, equity_before=100000.0, max_turnover_pct=10.0
        )
        self.assertEqual(t["turnover_pct"], 15.0)
        self.assertTrue(t["over_limit"])
        self.assertEqual(t["buy_amount"], 20000.0)
        self.assertEqual(t["sell_amount"], 10000.0)

    def test_turnover_soft_cap_skips_buys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "600036",
                    "stock_name": "招商银行",
                    "shares": 1000,
                    "cost": 30.0,
                    "bought_at": "2026-01-01T10:00:00",
                }
            ]
            paper["cash"] = 200000.0
            paper["initial_cash"] = 230000.0
            paper["snapshots"] = []
            paper["rules"] = {
                **(paper.get("rules") or {}),
                "max_positions": 5,
                "position_pct": 0.5,
                "min_score": 50,
                "min_hold_score": 0,
                "max_turnover_pct": 1.0,
            }
            ranking = [
                {"stock_code": "600519", "stock_name": "茅台", "score": 80},
                {"stock_code": "300750", "stock_name": "宁德", "score": 75},
            ]

            def fake_query(code):
                prices = {"600519": 50.0, "300750": 40.0, "600036": 40.0}
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": prices.get(str(code), 100.0),
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ), patch(
                "core.paper.mark_to_market",
                return_value={
                    "equity": 240000.0,
                    "cash": 200000.0,
                    "position_count": 1,
                },
            ), patch(
                "core.risk.check_account_risk",
                return_value={"ok": True, "blocks": [], "warnings": []},
            ):
                result = simulate_cross_section_rebalance(paper, ranking, top_k=2)

            self.assertTrue(result["success"])
            # 卖出 1000*40=4万 已使换手≈8% > 1%，再买应被软上限截断
            self.assertTrue(result.get("turnover_capped") or len(result.get("buy_trades") or []) == 0)
            self.assertIn("cash_impact", result)


if __name__ == "__main__":
    unittest.main()
