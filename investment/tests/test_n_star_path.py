"""N1–N5 北极星路径骨架单测（离线）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP1QualityGate(unittest.TestCase):
    def test_allows_production_score_rules(self):
        from core.data.facade import allows_production_score

        self.assertTrue(allows_production_score(quality_level="good", fallback=False)[0])
        self.assertFalse(allows_production_score(quality_level="thin", fallback=False)[0])
        self.assertFalse(allows_production_score(quality_level="empty", fallback=False)[0])
        self.assertFalse(allows_production_score(quality_level="good", fallback=True)[0])

    def test_score_stock_gates_fallback(self):
        from core.signal.score_stock import score_stock

        quote = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "price": 100,
            "change": 1.0,
            "change_percent": 1.0,
            "volume": 1,
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars",
            return_value=([], "empty"),
        ), patch(
            "core.signal.score_stock.bars_from_quote_fallback",
            return_value=[
                {"date": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            ],
        ), patch("core.signal.score_stock.score_bars") as mock_score:
            out = score_stock("600519", quote=quote, skip_fundamentals=True)
        mock_score.assert_not_called()
        self.assertTrue(out["success"])
        self.assertTrue(out["quality_gate"])
        item = out["signal_item"]
        self.assertTrue(item["hard_reject"])
        self.assertIn("gate", (item.get("gate_reason") or ""))
        self.assertIsNone(item.get("score"))

    def test_score_stock_passes_good_bars(self):
        from core.signal.score_stock import score_stock
        from datetime import datetime, timedelta

        quote = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "price": 100,
            "change": 1.0,
        }
        today = datetime.now().date()
        bars = [
            {
                "date": (today - timedelta(days=30 - i)).isoformat(),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.0 + i * 0.01,
                "volume": 100,
            }
            for i in range(1, 25)
        ]
        with patch(
            "core.signal.score_stock.fetch_daily_bars",
            return_value=(bars, "akshare_cn_daily"),
        ), patch(
            "core.signal.score_stock.score_bars",
            return_value={"score": 60, "hard_reject": False, "reject_reason": None},
        ), patch(
            "core.signal.score_stock.load_signal_config",
            return_value={"fundamentals": {"enabled": False}},
        ), patch(
            "core.signal.cluster.live.lookup_code_return_model",
            return_value=None,
        ), patch(
            "core.signal.return_score_store.load_return_model",
            return_value=(None, {}),
        ):
            out = score_stock("600519", quote=quote, skip_fundamentals=True)
        self.assertFalse(out.get("quality_gate"))
        # 无 return_model 时主分 score 为 None；启发式分仍落 heuristic_score
        self.assertIsNone(out["signal_item"]["score"])
        self.assertEqual(out["signal_item"]["heuristic_score"], 60.0)

    def test_manifest_has_adjust_policy(self):
        from core.run_manifest import build_run_manifest
        from core.data.facade import DEFAULT_ADJUST_POLICY

        m = build_run_manifest(kind="test", cost_model="simple_cn")
        self.assertEqual(m["adjust_policy"], DEFAULT_ADJUST_POLICY)

    def test_oos_failed_when_underperform(self):
        from core.backtest.oos_report import split_oos_summary

        # IS rising, OOS collapsing → failed
        curve = [{"date": f"d{i}", "equity": 100 + i} for i in range(14)]
        curve += [{"date": f"d{i}", "equity": 120 - (i - 13) * 5} for i in range(14, 20)]
        out = split_oos_summary(curve)
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("failed"))
        self.assertIsNotNone(out.get("oos_max_drawdown_pct"))
        self.assertGreater(out["oos_max_drawdown_pct"], 0)

    def test_ops_report_includes_target_weights(self):
        from core.paper import build_ops_report

        ops = build_ops_report(
            strategy_id="short",
            optimize={
                "ok": True,
                "weights_pct": {"600519": 25.0, "600036": 20.0},
                "total_pct": 45.0,
                "count": 2,
                "limits": {"max_position_pct": 25.0, "max_sector_pct": 40.0, "max_positions": 5},
                "skipped": [],
            },
        )
        self.assertEqual(ops["target_weights"]["600519"], 25.0)
        self.assertEqual(ops["risk_limits"]["max_position_pct"], 25.0)
        self.assertEqual(ops["optimize"]["count"], 2)

    def test_strategy_specs_expose_risk(self):
        from core.strategy import list_strategy_specs

        specs = list_strategy_specs()
        self.assertTrue(specs)
        r = specs[0].get("risk") or {}
        self.assertIn("max_position_pct", r)
        self.assertIn("max_sector_pct", r)

    def test_cost_compare_helper_gap(self):
        from quant.services.quant_service_replay import _metrics_slice

        sliced = _metrics_slice(
            {"metrics": {"total_return_pct": 10.0, "max_drawdown_pct": 3.0}, "trade_count": 4}
        )
        self.assertEqual(sliced["total_return_pct"], 10.0)
        self.assertEqual(sliced["trade_count"], 4)

    def test_rolling_walk_forward_slices(self):
        from research.split import rolling_walk_forward_slices

        folds = rolling_walk_forward_slices(120, n_splits=3, min_train=40, min_test=12)
        self.assertEqual(len(folds), 3)
        for i, f in enumerate(folds):
            self.assertEqual(f.fold, i + 1)
            self.assertLess(f.train_end, f.test_end)
            self.assertEqual(f.train_end, f.test_start)
            if i:
                self.assertEqual(f.test_start, folds[i - 1].test_end)
        self.assertEqual(folds[-1].test_end, 120)

    def test_portfolio_wf_slices_smoke(self):
        from quant.research.wf_slices import run_portfolio_wf_slices

        def _bars(code, n=90, start=10.0):
            out = []
            p = start
            for i in range(n):
                p *= 1.002
                out.append(
                    {
                        "date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                        "open": p,
                        "high": p * 1.01,
                        "low": p * 0.99,
                        "close": p,
                        "volume": 1000,
                    }
                )
            return out

        stock_bars = {
            "AAA": _bars("AAA", 90, 10),
            "BBB": _bars("BBB", 90, 20),
            "CCC": _bars("CCC", 90, 15),
        }
        with patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            side_effect=lambda bars, **kw: {
                "success": True,
                "metrics": {
                    "total_return_pct": 5.0,
                    "max_drawdown_pct": 2.0,
                    "trade_count": 3,
                },
                "equity_curve": [
                    {"date": b["date"], "equity": 100 + i}
                    for i, b in enumerate(list(bars.values())[0])
                ],
            },
        ):
            out = run_portfolio_wf_slices(stock_bars, n_splits=3, min_train=30, min_test=10)
        self.assertTrue(out.get("folds"))
        self.assertGreaterEqual(len(out["folds"]), 1)


class TestN1DataService(unittest.TestCase):
    def test_get_bars_shape(self):
        from core.data.facade import get_bars

        fake_bars = [
            {"date": "2026-01-0%d" % i, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1}
            for i in range(1, 9)
        ]
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(fake_bars, "akshare_cn_daily"),
        ):
            out = get_bars("600519", limit=30)
        self.assertEqual(out["data_source"], "akshare_cn_daily")
        self.assertFalse(out["fallback"])
        self.assertIn(out["quality"]["level"], {"good", "thin", "empty"})
        self.assertEqual(out["adjust_policy"], "qfq")
        self.assertIn("production_ok", out)
        self.assertEqual(len(out["bars"]), 8)

    def test_manifest_data_quality_key(self):
        from core.run_manifest import build_run_manifest

        m = build_run_manifest(
            kind="test",
            cost_model="simple_cn",
            data_quality={"levels": {"good": 1}, "fallback_count": 0},
        )
        self.assertIn("data_quality", m)
        self.assertEqual(m["data_quality"]["fallback_count"], 0)


class TestN2FactorAndMl(unittest.TestCase):
    def test_alt_sentiment_registered_and_skippable(self):
        from core.signal.factors.meta.registry import compute_configured_factors, registered_factor_names

        self.assertIn("alt_sentiment", registered_factor_names())
        bars = [
            {"date": "2026-01-%02d" % i, "open": 1, "high": 1, "low": 1, "close": 1.0 + i * 0.01, "volume": 100}
            for i in range(1, 25)
        ]
        subs, contribs, _ = compute_configured_factors(
            bars,
            weights={"momentum": 1.0, "alt_sentiment": 0.0},
        )
        self.assertIn("momentum", subs)
        self.assertNotIn("alt_sentiment", subs)

    def test_ml_artifact_never_writes_score(self):
        from research.ml.write_artifact import build_artifact, write_artifact

        art = build_artifact(note="unit")
        self.assertFalse(art["constraints"]["writes_production_score"])
        with tempfile.TemporaryDirectory() as td:
            path = write_artifact(os.path.join(td, "artifact.json"), art)
            self.assertTrue(os.path.isfile(path))


class TestN3PortfolioRisk(unittest.TestCase):
    def test_optimize_weights_respects_sector_cap(self):
        from core.portfolio_optimize import optimize_weights

        cands = [
            {"stock_code": "AAA", "score": 90, "sector": "X"},
            {"stock_code": "BBB", "score": 88, "sector": "X"},
            {"stock_code": "CCC", "score": 80, "sector": "Y"},
        ]
        out = optimize_weights(
            cands,
            max_position_pct=2.0,
            max_sector_pct=3.0,
            max_positions=5,
            min_score=50,
            sector_map={},
            weight_mode="greedy_cap",
            apply_market_vol=False,
        )
        self.assertTrue(out["ok"])
        self.assertLessEqual(out["sector_exposure_pct"].get("X", 0), 3.0 + 1e-6)
        self.assertIn("CCC", out["weights_pct"])

    def test_score_budget_and_vol_scale(self):
        from core.portfolio_optimize import optimize_weights
        from core.risk.budget import score_budget_weights

        ranked = [
            {"stock_code": "A", "score": 90, "sector": "X"},
            {"stock_code": "B", "score": 60, "sector": "Y"},
        ]
        w, sec, _ = score_budget_weights(
            ranked, max_position_pct=70.0, max_sector_pct=80.0, max_positions=5
        )
        self.assertGreater(w["A"], w["B"])
        self.assertLessEqual(w["A"], 70.0 + 1e-6)

        out = optimize_weights(
            [
                {"stock_code": "A", "score": 90, "sector": "X"},
                {"stock_code": "B", "score": 60, "sector": "Y"},
            ],
            max_position_pct=20.0,
            max_sector_pct=40.0,
            max_positions=5,
            min_score=50,
            sector_map={},
            weight_mode="score_budget",
            vol_scale=0.8,
            apply_market_vol=False,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["weight_mode"], "score_budget")
        self.assertAlmostEqual(out["limits"]["effective_max_position_pct"], 16.0, places=3)
        self.assertTrue(out["vol_scale"].get("high_vol"))
        self.assertLessEqual(out["weights_pct"].get("A", 0), 16.0 + 1e-6)

    def test_sector_risk_block(self):
        from core.risk.checks import check_account_risk

        paper = {"cash": 0, "strategy_id": "short", "holdings": []}
        summary = {
            "equity": 100000,
            "max_drawdown_pct": 1.0,
            "holdings": [
                {"stock_code": "300750", "shares": 100, "market_value": 30000, "sector": "新能源"},
                {"stock_code": "002594", "shares": 100, "market_value": 25000, "sector": "新能源"},
            ],
        }
        risk = {
            "max_drawdown_pct": 50,
            "max_position_pct": 40,
            "max_sector_pct": 40,
            "max_positions": 10,
        }
        # 55% sector → block
        out = check_account_risk(paper, summary, risk=risk)
        self.assertFalse(out["ok"])
        self.assertTrue(any("行业" in b for b in out["blocks"]))


class TestN4OosReport(unittest.TestCase):
    def test_attach_robustness(self):
        from core.backtest.oos_report import attach_robustness_fields

        curve = [{"date": f"d{i}", "equity": 100 + i} for i in range(20)]
        bars = [
            {"date": f"2026-01-{i:02d}", "close": 10 + (i % 3) * 0.2}
            for i in range(1, 30)
        ]
        out = attach_robustness_fields(
            {"success": True, "equity_curve": curve, "params": {"apply_costs": True}},
            cost_model="simple_cn",
            sample_bars=bars,
        )
        self.assertEqual(out["cost_model"], "simple_cn")
        self.assertTrue(out["oos_summary"]["ok"])
        self.assertIn("regime", out["regime_summary"])

    def test_portfolio_request_defaults_apply_costs(self):
        from web.schemas import PortfolioBacktestRequest

        body = PortfolioBacktestRequest()
        self.assertTrue(body.apply_costs)
        self.assertTrue(body.include_wf_slices)
        self.assertEqual(body.wf_n_splits, 3)


class TestN5Monitor(unittest.TestCase):
    def test_health_warn_on_target_dd(self):
        from core.strategy_monitor import assess_strategy_health

        out = assess_strategy_health(
            {"strategy_id": "short"},
            summary={"max_drawdown_pct": 15.0, "equity": 100000, "cash": 10000},
            risk={"max_drawdown_pct": 20.0, "target_drawdown_pct": 12.0},
        )
        self.assertEqual(out["level"], "warn")
        self.assertTrue(out["suggestions"])

    def test_ic_decay_and_sector_coverage(self):
        from core.strategy_monitor import (
            assess_strategy_health,
            estimate_composite_ic,
            pearson_ic,
            sector_coverage_report,
        )

        self.assertEqual(pearson_ic([1, 2, 3], [1, 2, 3]), 1.0)
        cov = sector_coverage_report(["600519", "999999"])
        self.assertEqual(cov["mapped"], 1)
        self.assertEqual(cov["heuristic"], 1)
        self.assertLess(cov["coverage"], 0.6)

        out = assess_strategy_health(
            {
                "strategy_id": "short",
                "cash": 10000,
                "holdings": [{"stock_code": "999991"}, {"stock_code": "999992"}],
            },
            summary={"max_drawdown_pct": 1.0, "equity": 100000},
            rolling_ic=0.005,
            codes=["999991", "999992"],
        )
        codes = {a["code"] for a in out["alerts"]}
        self.assertIn("ic_decay", codes)
        self.assertIn("sector_map_thin", codes)
        self.assertEqual(out["metrics"]["rolling_ic"], 0.005)

        # 合成 IC：单调上涨 + score 应有样本（不强制正 IC）
        bars = [
            {
                "date": f"2026-01-{i:02d}",
                "open": 10 + i * 0.1,
                "high": 11 + i * 0.1,
                "low": 9 + i * 0.1,
                "close": 10 + i * 0.1,
                "volume": 1000 + i * 10,
            }
            for i in range(1, 50)
        ]
        ic_out = estimate_composite_ic(bars, horizon_days=3, min_history=20, max_points=20)
        self.assertIn("sample_count", ic_out)


class TestP0OpsReportAndRiskBlock(unittest.TestCase):
    def test_build_ops_report_five_keys(self):
        from core.paper import build_ops_report

        ops = build_ops_report(
            strategy_id="short",
            strategy_version="1",
            cost_model="simple_cn",
            data_quality={"fallback_count": 2, "count": 5},
            risk_blocks=["行业超限"],
            monitor_alerts=[{"level": "warn", "message": "回撤预警"}],
            buys_blocked=True,
        )
        for key in (
            "strategy_id",
            "strategy_version",
            "cost_model",
            "data_quality",
            "risk_blocks",
            "monitor_alerts",
            "fallback_count",
        ):
            self.assertIn(key, ops)
        self.assertEqual(ops["fallback_count"], 2)
        self.assertTrue(ops["buys_blocked"])

    def test_daily_cycle_blocks_buys_and_logs(self):
        from core.paper import run_daily_cycle

        paper = {
            "cash": 10000,
            "strategy_id": "short",
            "strategy_version": "t",
            "cost_model": "simple_cn",
            "holdings": [
                {
                    "stock_code": "300750",
                    "stock_name": "宁德",
                    "shares": 100,
                    "cost": 100,
                    "sector": "新能源",
                },
                {
                    "stock_code": "002594",
                    "stock_name": "比亚迪",
                    "shares": 100,
                    "cost": 100,
                    "sector": "新能源",
                },
            ],
            "rules": {"min_score": 55, "add_score": 60, "reduce_score": 50, "min_hold_score": 45},
            "operation_log": [],
            "trades": [],
            "snapshots": [],
        }
        risk = {
            "max_drawdown_pct": 50,
            "max_position_pct": 40,
            "max_sector_pct": 40,
            "max_positions": 10,
        }
        summary = {
            "equity": 100000,
            "cash": 10000,
            "max_drawdown_pct": 1.0,
            "position_count": 2,
            "holdings": [
                {"stock_code": "300750", "shares": 100, "market_value": 30000, "sector": "新能源"},
                {"stock_code": "002594", "shares": 100, "market_value": 25000, "sector": "新能源"},
            ],
        }

        with patch("core.paper.run_signal_scan", return_value=[]), patch(
            "core.paper.simulate_sells", return_value=[]
        ), patch("core.paper.simulate_buys") as mock_buys, patch(
            "core.paper.mark_to_market", return_value=summary
        ), patch("core.paper.append_snapshot"), patch(
            "core.risk.check_account_risk",
            return_value={"ok": False, "blocks": ["行业 新能源 敞口 55.0% > 上限 40%"], "warnings": [], "limits": risk},
        ), patch(
            "core.data.facade.summarize_data_quality",
            return_value={"levels": {"good": 0}, "fallback_count": 1, "count": 2},
        ), patch(
            "core.strategy_monitor.assess_strategy_health",
            return_value={"ok": True, "level": "ok", "alerts": []},
        ), patch(
            "core.run_manifest.write_run_manifest", return_value="/tmp/m.json"
        ):
            out = run_daily_cycle(paper, simulate_buy=True, strategy="short")

        mock_buys.assert_not_called()
        self.assertTrue(out["buys_blocked"])
        self.assertTrue(out["risk_blocks"])
        self.assertIn("ops_report", out)
        self.assertEqual(out["ops_report"]["cost_model"], "simple_cn")
        self.assertIn("data_quality", out)
        self.assertEqual(out["data_quality"].get("fallback_count"), 1)
        types = [e.get("type") for e in paper.get("operation_log") or []]
        self.assertIn("risk_block", types)

    def test_cross_section_soft_sector_budget(self):
        """行业超限不整批 buys_blocked，走 risk_budget 缩量。"""
        from core.paper.rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 50000,
            "strategy_id": "short",
            "cost_model": "simple_cn",
            "holdings": [
                {
                    "stock_code": "300750",
                    "stock_name": "宁德",
                    "shares": 100,
                    "cost": 200,
                }
            ],
            "rules": {"max_positions": 5, "position_pct": 0.2},
            "operation_log": [],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "300750",
                "score": 2.0,
                "stock_name": "宁德",
                "predicted_score_tau": 1.0,
            },
            {
                "stock_code": "600519",
                "score": 3.0,
                "stock_name": "茅台",
                "predicted_score_tau": 1.0,
            },
        ]
        risk_out = {
            "ok": False,
            "blocks": ["行业 新能源 敞口 55.0% > 上限 40%"],
            "block_items": [
                {
                    "code": "max_sector",
                    "message": "行业 新能源 敞口 55.0% > 上限 40%",
                    "sector": "新能源",
                    "value": 55.0,
                    "limit": 40.0,
                }
            ],
            "warnings": [],
            "limits": {"max_position_pct": 25.0, "max_sector_pct": 40.0},
        }
        with patch(
            "core.ports.market.query_quote",
            return_value={"success": True, "stock_name": "茅台", "price": 100},
        ), patch(
            "core.paper.rebalance._quote_price", return_value=100.0
        ), patch(
            "core.paper.mark_to_market",
            return_value={
                "equity": 100000,
                "cash": 50000,
                "max_drawdown_pct": 1,
                "holdings": [
                    {"stock_code": "300750", "shares": 100, "market_value": 55000, "sector": "新能源"}
                ],
            },
        ), patch("core.risk.check_account_risk", return_value=risk_out), patch(
            "core.data.facade.summarize_data_quality",
            return_value={"levels": {}, "fallback_count": 0, "count": 1},
        ):
            out = simulate_cross_section_rebalance(paper, ranking, top_k=2)

        self.assertFalse(out["buys_blocked"])
        self.assertIn("ops_report", out)
        self.assertIn("data_quality", out)
        types = [e.get("type") for e in paper.get("operation_log") or []]
        self.assertIn("risk_budget", types)


class TestP2PaperOpsLoop(unittest.TestCase):
    def test_feedback_from_monitor_alerts(self):
        from core.feedback_suggest import suggest_config_feedback

        out = suggest_config_feedback(
            monitor_alerts=[
                {
                    "level": "warn",
                    "code": "drawdown_target",
                    "message": "回撤 12% 触及目标预警",
                }
            ]
        )
        self.assertTrue(out.get("ok"))
        self.assertTrue(any("监控告警" in r for r in out.get("reasons") or []))
        self.assertIn("patch", out)
        self.assertFalse(out.get("auto_apply"))

    def test_cross_section_ops_report_carries_monitor_alerts(self):
        from core.paper.rebalance import simulate_cross_section_rebalance

        paper = {
            "cash": 50000,
            "strategy_id": "short",
            "cost_model": "simple_cn",
            "holdings": [],
            "rules": {"max_positions": 5, "min_score": 50, "min_hold_score": 40, "position_pct": 0.2},
            "operation_log": [],
            "trades": [],
        }
        ranking = [
            {
                "stock_code": "600519",
                "score": 90,
                "stock_name": "茅台",
                "predicted_score_tau": 1.0,
            }
        ]
        alerts = [
            {"level": "warn", "code": "drawdown_target", "message": "回撤触及预警"}
        ]
        with patch(
            "core.ports.market.query_quote",
            return_value={"success": True, "stock_name": "茅台", "price": 100},
        ), patch(
            "core.paper.rebalance._quote_price", return_value=100.0
        ), patch(
            "core.paper.mark_to_market",
            return_value={
                "equity": 100000,
                "cash": 50000,
                "max_drawdown_pct": 12.0,
                "holdings": [],
            },
        ), patch(
            "core.risk.check_account_risk",
            return_value={"ok": True, "blocks": [], "warnings": [], "limits": {}},
        ), patch(
            "core.data.facade.summarize_data_quality",
            return_value={"levels": {}, "fallback_count": 0, "count": 1},
        ), patch(
            "core.strategy_monitor.assess_strategy_health",
            return_value={"ok": True, "level": "warn", "alerts": alerts},
        ):
            out = simulate_cross_section_rebalance(paper, ranking, top_k=1)

        ops = out.get("ops_report") or {}
        self.assertEqual(ops.get("monitor_alerts"), alerts)

    def test_run_schedule_paper_daily_kind(self):
        from core.schedule_jobs import run_schedule

        fake = {
            "ok": True,
            "kind": "paper_daily",
            "monitor_alerts": [],
            "strategy_id": "short",
            "cost_model": "simple_cn",
            "data_quality": {},
            "risk_blocks": [],
        }
        with patch("core.schedule_jobs.run_paper_daily", return_value=fake) as mocked:
            out = run_schedule("paper_daily", simulate_buy=False, strategy="short")
        mocked.assert_called_once()
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("kind"), "paper_daily")


if __name__ == "__main__":
    unittest.main()
