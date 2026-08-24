"""Unit tests for paper rebalance orchestrator mode routing (C3)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper.rebalance.orchestrator import (  # noqa: E402
    resolve_rebalance_mode,
    run_paper_rebalance,
)


class TestResolveRebalanceMode(unittest.TestCase):
    def test_defaults_to_cross_section(self):
        paper = {"rules": {}}
        with patch(
            "core.signal.cluster.live.get_cluster_scoring_cfg",
            return_value={"mode": "off"},
        ):
            self.assertEqual(resolve_rebalance_mode(paper), "cross_section")

    def test_cluster_scoring_active(self):
        paper = {"rules": {}}
        with patch(
            "core.signal.cluster.live.get_cluster_scoring_cfg",
            return_value={"mode": "active"},
        ):
            self.assertEqual(resolve_rebalance_mode(paper), "cluster_book")

    def test_cluster_mode_flag(self):
        paper = {"rules": {"cluster_mode": True}}
        with patch(
            "core.signal.cluster.live.get_cluster_scoring_cfg",
            return_value={"mode": "off"},
        ):
            self.assertEqual(
                resolve_rebalance_mode(paper, cluster_mode=False), "cluster_book"
            )


class TestRunPaperRebalanceRouting(unittest.TestCase):
    def test_holding_rules_delegates_to_daily_cycle(self):
        paper = {"holdings": [], "rules": {}}
        with patch(
            "core.paper.cycle.run_daily_cycle",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_cycle:
            out = run_paper_rebalance(
                paper,
                mode="holding_rules",
                simulate_buy=True,
                strategy="short",
            )
        mock_cycle.assert_called_once()
        self.assertEqual(out["mode"], "holding_rules")
        self.assertTrue(out["ok"])

    def test_cross_section_delegates_to_simulator(self):
        paper = {"holdings": [], "rules": {"max_positions": 5}, "strategy_id": "short"}
        ranking = [{"stock_code": "600519", "score": 80}]

        class _Book:
            def as_dict(self):
                return {"success": True, "ranking": ranking}

        mock_svc = MagicMock()
        mock_svc.rank_cross_section.return_value = _Book()
        with patch(
            "core.signal.service.get_default_signal_service",
            return_value=mock_svc,
        ), patch(
            "core.paper.rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ):
            out = run_paper_rebalance(paper, mode="cross_section", top_k=3)
        mock_sim.assert_called_once()
        mock_svc.rank_cross_section.assert_called_once()
        self.assertEqual(out["mode"], "cross_section")
        self.assertFalse(out["cluster_mode"])
        self.assertEqual(out["ranking"], ranking)

    def test_cluster_book_supplements_holding_scores(self):
        paper = {
            "holdings": [
                {"stock_code": "000063", "stock_name": "中兴", "shares": 100, "cost": 30},
                {"stock_code": "601138", "stock_name": "富联", "shares": 100, "cost": 50},
            ],
            "rules": {"max_positions": 5},
            "strategy_id": "short",
        }
        ranking = [{"stock_code": "688303", "score": 2.0}]
        scored_all = [
            {"stock_code": "688303", "score": 2.0},
            {
                "stock_code": "601138",
                "score": None,
                "hard_reject": True,
                "reject_reason": "近3日涨幅过大",
            },
        ]
        with patch(
            "core.paper.rebalance.orchestrator.try_reuse_active_cluster_book",
            return_value=None,
        ), patch(
            "core.signal.cluster.rank.rank_cluster_pools",
            return_value={
                "success": True,
                "book": ranking,
                "scored_all": scored_all,
            },
        ), patch(
            "core.signal.cluster.live.assess_cluster_live_health",
            return_value={"alerts": []},
        ), patch(
            "core.paper.rebalance.orchestrator._supplement_holding_scores",
            side_effect=lambda paper, rows, **kw: list(rows)
            + [
                {
                    "stock_code": "000063",
                    "stock_name": "中兴",
                    "score": 1.2,
                    "holding_supplement": True,
                }
            ],
        ), patch(
            "core.paper.rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ), patch(
            "core.signal.score_display.selection_min_score",
            return_value=-0.01,
        ):
            out = run_paper_rebalance(paper, mode="cluster_book", dry_run=True)
        lookup = mock_sim.call_args[1]["score_lookup"]
        by = {str(r["stock_code"]): r for r in lookup}
        self.assertIn("000063", by)
        self.assertEqual(by["000063"].get("score"), 1.2)
        self.assertTrue(by["601138"].get("hard_reject"))
        self.assertEqual(out["mode"], "cluster_book")

    def test_confirm_reuses_fresh_active_book(self):
        paper = {
            "holdings": [],
            "rules": {"max_positions": 5},
            "strategy_id": "short",
        }
        book = [{"stock_code": "600519", "score": 2.5, "stock_name": "茅台"}]
        cached = {
            "success": True,
            "from_cache": True,
            "book": book,
            "ranking": book,
            "scored_all": book,
            "health": {"alerts": []},
        }
        with patch(
            "core.paper.rebalance.orchestrator.try_reuse_active_cluster_book",
            return_value=cached,
        ), patch(
            "core.signal.cluster.rank.rank_cluster_pools",
        ) as mock_rank, patch(
            "core.signal.cluster.live.assess_cluster_live_health",
            return_value={"alerts": []},
        ), patch(
            "core.paper.rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ), patch(
            "core.signal.score_display.selection_min_score",
            return_value=-0.01,
        ):
            out = run_paper_rebalance(paper, mode="cluster_book", dry_run=False)
        mock_rank.assert_not_called()
        self.assertTrue(out.get("book_reused"))
        self.assertEqual(out["ranking"], book)
        self.assertTrue(mock_sim.call_args.kwargs.get("skip_sentiment_prior"))

    def test_preview_also_reuses_fresh_active_book(self):
        paper = {
            "holdings": [],
            "rules": {"max_positions": 5},
            "strategy_id": "short",
        }
        book = [{"stock_code": "600519", "score": 2.5}]
        cached = {
            "success": True,
            "from_cache": True,
            "book": book,
            "ranking": book,
            "scored_all": book,
            "health": {"alerts": []},
        }
        with patch(
            "core.paper.rebalance.orchestrator.try_reuse_active_cluster_book",
            return_value=cached,
        ), patch(
            "core.signal.cluster.rank.rank_cluster_pools",
        ) as mock_rank, patch(
            "core.signal.cluster.live.assess_cluster_live_health",
            return_value={"alerts": []},
        ), patch(
            "core.paper.rebalance.simulate_cross_section_rebalance",
            return_value={"sell_trades": [], "buy_trades": []},
        ) as mock_sim, patch(
            "core.strategy.apply_strategy_to_paper",
        ), patch(
            "core.signal.score_display.selection_min_score",
            return_value=-0.01,
        ):
            out = run_paper_rebalance(paper, mode="cluster_book", dry_run=True)
        mock_rank.assert_not_called()
        self.assertTrue(out.get("book_reused"))
        # 预演复用簿时仍可跑舆情；确认落账才 skip
        self.assertFalse(mock_sim.call_args.kwargs.get("skip_sentiment_prior"))

    def test_prepare_reuses_book_for_preview_and_confirm(self):
        from core.paper.rebalance.orchestrator import prepare_cluster_book_rank

        book = [{"stock_code": "000001", "score": 1.0}]
        with patch(
            "core.paper.rebalance.orchestrator.try_reuse_active_cluster_book",
            return_value={
                "success": True,
                "from_cache": True,
                "book": book,
                "ranking": book,
                "scored_all": book,
            },
        ), patch(
            "core.signal.cluster.live.assess_cluster_live_health",
            return_value={"alerts": []},
        ), patch(
            "core.signal.cluster.rank.rank_cluster_pools",
        ) as mock_rank:
            out_prev = prepare_cluster_book_rank({"rules": {}}, dry_run=True)
            out_cfm = prepare_cluster_book_rank({"rules": {}}, dry_run=False)
        mock_rank.assert_not_called()
        self.assertTrue(out_prev.get("from_cache"))
        self.assertTrue(out_cfm.get("from_cache"))
        self.assertEqual(out_prev["book"], book)


class TestSupplementHoldingScores(unittest.TestCase):
    def test_noop_when_all_present(self):
        from core.paper.rebalance.orchestrator import _supplement_holding_scores

        rows = [{"stock_code": "000001", "score": 1.0}]
        paper = {"holdings": [{"stock_code": "000001", "shares": 100}]}
        out = _supplement_holding_scores(paper, rows)
        self.assertEqual(len(out), 1)

    def test_scores_missing_holding(self):
        from core.paper.rebalance.orchestrator import _supplement_holding_scores

        paper = {
            "holdings": [
                {"stock_code": "000063", "stock_name": "中兴", "shares": 100},
            ]
        }
        with patch(
            "core.signal.score_stock.score_stock",
            return_value={
                "success": True,
                "signal_item": {
                    "stock_code": "000063",
                    "stock_name": "中兴通讯",
                    "score": 1.48,
                    "predicted_score": 1.48,
                },
            },
        ):
            out = _supplement_holding_scores(paper, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["stock_code"], "000063")
        self.assertEqual(out[0]["score"], 1.48)
        self.assertTrue(out[0].get("holding_supplement"))


class TestClusterBookShockInvalidate(unittest.TestCase):
    def _mock_quotes_service(self, quotes=None, *, side_effect=None):
        svc = MagicMock()
        if side_effect is not None:
            svc.batch_get_quotes.side_effect = side_effect
        else:
            svc.batch_get_quotes.return_value = quotes or {}
        return patch(
            "core.data.service.get_default_service",
            return_value=svc,
        )

    def test_shock_reason_detects_wide_moves(self):
        from core.paper.rebalance.orchestrator import _cluster_book_market_shock_reason

        book = [{"stock_code": f"00000{i}"} for i in range(4)]
        quotes = {
            "000000": {"success": True, "change_raw": 6.0},
            "000001": {"success": True, "change_raw": -7.0},
            "000002": {"success": True, "change_raw": 0.5},
            "000003": {"success": True, "change_raw": 1.0},
        }
        with self._mock_quotes_service(quotes):
            reason = _cluster_book_market_shock_reason(book, shock_frac=0.4)
        self.assertIsNotNone(reason)
        self.assertIn("book_shock", str(reason))

    def test_shock_reason_fail_closed_on_quote_exception(self):
        from core.paper.rebalance.orchestrator import _cluster_book_market_shock_reason

        book = [{"stock_code": "600519"}]
        with self._mock_quotes_service(side_effect=RuntimeError("quote down")):
            reason = _cluster_book_market_shock_reason(book)
        self.assertEqual(reason, "quote_check_failed:exception")

    def test_shock_reason_fail_closed_on_empty_quotes(self):
        from core.paper.rebalance.orchestrator import _cluster_book_market_shock_reason

        book = [{"stock_code": "600519"}]
        with self._mock_quotes_service({}):
            reason = _cluster_book_market_shock_reason(book)
        self.assertEqual(reason, "quote_check_failed:empty")

    def test_reuse_skips_on_shock(self):
        from datetime import datetime, timezone

        from core.paper.rebalance.orchestrator import try_reuse_active_cluster_book

        doc = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "book": [{"stock_code": "600519", "score": 1.0}],
            "meta": {"version": "v1"},
            "scored_all": [],
        }
        quotes = {"600519": {"success": True, "change_raw": 9.8}}
        with patch(
            "core.signal.cluster.live.load_active_cluster_book", return_value=doc
        ), patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value={"version": "v1"},
        ), self._mock_quotes_service(quotes):
            out = try_reuse_active_cluster_book()
        self.assertIsNone(out)

    def test_reuse_skips_when_quotes_fail(self):
        from datetime import datetime, timezone

        from core.paper.rebalance.orchestrator import try_reuse_active_cluster_book

        doc = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "book": [{"stock_code": "600519", "score": 1.0}],
            "meta": {"version": "v1"},
            "scored_all": [],
        }
        with patch(
            "core.signal.cluster.live.load_active_cluster_book", return_value=doc
        ), patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value={"version": "v1"},
        ), self._mock_quotes_service(side_effect=OSError("network")):
            out = try_reuse_active_cluster_book()
        self.assertIsNone(out)

    def test_reuse_ok_when_quiet(self):
        from datetime import datetime, timezone

        from core.paper.rebalance.orchestrator import try_reuse_active_cluster_book

        book = [{"stock_code": "600519", "score": 1.0}]
        doc = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "book": book,
            "meta": {"version": "v1", "max_names": 1},
            "scored_all": book,
        }
        quotes = {"600519": {"success": True, "change_raw": 0.4}}
        with patch(
            "core.signal.cluster.live.load_active_cluster_book", return_value=doc
        ), patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value={"version": "v1"},
        ), self._mock_quotes_service(quotes):
            out = try_reuse_active_cluster_book()
        self.assertIsNotNone(out)
        self.assertTrue(out.get("from_cache"))


if __name__ == "__main__":
    unittest.main()
