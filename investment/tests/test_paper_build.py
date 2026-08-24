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


class _QuotePatchMixin:
    """plan_buy_codes / 盯市走 facade.get_quote，不能只补 StockAPI.query。"""

    def setUp(self):
        p = patch("core.data.facade.get_quote", side_effect=_fake_query)
        p.start()
        self.addCleanup(p.stop)


class TestPlanBuyCodes(_QuotePatchMixin, unittest.TestCase):
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


class TestHoldingOrigin(_QuotePatchMixin, unittest.TestCase):
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
        from core.paper.costs import enrich_operation_log_with_trade_fees

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

    def test_enrich_operation_log_backfills_sell_pnl(self):
        from core.paper.costs import enrich_operation_log_with_trade_fees

        logs = [
            {
                "type": "sell",
                "meta": {
                    "stock_code": "600519",
                    "shares": 100,
                    "price": 11.0,
                    "amount": 1100.0,
                    "commission": 5.0,
                    "fees": 5.0,
                },
            }
        ]
        trades = [
            {
                "side": "sell",
                "stock_code": "600519",
                "shares": 100,
                "amount": 1100.0,
                "pnl_pct": 10.0,
                "commission": 5.0,
                "fees": 5.0,
            }
        ]
        out = enrich_operation_log_with_trade_fees(logs, trades)
        self.assertEqual(out[0]["meta"]["pnl_pct"], 10.0)
        self.assertEqual(out[0]["meta"]["fees"], 5.0)

    def test_manual_sell_writes_pnl_pct(self):
        from core.paper import manual_sell

        paper = {
            "cash": 10000.0,
            "cost_model": "zero",
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "trades": [],
            "operation_log": [],
        }
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._quote_price", return_value=12.0):
                with patch(
                    "core.paper.rebalance._sell_match_block_reason",
                    return_value=None,
                ):
                    trades = manual_sell(paper, stock_code="600519", shares=100)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["pnl_pct"], 20.0)

    def test_enrich_estimates_strategy_buy_without_fee_fields(self):
        from core.paper.costs import enrich_operation_log_with_trade_fees

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
        from core.paper.costs import calc_trade_fees

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
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._batch_query_quotes", return_value={}):
                with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
                    summary = mark_to_market(paper)
        # cash 5000 + mv 1000 = 6000；仓位 1000/6000
        self.assertAlmostEqual(summary["invested_pct"], round(1000 / 6000 * 100, 1))
        self.assertEqual(summary["cost_model"], "zero")
        self.assertGreater(summary["max_drawdown_pct"], 0)

    def test_mark_to_market_today_pnl_from_change_pct(self):
        """今日收益 = Σ MV·chg/(100+chg)；账户%相对昨收净值。"""
        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ],
        )
        quotes = {
            "600519": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "price_raw": 11.0,
                "change_raw": 10.0,  # 昨收 10 → 今 11；ΔMV=100
            },
            "000568": {
                "success": True,
                "stock_code": "000568",
                "stock_name": "泸州",
                "price_raw": 19.0,
                "change_raw": -5.0,  # 昨收 20 → 今 19；ΔMV=-100
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=quotes):
            summary = mark_to_market(paper)
        # 今日浮动合计 0；净值 = 5000 + 1100 + 1900 = 8000；昨收净值同为 8000
        self.assertEqual(summary["today_pnl"], 0.0)
        self.assertEqual(summary["today_pnl_pct"], 0.0)
        self.assertEqual(summary["holdings"][0]["change_pct"], 10.0)
        self.assertEqual(summary["holdings"][1]["change_pct"], -5.0)

        quotes2 = {
            "600519": {
                "success": True,
                "price_raw": 11.0,
                "change_raw": 10.0,
            },
            "000568": {
                "success": True,
                "price_raw": 20.0,
                "change_raw": 0.0,
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=quotes2):
            summary2 = mark_to_market(paper)
        # ΔMV = 100；equity=5000+1100+2000=8100；昨收净值=8000；pct=1.25
        self.assertEqual(summary2["today_pnl"], 100.0)
        self.assertEqual(summary2["today_pnl_pct"], 1.25)

    def test_mark_to_market_today_pnl_vs_prev_nav(self):
        """有昨收账本时，今日收益 = 当前净值 − 上一交易日最后快照（含卖出/跳空）。"""
        paper = _paper(
            cash=5000.0,
            holdings=[{"stock_code": "600519", "shares": 100, "cost": 10.0}],
        )
        paper["initial_cash"] = 6000.0
        paper["snapshots"] = [
            {"ts": "2026-08-17T23:44:12.247", "equity": 6200.0},
        ]
        quotes = {
            "600519": {
                "success": True,
                "stock_code": "600519",
                "price_raw": 11.0,
                # 若仍按个股涨跌幅，会得到 +100 / ~1.6%；账本口径应对 6200
                "change_raw": 10.0,
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=quotes):
            summary = mark_to_market(paper)
        # equity = 5000 + 1100 = 6100；相对昨收账本 6200 → -100 / -1.61%
        self.assertEqual(summary["today_pnl_basis"], "prev_nav")
        self.assertEqual(summary["today_pnl"], -100.0)
        self.assertAlmostEqual(summary["today_pnl_pct"], round(-100.0 / 6200.0 * 100.0, 2))
        self.assertAlmostEqual(summary["total_pnl_pct"], round((6100 / 6000 - 1) * 100.0, 2))

    def test_mark_to_market_today_pnl_cash_only(self):
        paper = _paper(cash=10000.0, holdings=[])
        summary = mark_to_market(paper)
        self.assertEqual(summary["today_pnl"], 0.0)
        self.assertEqual(summary["today_pnl_pct"], 0.0)

    def test_mark_to_market_today_pnl_uses_reset_anchor(self):
        """当日回零后，今日收益相对回零价，不再吃昨收涨跌幅。"""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        paper = _paper(
            cash=5000.0,
            holdings=[
                {"stock_code": "600519", "shares": 100, "cost": 10.0},
                {"stock_code": "000568", "shares": 100, "cost": 20.0},
            ],
        )
        paper["initial_cash"] = 8100.0
        paper["pnl_anchor"] = {
            "ts": f"{today}T10:00:00",
            "date": today,
            "equity": 8100.0,
            "prices": {"600519": 11.0, "000568": 20.0},
        }
        quotes = {
            "600519": {
                "success": True,
                "price_raw": 12.0,
                "change_raw": 20.0,  # 昨收口径会算更大；锚点只认 +1
            },
            "000568": {
                "success": True,
                "price_raw": 20.0,
                "change_raw": 5.0,
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=quotes):
            summary = mark_to_market(paper)
        # (12-11)*100 + (20-20)*100 = 100；相对回零净值 8100 → 1.23%
        self.assertEqual(summary["today_pnl_basis"], "reset")
        self.assertEqual(summary["today_pnl"], 100.0)
        self.assertEqual(summary["today_pnl_pct"], 1.23)

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
        with patch("core.paper.exec._query_quote", side_effect=_fake_query):
            with patch("core.paper.exec._batch_query_quotes", return_value={}):
                with patch("skills.common.quote_api.StockAPI.query", side_effect=_fake_query):
                    summary = mark_to_market(paper)
        labels = {o["origin_label"]: o for o in summary["origin_summary"]}
        self.assertEqual(labels["手动"]["count"], 1)
        self.assertEqual(labels["策略"]["count"], 1)
        self.assertAlmostEqual(labels["手动"]["market_value"], 1000.0)
        self.assertAlmostEqual(labels["策略"]["market_value"], 2000.0)


class TestPlanSyncToPaper(_QuotePatchMixin, unittest.TestCase):
    def test_preview_limited_to_watchlist(self):
        from core.watching.store import plan_sync_to_paper, read_watching, write_watching

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

            with patch("core.watching.store.read_watching", return_value=read_watching(uni_path)):
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

        def _batch(codes):
            return {str(c): _q(c) for c in codes}

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            svc = PaperService(path)
            svc.init()
            with patch("core.paper.open_fill.require_open_fill", return_value=None):
                with patch("core.paper.exec._query_quote", side_effect=_q):
                    with patch("core.paper.exec._batch_query_quotes", side_effect=_batch):
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
                        paper = load_paper(path)
                        from core.market.calendar import prev_trading_day
                        from core.paper.tplus1 import session_date

                        sess = session_date()
                        prev = prev_trading_day(sess) or "2026-08-20"
                        for h in paper.get("holdings") or []:
                            h["bought_at"] = f"{prev}T10:00:00"
                            h["lots"] = [
                                {
                                    "shares": float(h.get("shares") or 0),
                                    "bought_at": h["bought_at"],
                                    "bought_date": prev,
                                }
                            ]
                        save_paper(paper, path)
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


class TestSnapshotsForUiLiveOverlay(unittest.TestCase):
    """盘中盯市与落盘快照分叉时，UI 曲线末点应对齐摘要总净值。"""

    def test_appends_live_point_when_mtm_moved(self):
        from core.paper import snapshots_for_ui

        paper = {
            "snapshots": [
                {
                    "ts": "2026-08-18T11:48:25.999",
                    "equity": 99976.3,
                    "cash": 55040.3,
                    "stock_value": 44936.0,
                }
            ]
        }
        summary = {
            "equity": 100238.3,
            "cash": 55040.3,
            "stock_value": 45198.0,
            "total_pnl_pct": 0.24,
            "position_count": 5,
        }
        out = snapshots_for_ui(paper, summary)
        self.assertEqual(len(out), 2)
        self.assertFalse(out[0].get("live"))
        self.assertTrue(out[-1].get("live"))
        self.assertAlmostEqual(float(out[-1]["equity"]), 100238.3)
        self.assertGreater(str(out[-1]["ts"]), str(out[0]["ts"]))

    def test_skips_overlay_when_already_aligned(self):
        from core.paper import snapshots_for_ui

        paper = {
            "snapshots": [{"ts": "2026-08-18T11:48:25.999", "equity": 99976.3}]
        }
        out = snapshots_for_ui(paper, {"equity": 99976.3, "cash": 1})
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].get("live"))

    def test_does_not_mutate_paper_snapshots(self):
        from core.paper import snapshots_for_ui

        paper = {"snapshots": [{"ts": "2026-08-18T11:48:25.999", "equity": 100.0}]}
        snapshots_for_ui(paper, {"equity": 110.0})
        self.assertEqual(len(paper["snapshots"]), 1)
        self.assertEqual(paper["snapshots"][0]["equity"], 100.0)


if __name__ == "__main__":
    unittest.main()
