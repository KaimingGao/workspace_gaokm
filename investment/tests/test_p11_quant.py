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
        # next_open：意图价→开盘价应还原缺口，ŷ_EOD_rem 不再等于裸 ŷ_EOD
        if row.get("intent_price") and row.get("entry_price") and row.get("predicted_score") is not None:
            self.assertIsNotNone(row.get("realized_t1_to_tau"))
            self.assertIsNotNone(row.get("predicted_score_eod_rem"))
            if abs(float(row["realized_t1_to_tau"])) > 1e-9:
                self.assertNotAlmostEqual(
                    float(row["predicted_score_eod_rem"]),
                    float(row["predicted_score"]),
                    places=5,
                )
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

    def test_rebalance_floors_are_json_safe(self):
        """未设 ŷ 门槛时内部用 -inf，API 落盘必须是 null（Starlette allow_nan=False）。"""
        import json
        from core.signal.score_display import json_safe, json_safe_number

        self.assertIsNone(json_safe_number(float("-inf")))
        self.assertIsNone(json_safe_number(float("nan")))
        payload = json_safe(
            {
                "min_score": float("-inf"),
                "min_hold_score": float("inf"),
                "last_optimize": {"limits": {"min_score": float("-inf")}},
            }
        )
        json.dumps(payload, allow_nan=False)
        self.assertIsNone(payload["min_score"])
        self.assertIsNone(payload["last_optimize"]["limits"]["min_score"])


class TestClusterSellHysteresis(unittest.TestCase):
    """分池：卖出仅 ŷ < min_hold；中间带不因未进簿清仓。"""

    def test_cluster_keeps_mid_band_outside_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "000739",
                    "stock_name": "高分",
                    "shares": 200,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
                {
                    "stock_code": "600938",
                    "stock_name": "中间带",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
                {
                    "stock_code": "601658",
                    "stock_name": "弱分",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
            ]
            paper["cash"] = 200000.0
            paper["rules"] = {
                **(paper.get("rules") or {}),
                "max_positions": 5,
                "position_pct": 0.2,
            }
            # 目标簿仅高分；中间带/弱分不在簿
            ranking = [
                {"stock_code": "000739", "stock_name": "高分", "score": 1.87},
            ]
            score_lookup = [
                {"stock_code": "000739", "score": 1.87},
                {"stock_code": "600938", "score": 0.426},
                {"stock_code": "601658", "score": -1.5},
            ]

            def fake_query(code):
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": 20.0,
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ), patch(
                "core.risk.check_account_risk",
                return_value={"ok": True, "blocks": [], "warnings": []},
            ):
                result = simulate_cross_section_rebalance(
                    paper,
                    ranking,
                    top_k=1,
                    min_score=1.0,
                    respect_max_positions=False,
                    score_lookup=score_lookup,
                )

            self.assertTrue(result["success"])
            sold = {t["stock_code"] for t in result.get("sell_trades") or []}
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertNotIn("600938", sold)
            self.assertIn("600938", held)
            self.assertIn("000739", held)
            self.assertIn("601658", sold)
            self.assertNotIn("601658", held)
            note = (result["sell_trades"][0].get("note") or "")
            self.assertIn("卖出门槛", note)

    def test_cluster_buys_book_while_over_capacity_hysteresis(self):
        """滞回持仓多于簿长时，仍应买入未持仓的目标簿票。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            # 3 只中间带（均不在簿、ŷ 高于卖门）→ 账户仓位 > 簿长
            paper["holdings"] = [
                {
                    "stock_code": "600938",
                    "stock_name": "中间A",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
                {
                    "stock_code": "600900",
                    "stock_name": "中间B",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
                {
                    "stock_code": "600276",
                    "stock_name": "中间C",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
            ]
            paper["cash"] = 500000.0
            paper["rules"] = {
                **(paper.get("rules") or {}),
                "max_positions": 5,
                "position_pct": 0.2,
            }
            ranking = [
                {"stock_code": "000739", "stock_name": "簿内新票", "score": 2.5},
                {"stock_code": "600426", "stock_name": "簿内新票2", "score": 2.0},
            ]
            score_lookup = [
                {"stock_code": "000739", "score": 2.5},
                {"stock_code": "600426", "score": 2.0},
                {"stock_code": "600938", "score": 0.5},
                {"stock_code": "600900", "score": 0.4},
                {"stock_code": "600276", "score": 0.3},
            ]

            def fake_query(code):
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": 20.0,
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ), patch(
                "core.risk.check_account_risk",
                return_value={"ok": True, "blocks": [], "warnings": []},
            ):
                result = simulate_cross_section_rebalance(
                    paper,
                    ranking,
                    top_k=2,
                    min_score=1.0,
                    respect_max_positions=False,
                    score_lookup=score_lookup,
                )

            self.assertTrue(result["success"])
            bought = {t["stock_code"] for t in result.get("buy_trades") or []}
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertIn("000739", bought)
            self.assertIn("600426", bought)
            self.assertIn("000739", held)
            self.assertIn("600426", held)
            # 中间带未清仓
            self.assertIn("600938", held)
            self.assertIn("600900", held)
            self.assertIn("600276", held)

    def test_cluster_hard_reject_sells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "601138",
                    "stock_name": "工业富联",
                    "shares": 100,
                    "cost": 50.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
                {
                    "stock_code": "000739",
                    "stock_name": "高分",
                    "shares": 100,
                    "cost": 10.0,
                    "bought_at": "2026-01-01T10:00:00",
                },
            ]
            paper["cash"] = 200000.0
            paper["rules"] = {
                **(paper.get("rules") or {}),
                "max_positions": 5,
                "position_pct": 0.2,
            }
            ranking = [{"stock_code": "000739", "stock_name": "高分", "score": 1.87}]
            score_lookup = [
                {"stock_code": "000739", "score": 1.87},
                {
                    "stock_code": "601138",
                    "score": None,
                    "hard_reject": True,
                    "reject_reason": "近3日涨幅过大(17.6%)，短线追高风险高",
                },
            ]

            def fake_query(code):
                return {
                    "success": True,
                    "stock_code": str(code),
                    "stock_name": str(code),
                    "price_raw": 20.0,
                }

            with patch(
                "skills.common.quote_api.StockAPI.query", side_effect=fake_query
            ), patch(
                "core.ports.market.query_quote", side_effect=fake_query
            ), patch(
                "core.paper_rebalance._quote_price",
                side_effect=lambda q: float((q or {}).get("price_raw") or 0) or None,
            ), patch(
                "core.risk.check_account_risk",
                return_value={"ok": True, "blocks": [], "warnings": []},
            ):
                result = simulate_cross_section_rebalance(
                    paper,
                    ranking,
                    top_k=1,
                    min_score=1.0,
                    respect_max_positions=False,
                    score_lookup=score_lookup,
                )

            self.assertTrue(result["success"])
            sold = {t["stock_code"]: t for t in result.get("sell_trades") or []}
            self.assertIn("601138", sold)
            self.assertIn("追高", sold["601138"].get("note") or "")
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertNotIn("601138", held)
            self.assertIn("000739", held)


if __name__ == "__main__":
    unittest.main()
