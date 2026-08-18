"""打分粗筛 / 分池 force_trim / 行业减仓 / 回撤下舆情恢复 — 回归。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestSelectForceTrimCodes(unittest.TestCase):
    def test_prefers_mid_band_over_low_score_book(self):
        from core.paper_rebalance import select_force_trim_codes

        holdings = [
            {"stock_code": "A"},
            {"stock_code": "B"},
            {"stock_code": "M1"},
            {"stock_code": "M2"},
        ]
        scores = {"A": 0.2, "B": 2.0, "M1": 1.0, "M2": 0.9}
        out = select_force_trim_codes(
            holdings,
            score_by_code=scores,
            top_codes={"A", "B"},
            trim_count=2,
        )
        self.assertEqual(out, ["M2", "M1"])


class TestScoreUniversePrefilter(unittest.TestCase):
    def test_uses_prior_yhat_not_day_change(self):
        from core.signal.cluster_rank import select_score_universe

        codes = [f"{i:06d}" for i in range(1, 301)]
        prior = {c: float(i) for i, c in enumerate(codes)}  # higher index = higher ŷ
        # 当日涨跌：低序号动量更高（若误用涨跌幅会选错）
        with patch(
            "core.signal.cluster_rank._prior_yhat_by_code",
            return_value=(prior, "ledger_yhat"),
        ), patch("core.ports.market.batch_query_quotes") as mock_q:
            selected, ranked, axis = select_score_universe(codes, cap=240)
        mock_q.assert_not_called()
        self.assertTrue(ranked)
        # S2: axis 后缀带 +unknown{X}% 配额标记（默认 20%）；语义仍以先验 ŷ 为主
        self.assertTrue(axis.startswith("ledger_yhat"), f"axis={axis}")
        self.assertIn("unknown", axis)
        self.assertEqual(len(selected), 240)
        # 先验最高的票必须入选；动量最高的低分票可被挤出
        self.assertIn("000300", selected)
        self.assertNotIn("000001", selected)


class TestForceTrimIntegration(unittest.TestCase):
    def test_force_trim_sells_mid_band_keeps_book(self):
        from core.paper_rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 50000.0,
            "strategy_id": "short",
            "rules": {
                "max_positions": 3,
                "position_pct": 0.2,
                "min_hold_predicted_score": -10.0,
                "min_predicted_score": 0.0,
            },
            "holdings": [
                {
                    "stock_code": "000001",
                    "stock_name": "A",
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                },
                {
                    "stock_code": "000002",
                    "stock_name": "B",
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                },
                {
                    "stock_code": "000003",
                    "stock_name": "C",
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                },
                {
                    "stock_code": "000004",
                    "stock_name": "M1",
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                },
                {
                    "stock_code": "000005",
                    "stock_name": "M2",
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                },
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "000001",
                "stock_name": "A",
                "score": 0.2,
                "predicted_score": 0.2,
                "predicted_score_eod": 1.0,
                "predicted_score_tau": 0.5,
            },
            {
                "stock_code": "000002",
                "stock_name": "B",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.5,
            },
            {
                "stock_code": "000003",
                "stock_name": "C",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.5,
            },
        ]
        score_lookup = ranking + [
            {"stock_code": "000004", "score": 1.0, "predicted_score": 1.0},
            {"stock_code": "000005", "score": 0.8, "predicted_score": 0.8},
        ]

        def fake_quotes(codes):
            return {
                c: {
                    "success": True,
                    "price": 10.0,
                    "price_raw": 10.0,
                    "prev_close": 10.0,
                    "change_raw": 0.0,
                    "stock_name": c,
                }
                for c in codes
            }

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={"ok": True, "blocks": [], "warnings": [], "limits": {}},
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.signal.dual_score.buy_passes_tau_gate",
            return_value=(True, None),
        ):
            simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=3,
                min_score=0.0,
                respect_max_positions=False,
                score_lookup=score_lookup,
                skip_sentiment_prior=True,
            )

        held = {h["stock_code"] for h in paper["holdings"]}
        force_sells = [
            t["stock_code"]
            for t in paper["trades"]
            if t.get("side") == "sell" and "强制减仓" in str(t.get("note") or "")
        ]
        # 应卸中间带，保留簿内（含低分 A）
        self.assertTrue(set(force_sells) <= {"000004", "000005"})
        self.assertIn("000001", held)
        self.assertIn("000002", held)
        self.assertIn("000003", held)
        self.assertLessEqual(len(held), 3)


class TestForceTrimIncompleteNoCrash(unittest.TestCase):
    def test_limit_down_overflow_sets_warning_not_unbound(self):
        """膨胀 + 全员跌停：可卖 < 需卸时不得 UnboundLocalError，并保留 warning。"""
        from core.paper_rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 50000.0,
            "strategy_id": "short",
            "rules": {
                "max_positions": 2,
                "position_pct": 0.2,
                "min_hold_predicted_score": -10.0,
                "min_predicted_score": 0.0,
            },
            "holdings": [
                {
                    "stock_code": f"00000{i}",
                    "stock_name": str(i),
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 900,
                }
                for i in range(1, 5)
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "000001",
                "stock_name": "1",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.5,
            },
            {
                "stock_code": "000002",
                "stock_name": "2",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.5,
            },
        ]
        score_lookup = ranking + [
            {"stock_code": "000003", "score": 1.0, "predicted_score": 1.0},
            {"stock_code": "000004", "score": 0.8, "predicted_score": 0.8},
        ]

        def fake_quotes(codes):
            return {
                c: {
                    "success": True,
                    "price": 9.0,
                    "price_raw": 9.0,
                    "prev_close": 10.0,
                    "change_raw": -10.0,
                    "stock_name": c,
                }
                for c in codes
            }

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={"ok": True, "blocks": [], "warnings": ["risk_ok"], "limits": {}},
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.signal.dual_score.buy_passes_tau_gate",
            return_value=(True, None),
        ):
            out = simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=2,
                min_score=0.0,
                respect_max_positions=False,
                score_lookup=score_lookup,
                skip_sentiment_prior=True,
            )

        gate = out.get("risk_gate") or {}
        self.assertTrue(gate.get("force_trim_incomplete"))
        warns = " ".join(str(w) for w in (gate.get("warnings") or []))
        self.assertIn("膨胀减仓", warns)
        self.assertIn("risk_ok", warns)
        self.assertEqual(len(paper["holdings"]), 4)


class TestForceTrimDoesNotBuyBackInBook(unittest.TestCase):
    def test_limit_down_mid_band_does_not_rebuy_cut_book(self):
        """中间带跌停时卸簿内：本轮不得立刻买回同一批簿内票。"""
        from core.paper_rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 50000.0,
            "strategy_id": "short",
            "rules": {
                "max_positions": 2,
                "position_pct": 0.2,
                "min_hold_predicted_score": -10.0,
                "min_predicted_score": 0.0,
            },
            "holdings": [
                {
                    "stock_code": c,
                    "stock_name": c,
                    "shares": 100,
                    "cost": 10,
                    "origin": "strategy",
                    "market_value": 1000,
                }
                for c in ("000001", "000002", "000003", "000004")
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "000001",
                "stock_name": "000001",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.5,
                "score_scale": "predicted_yhat",
            },
            {
                "stock_code": "000002",
                "stock_name": "000002",
                "score": 1.8,
                "predicted_score": 1.8,
                "predicted_score_eod": 1.8,
                "predicted_score_tau": 0.5,
                "score_scale": "predicted_yhat",
            },
        ]
        score_lookup = ranking + [
            {"stock_code": "000003", "score": 1.0, "predicted_score": 1.0},
            {"stock_code": "000004", "score": 0.8, "predicted_score": 0.8},
        ]

        def fake_quotes(codes):
            out = {}
            for c in codes:
                if c in ("000003", "000004"):
                    out[c] = {
                        "success": True,
                        "price": 9.0,
                        "price_raw": 9.0,
                        "prev_close": 10.0,
                        "change_raw": -10.0,
                        "stock_name": c,
                    }
                else:
                    out[c] = {
                        "success": True,
                        "price": 10.0,
                        "price_raw": 10.0,
                        "prev_close": 10.0,
                        "change_raw": 0.0,
                        "stock_name": c,
                    }
            return out

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={"ok": True, "blocks": [], "warnings": [], "limits": {}},
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.signal.dual_score.buy_passes_tau_gate",
            return_value=(True, None),
        ):
            out = simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=2,
                min_score=0.0,
                respect_max_positions=False,
                score_lookup=score_lookup,
                skip_sentiment_prior=True,
            )

        held = {h["stock_code"] for h in paper["holdings"]}
        force_sells = [
            t["stock_code"]
            for t in paper["trades"]
            if t.get("side") == "sell" and "强制减仓" in str(t.get("note") or "")
        ]
        buys = [t["stock_code"] for t in paper["trades"] if t.get("side") == "buy"]
        self.assertTrue(set(force_sells) <= {"000001", "000002"})
        self.assertTrue(force_sells)
        self.assertNotIn("000001", buys)
        self.assertNotIn("000002", buys)
        self.assertTrue({"000003", "000004"} <= held)
        warns = " ".join(str(w) for w in ((out.get("risk_gate") or {}).get("warnings") or []))
        self.assertIn("不买回", warns)


class TestSentimentRestoreUnderDrawdown(unittest.TestCase):
    def test_restore_runs_when_drawdown_blocks_new_buys(self):
        from core.paper_rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 100000.0,
            "strategy_id": "short",
            "rules": {"max_positions": 5, "position_pct": 0.2},
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 100.0,
                    "origin": "strategy",
                    "market_value": 10000,
                    "sentiment_trim_base_shares": 200,
                }
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "score": 1.5,
                "predicted_score_eod": 1.5,
                "predicted_score_tau": 0.5,
            }
        ]

        def fake_quotes(codes):
            return {
                c: {
                    "success": True,
                    "price": 100.0,
                    "price_raw": 100.0,
                    "prev_close": 100.0,
                    "change_raw": 0.0,
                    "stock_name": "茅台",
                }
                for c in codes
            }

        prior = {
            "600519": {
                "success": True,
                "active": False,
                "actions": [],
                "mode": "gate",
            }
        }

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={
                "ok": False,
                "blocks": ["账户当前回撤超限"],
                "block_items": [
                    {"code": "drawdown_limit", "message": "回撤超限：暂停加仓"}
                ],
                "warnings": [],
                "limits": {"max_position_pct": 25.0, "max_sector_pct": 40.0},
            },
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={
                "mode": "gate",
                "scale_holds": True,
                "scale_buy_pct": 0.5,
                "block_new_buys": False,
                "warn_only": False,
                "reduce_avoid_on_bullish": True,
            },
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.check_sentiment_priors_for_codes",
            return_value={
                "ok": True,
                "by_code": prior,
                "warnings": [],
                "blocks": [],
            },
        ):
            out = simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=1,
                min_score=0.0,
                respect_max_positions=True,
                skip_sentiment_prior=False,
            )

        restores = [
            t
            for t in paper["trades"]
            if t.get("side") == "buy" and t.get("sentiment_restore")
        ]
        new_buys = [
            t
            for t in paper["trades"]
            if t.get("side") == "buy" and not t.get("sentiment_restore")
        ]
        self.assertTrue(restores, msg="回撤硬拦时仍应允许舆情补回")
        self.assertEqual(new_buys, [])
        held = paper["holdings"][0]
        self.assertGreaterEqual(float(held.get("shares") or 0), 200)
        self.assertIsNone(held.get("sentiment_trim_base_shares"))
        self.assertTrue(out.get("success", True))


class TestSentimentRestoreTurnoverBudget(unittest.TestCase):
    def test_restore_respects_turnover_budget(self):
        from core.paper_rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 100000.0,
            "strategy_id": "short",
            "rules": {
                "max_positions": 5,
                "position_pct": 0.2,
                "max_turnover_pct": 0.1,
            },
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 100.0,
                    "origin": "strategy",
                    "market_value": 10000,
                    "sentiment_trim_base_shares": 200,
                }
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "score": 1.5,
                "predicted_score": 1.5,
                "predicted_score_eod": 1.5,
                "predicted_score_tau": 0.5,
                "score_scale": "predicted_yhat",
            }
        ]

        def fake_quotes(codes):
            return {
                c: {
                    "success": True,
                    "price": 100.0,
                    "price_raw": 100.0,
                    "prev_close": 100.0,
                    "change_raw": 0.0,
                    "stock_name": "x",
                }
                for c in codes
            }

        prior = {
            "600519": {
                "success": True,
                "active": False,
                "actions": [],
                "mode": "gate",
            }
        }

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={
                "ok": True,
                "blocks": [],
                "warnings": [],
                "limits": {"max_position_pct": 25.0, "max_sector_pct": 40.0},
            },
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={
                "mode": "gate",
                "scale_holds": True,
                "scale_buy_pct": 0.5,
                "block_new_buys": False,
                "warn_only": False,
                "reduce_avoid_on_bullish": True,
            },
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.check_sentiment_priors_for_codes",
            return_value={
                "ok": True,
                "by_code": prior,
                "warnings": [],
                "blocks": [],
            },
        ):
            simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=1,
                min_score=0.0,
                respect_max_positions=True,
                skip_sentiment_prior=False,
            )

        restores = [
            t
            for t in paper["trades"]
            if t.get("side") == "buy" and t.get("sentiment_restore")
        ]
        self.assertEqual(restores, [])
        self.assertEqual(float(paper["holdings"][0].get("shares") or 0), 100)


class TestSectorRiskTrim(unittest.TestCase):
    def test_sector_overweight_trims_lowest_score(self):
        from core.paper_rebalance import simulate_cross_section_rebalance

        # equity ~ 100k; two names same sector each 30k → sector 60% > 40%
        paper = {
            "cash": 40000.0,
            "strategy_id": "short",
            "rules": {"max_positions": 5, "position_pct": 0.2},
            "holdings": [
                {
                    "stock_code": "000001",
                    "stock_name": "低分",
                    "shares": 3000,
                    "cost": 10.0,
                    "origin": "strategy",
                    "market_value": 30000,
                    "sector": "银行",
                },
                {
                    "stock_code": "000002",
                    "stock_name": "高分",
                    "shares": 3000,
                    "cost": 10.0,
                    "origin": "strategy",
                    "market_value": 30000,
                    "sector": "银行",
                },
            ],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "000001",
                "score": 0.5,
                "predicted_score_eod": 0.5,
                "predicted_score_tau": 0.2,
                "sector": "银行",
            },
            {
                "stock_code": "000002",
                "score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_tau": 0.2,
                "sector": "银行",
            },
        ]

        def fake_quotes(codes):
            return {
                c: {
                    "success": True,
                    "price": 10.0,
                    "price_raw": 10.0,
                    "prev_close": 10.0,
                    "change_raw": 0.0,
                    "stock_name": c,
                }
                for c in codes
            }

        with patch(
            "core.paper_rebalance._batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.batch_query_quotes", side_effect=fake_quotes
        ), patch(
            "core.ports.market.query_quote",
            side_effect=lambda c: fake_quotes([c])[c],
        ), patch(
            "core.risk.check_account_risk",
            return_value={
                "ok": True,
                "blocks": [],
                "block_items": [],
                "warnings": [],
                "limits": {"max_position_pct": 50.0, "max_sector_pct": 40.0},
            },
        ), patch(
            "core.portfolio_optimize._sector_for", return_value="银行"
        ), patch(
            "core.event_prior.get_event_prior_cfg",
            return_value={"mode": "off"},
        ), patch(
            "core.sentiment_prior.get_sentiment_prior_cfg",
            return_value={"mode": "off"},
        ):
            simulate_cross_section_rebalance(
                paper,
                ranking,
                top_k=2,
                min_score=0.0,
                respect_max_positions=True,
                score_lookup=ranking,
                skip_sentiment_prior=True,
            )

        sector_sells = [
            t
            for t in paper["trades"]
            if t.get("side") == "sell" and "行业" in str(t.get("note") or "")
        ]
        self.assertTrue(sector_sells)
        # 低分票应先被减
        self.assertEqual(sector_sells[0]["stock_code"], "000001")


class TestEodResolveNoBlendFallback(unittest.TestCase):
    def test_score_matching_blend_is_not_eod(self):
        from core.signal.dual_score import (
            eod_gate_score_for_item,
            resolve_predicted_score_eod,
        )

        item = {
            "score": 0.525,  # ŷ_trade after align
            "predicted_score_blend": 0.525,
            "predicted_score_tau": 0.35,
        }
        self.assertIsNone(resolve_predicted_score_eod(item))
        self.assertIsNone(eod_gate_score_for_item(item))

    def test_heuristic_score_with_tau_still_gates(self):
        """仅有 0–100 ``score`` 时不得当作 ŷ_EOD（避免 OOS 失败票漏进 Top）。"""
        from core.signal.dual_score import resolve_predicted_score_eod

        self.assertIsNone(
            resolve_predicted_score_eod({"score": 72.0, "predicted_score_tau": 1.0})
        )

    def test_explicit_eod_wins_over_blend_score(self):
        from core.signal.dual_score import resolve_predicted_score_eod

        self.assertAlmostEqual(
            resolve_predicted_score_eod(
                {
                    "score": 0.525,
                    "predicted_score_blend": 0.525,
                    "predicted_score": 0.7,
                    "predicted_score_eod": 0.7,
                }
            ),
            0.7,
        )

    def test_yhat_pct_ge_10_with_scale_is_not_heuristic(self):
        """涨停板 ŷ%≥10 且标明 predicted_yhat 时，回退 score 不得丢 ŷ_EOD。"""
        from core.signal.dual_score import resolve_predicted_score_eod
        from core.signal.score_display import looks_like_legacy_heuristic_score

        item = {
            "score": 12.5,
            "score_scale": "predicted_yhat",
        }
        self.assertFalse(looks_like_legacy_heuristic_score(12.5, item=item))
        self.assertAlmostEqual(resolve_predicted_score_eod(item), 12.5)
        # 未标明尺的大数仍按遗留 0–100 拒绝
        self.assertIsNone(resolve_predicted_score_eod({"score": 12.5}))
        from quant.services.quant_report_export import _fmt_yhat, _yhat_from_row

        self.assertEqual(_fmt_yhat(12.5), "12.500%")
        self.assertAlmostEqual(_yhat_from_row(item), 12.5)
        self.assertIsNone(_yhat_from_row({"score": 55.0}))


class TestOptimizeEligibilityUsesEod(unittest.TestCase):
    def test_low_blend_high_eod_still_gets_weight(self):
        from core.portfolio_optimize import optimize_weights

        candidates = [
            {
                "stock_code": "A",
                "score": 0.525,  # blend
                "predicted_score": 0.7,
                "predicted_score_eod": 0.7,
                "predicted_score_tau": 0.35,
                "predicted_score_blend": 0.525,
                "sector": "其他",
            },
            {
                "stock_code": "B",
                "score": 0.9,
                "predicted_score": 0.9,
                "predicted_score_eod": 0.9,
                "predicted_score_tau": 0.9,
                "predicted_score_blend": 0.9,
                "sector": "其他",
            },
        ]
        out = optimize_weights(
            candidates,
            max_position_pct=50.0,
            max_sector_pct=100.0,
            max_positions=5,
            min_score=0.6,
            weight_mode="score_budget",
            apply_market_vol=False,
            apply_regime_scale=False,
        )
        weights = out.get("weights_pct") or {}
        self.assertIn("A", weights, msg="EOD≥floor 即使 blend<floor 也应入目标仓")
        self.assertIn("B", weights)


class TestTurnoverBudgetClip(unittest.TestCase):
    def test_clips_to_remaining_buy_budget(self):
        from core.paper_rebalance import clip_shares_to_turnover_budget

        # equity=100k, max_to=2% → single-side 1k；已买 800 → 剩 200
        # px=10 → 最多 20 股，取整 0 手？ 200/10=20 → 0*100
        # use px=1 → 200 股
        sh = clip_shares_to_turnover_budget(
            shares=1000,
            fill_px=1.0,
            buy_amt_so_far=800.0,
            buy_budget_amt=1000.0,
            sell_amt=0.0,
            equity_before=100000.0,
            max_turnover_pct=2.0,
        )
        self.assertEqual(sh, 200)

    def test_zero_when_budget_exhausted(self):
        from core.paper_rebalance import clip_shares_to_turnover_budget

        sh = clip_shares_to_turnover_budget(
            shares=500,
            fill_px=10.0,
            buy_amt_so_far=1000.0,
            buy_budget_amt=1000.0,
            sell_amt=0.0,
            equity_before=100000.0,
            max_turnover_pct=2.0,
        )
        self.assertEqual(sh, 0)


if __name__ == "__main__":
    unittest.main()
