"""建仓预览（dry-run）与持仓出处标记。"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper import (
    ORIGIN_MANUAL,
    ORIGIN_MIXED,
    ORIGIN_STRATEGY,
    buy_codes_direct,
    load_paper,
    manual_buy,
    mark_to_market,
    merge_origin,
    plan_buy_codes,
    save_paper,
)

PRICES = {"600519": 10.0, "000568": 20.0, "601318": 50.0}


def _fake_query(code):
    code = str(code)
    price = PRICES.get(code)
    if price is None:
        return {"success": False, "error": f"未知代码 {code}"}
    return {
        "success": True,
        "stock_code": code,
        "stock_name": f"名{code}",
        "price_raw": price,
    }


def _paper(cash=100000.0, holdings=None):
    return {
        "name": "test",
        "cash": cash,
        "initial_cash": cash,
        "holdings": holdings or [],
        "trades": [],
    }


class TestPlanBuyCodes(unittest.TestCase):
    def test_plan_reports_shares_cost_and_remaining_cash(self):
        paper = _paper()
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, ["600519", "000568"], lot_shares=200)
        self.assertEqual(plan["lot_shares"], 200)
        self.assertEqual(plan["buy_count"], 2)
        self.assertEqual(plan["total_amount"], 200 * 10.0 + 200 * 20.0)
        self.assertEqual(plan["cash"], 100000.0)
        self.assertEqual(plan["cash_after"], 100000.0 - plan["total_amount"])
        for item in plan["items"]:
            self.assertEqual(item["shares"], 200)
            self.assertEqual(item["amount"], item["shares"] * item["price"])

    def test_plan_does_not_touch_account(self):
        paper = _paper()
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan_buy_codes(paper, ["600519"], lot_shares=200)
        self.assertEqual(paper["cash"], 100000.0)
        self.assertEqual(paper["holdings"], [])
        self.assertEqual(paper["trades"], [])

    def test_plan_skips_held_and_unaffordable(self):
        paper = _paper(cash=2500.0, holdings=[{"stock_code": "600519", "shares": 100}])
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, ["600519", "000568", "601318"], lot_shares=100)
        bought = [i["stock_code"] for i in plan["items"]]
        reasons = {s["stock_code"]: s["reason"] for s in plan["skipped"]}
        # 便宜的先买：000568 花 2000 后余 500，601318 买不起
        self.assertEqual(bought, ["000568"])
        self.assertEqual(reasons["600519"], "已持仓")
        self.assertIn("现金不足", reasons["601318"])

    def test_plan_rejects_non_lot_shares(self):
        paper = _paper()
        with self.assertRaises(ValueError):
            plan_buy_codes(paper, ["600519"], lot_shares=50)

    def test_execution_matches_preview(self):
        paper = _paper(cash=5000.0)
        codes = ["600519", "000568", "601318"]
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(paper, codes, lot_shares=100)
            result = buy_codes_direct(paper, codes, lot_shares=100)
        self.assertEqual(
            [(i["stock_code"], i["shares"], i["amount"]) for i in plan["items"]],
            [(t["stock_code"], t["shares"], t["amount"]) for t in result["trades"]],
        )
        self.assertEqual(paper["cash"], plan["cash_after"])


class TestHoldingOrigin(unittest.TestCase):
    def test_merge_origin(self):
        self.assertEqual(merge_origin(None, ORIGIN_MANUAL), ORIGIN_MANUAL)
        self.assertEqual(merge_origin("", ORIGIN_STRATEGY), ORIGIN_STRATEGY)
        self.assertEqual(merge_origin(ORIGIN_MANUAL, ORIGIN_MANUAL), ORIGIN_MANUAL)
        self.assertEqual(merge_origin(ORIGIN_MANUAL, ORIGIN_STRATEGY), ORIGIN_MIXED)
        self.assertEqual(merge_origin(ORIGIN_STRATEGY, ORIGIN_MANUAL), ORIGIN_MIXED)

    def test_buy_paths_mark_manual(self):
        paper = _paper()
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            buy_codes_direct(paper, ["600519"], lot_shares=100)
            manual_buy(paper, "000568", shares=100)
        by_code = {h["stock_code"]: h for h in paper["holdings"]}
        self.assertEqual(by_code["600519"]["origin"], ORIGIN_MANUAL)
        self.assertEqual(by_code["000568"]["origin"], ORIGIN_MANUAL)
        for trade in paper["trades"]:
            self.assertEqual(trade["origin"], ORIGIN_MANUAL)

    def test_manual_add_on_strategy_position_becomes_mixed(self):
        paper = _paper(
            holdings=[
                {
                    "stock_code": "600519",
                    "stock_name": "名600519",
                    "shares": 100,
                    "cost": 9.0,
                    "origin": ORIGIN_STRATEGY,
                }
            ]
        )
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            manual_buy(paper, "600519", shares=100)
        self.assertEqual(paper["holdings"][0]["origin"], ORIGIN_MIXED)

    def test_mark_to_market_exposes_origin_label(self):
        paper = _paper(
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0, "origin": ORIGIN_STRATEGY},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ]
        )
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            summary = mark_to_market(paper)
        by_code = {h["stock_code"]: h for h in summary["holdings"]}
        self.assertEqual(by_code["600519"]["origin_label"], "策略")
        # 早期记录没有出处，不臆造
        self.assertIsNone(by_code["000568"]["origin"])
        self.assertEqual(by_code["000568"]["origin_label"], "")

    def test_plan_per_code_shares(self):
        paper = _paper()
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519", "000568"],
                lot_shares=200,
                shares_by_code={"600519": 100, "000568": 300},
            )
        by_code = {i["stock_code"]: i for i in plan["items"]}
        self.assertEqual(by_code["600519"]["shares"], 100)
        self.assertEqual(by_code["000568"]["shares"], 300)
        self.assertEqual(plan["total_amount"], 100 * 10.0 + 300 * 20.0)

    def test_plan_by_amount_per_code(self):
        paper = _paper()
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519", "000568"],
                lot_shares=0,
                amount_per_code=2000,
            )
        by_code = {i["stock_code"]: i for i in plan["items"]}
        # 2000/10=200 股；2000/20=100 股
        self.assertEqual(by_code["600519"]["shares"], 200)
        self.assertEqual(by_code["000568"]["shares"], 100)
        self.assertEqual(plan["sizing_mode"], "amount")
        self.assertEqual(plan["cost_model"], "zero")
        self.assertEqual(plan["total_amount"], 200 * 10.0 + 100 * 20.0)

    def test_plan_by_position_pct(self):
        paper = _paper(cash=10000.0)
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            plan = plan_buy_codes(
                paper,
                ["600519"],
                lot_shares=0,
                position_pct=0.2,
            )
        # 20% of 10000 = 2000 → 200 股 @10
        self.assertEqual(plan["items"][0]["shares"], 200)
        self.assertEqual(plan["sizing_mode"], "pct")

    def test_enrich_operation_log_shows_trade_fees(self):
        from core.paper_costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "buy",
                "meta": {
                    "stock_code": "600519",
                    "shares": 100,
                    "price": 10.0,
                    "amount": 1000.0,
                },
            }
        ]
        trades = [
            {
                "side": "buy",
                "stock_code": "600519",
                "shares": 100,
                "fees": 5.0,
                "commission": 5.0,
                "stamp_duty": 0.0,
                "cost_model": "simple_cn",
            }
        ]
        out = enrich_operation_log_with_trade_fees(logs, trades)
        self.assertEqual(out[0]["meta"]["commission"], 5.0)
        self.assertEqual(out[0]["meta"]["fees"], 5.0)

    def test_enrich_estimates_strategy_buy_without_fee_fields(self):
        from core.paper_costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "buy",
                "meta": {
                    "stock_code": "601318",
                    "shares": 100,
                    "price": 54.48,
                    "amount": 5448.0,
                    "origin": "strategy",
                },
            }
        ]
        trades = [
            {
                "side": "buy",
                "stock_code": "601318",
                "shares": 100,
                "amount": 5448.0,
                "origin": "strategy",
            }
        ]
        out = enrich_operation_log_with_trade_fees(
            logs, trades, cost_model="simple_cn"
        )
        self.assertTrue(out[0]["meta"].get("fees_estimated"))
        self.assertGreater(float(out[0]["meta"].get("commission") or 0), 0)

    def test_simple_cn_fees_reduce_cash(self):
        from core.paper_costs import calc_trade_fees

        fee = calc_trade_fees("buy", 10000.0, model="simple_cn")
        self.assertEqual(fee["cost_model"], "simple_cn")
        self.assertGreater(fee["fees"], 0)
        paper = _paper(cash=100000.0)
        paper["cost_model"] = "simple_cn"
        with patch("core.ports.market.query_quote", side_effect=_fake_query):
            with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
                plan = plan_buy_codes(
                    paper, ["600519"], lot_shares=0, amount_per_code=2000
                )
                result = buy_codes_direct(
                    paper, ["600519"], lot_shares=0, amount_per_code=2000
                )
        self.assertEqual(plan["cost_model"], "simple_cn")
        self.assertGreater(plan.get("total_fees") or 0, 0)
        self.assertAlmostEqual(paper["cash"], plan["cash_after"])
        self.assertGreater(result["trades"][0].get("fees") or 0, 0)

    def test_mark_to_market_invested_and_drawdown(self):
        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0, "origin": ORIGIN_MANUAL},
            ],
        )
        paper["snapshots"] = [
            {"equity": 12000.0},
            {"equity": 9000.0},
        ]
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            summary = mark_to_market(paper)
        # cash 5000 + mv 1000 = 6000；仓位 1000/6000
        self.assertAlmostEqual(summary["invested_pct"], round(1000 / 6000 * 100, 1))
        self.assertEqual(summary["cost_model"], "zero")
        self.assertGreater(summary["max_drawdown_pct"], 0)

    def test_mark_to_market_origin_summary(self):
        paper = _paper(
            holdings=[
                {
                    "stock_code": "600519",
                    "shares": 100,
                    "cost": 10.0,
                    "origin": ORIGIN_MANUAL,
                },
                {
                    "stock_code": "000568",
                    "shares": 100,
                    "cost": 20.0,
                    "origin": ORIGIN_STRATEGY,
                },
            ]
        )
        with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
            summary = mark_to_market(paper)
        labels = {o["origin_label"]: o for o in summary["origin_summary"]}
        self.assertEqual(labels["手动"]["count"], 1)
        self.assertEqual(labels["策略"]["count"], 1)
        self.assertAlmostEqual(labels["手动"]["market_value"], 1000.0)
        self.assertAlmostEqual(labels["策略"]["market_value"], 2000.0)


class TestPlanSyncToPaper(unittest.TestCase):
    def test_preview_limited_to_watchlist(self):
        from core.watching_store import plan_sync_to_paper, read_watching, write_watching

        with tempfile.TemporaryDirectory() as tmp:
            uni_path = os.path.join(tmp, "watching.json")
            paper_path = os.path.join(tmp, "paper.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": ["600519", "000568"],
                    "watchlist_origins": ["手动", "手动"],
                    "watchlist_names": ["贵州茅台", "泸州老窖"],
                },
                uni_path,
            )
            with open(paper_path, "w", encoding="utf-8") as f:
                json.dump(_paper(), f)

            with patch("core.watching_store.read_watching", return_value=read_watching(uni_path)):
                with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
                    plan = plan_sync_to_paper(
                        paper_path,
                        codes=["600519", "999999"],
                        lot_shares=100,
                    )

            self.assertTrue(plan["success"])
            self.assertEqual([i["stock_code"] for i in plan["items"]], ["600519"])
            self.assertEqual(plan["missing"], ["999999"])
            with open(paper_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["holdings"], [])


class TestSellNavCurveSnapshots(unittest.TestCase):
    """卖出前先盯市落点：涨跌先画上，卖出点本身接近持平。"""

    def test_sell_splits_mtm_rise_from_trade(self):
        from services.paper_service import PaperService

        prices = {"600519": 10.0}

        def _q(code):
            code = str(code)
            return {
                "success": True,
                "stock_code": code,
                "stock_name": f"名{code}",
                "price_raw": prices[code],
            }

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            svc = PaperService(path)
            svc.init()
            with patch("core.paper_exec.query_quote", side_effect=_q):
                svc.set_cost_model("zero")
                svc.buy(stock_code="600519", amount=5000)

                paper = load_paper(path)
                stale_equity = float(paper["snapshots"][-1]["equity"])
                paper["snapshots"] = [
                    {
                        "ts": "2024-01-01T00:00:00.000",
                        "equity": stale_equity,
                        "cash": paper.get("cash"),
                        "stock_value": 5000.0,
                    }
                ]
                save_paper(paper, path)

                prices["600519"] = 12.0
                out = svc.sell(codes=["600519"])
                self.assertTrue(out.get("ok"), out)

                paper = load_paper(path)
                snaps = paper.get("snapshots") or []
                self.assertGreaterEqual(len(snaps), 2)
                pre, post = snaps[-2], snaps[-1]
                self.assertGreater(float(pre["equity"]), stale_equity)
                self.assertAlmostEqual(
                    float(pre["equity"]), float(post["equity"]), delta=1.0
                )
                self.assertNotEqual(pre.get("ts"), post.get("ts"))
                self.assertEqual(paper.get("holdings") or [], [])


if __name__ == "__main__":
    unittest.main()
