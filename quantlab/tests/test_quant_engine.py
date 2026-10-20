"""QuantEngine 集成测试（P10-P14 + P45/P46/P68/P70/P77/P86/P94）。"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from core.backtest.legacy.topk_backtest import backtest_topk_equal_weight
from core.paper.rebalance import compute_turnover_stats
from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config
from core.signal.factors.liquidity import score_liquidity
from core.signal.factors.meta.registry import (
    compute_configured_factors,
    list_factors,
    run_factor_experiment,
)
from core.signal.factors.quality import score_quality
from core.signal.factors.reversal import score_reversal
from core.signal.factors.value import score_value
from core.signal.fundamentals_bridge import normalize_fundamentals_metrics
from core.signal.scorer import score_bars
from core.signal.threshold_suggest import (
    format_threshold_config_diff,
    suggest_stance_thresholds_from_oos,
    suggest_stance_thresholds_from_watching_oos,
)
from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic
from evals.mock_context import apply_case_mocks, rising_bars
from quant.research.factor_ols import compute_factor_ols_report
from quant.research.portfolio_data import summarize_portfolio_backtest
from quant.services.quant_interpret import build_rule_based_interpret, compact_quant_report, interpret_quant_report
from quant.services.quant_report_export import (
    export_quant_report_markdown,
    render_quant_report_markdown,
    summarize_cross_section_scores,
)
from quant.skill.engine import QuantEngine
from tests.conftest import ROOT, _aligned_bars, _rising_bars


# --- from test_p10_quant.py ---
class TestPortfolioCrossSection(unittest.TestCase):
    def test_cross_section_portfolio_backtest(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["trade_count"], 1)
        self.assertIn("metrics", result)
        self.assertEqual(result["params"]["top_k"], 2)
        self.assertIn(result["strategy"], ("cross_section_topk", "cross_section_topk_neutral"))
        self.assertGreaterEqual(len(result.get("equity_curve") or []), 2)

    def test_insufficient_common_dates(self):
        a = _rising_bars()[:10]
        b = _rising_bars()
        b[0]["date"] = "2099-01-01"
        result = backtest_topk_equal_weight(
            {"600519": a, "600036": b},
            min_history=8,
        )
        self.assertFalse(result["success"])

    def test_drops_thin_series_instead_of_collapsing_calendar(self):
        stock_bars = {
            "600519": _aligned_bars("a", n=40),
            "600036": _aligned_bars("b", n=40, step=0.35),
            "300750": _aligned_bars("c", n=40, step=0.45),
            "688825": _aligned_bars("thin", n=3),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=0,
            min_history=12,
            neutralize=False,
        )
        self.assertTrue(result["success"], result.get("error"))
        dropped = {d.get("stock_code") for d in (result.get("dropped_stocks") or [])}
        self.assertIn("688825", dropped)
        self.assertGreaterEqual(result.get("params", {}).get("common_dates") or 0, 16)

class TestWeightSuggest(unittest.TestCase):
    def test_suggest_from_positive_ic(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.12, "sample_count": 20},
                {"factor": "volume_price", "ic": -0.08, "sample_count": 20},
                {"factor": "score", "ic": 0.05, "sample_count": 20},
            ],
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "relative_strength": 0.2,
                "volatility": 0.1,
            },
            ic_mode="single",
        )
        self.assertTrue(out["success"])
        self.assertGreater(out["suggested_weights"]["momentum"], out["current_weights"]["momentum"])
        self.assertLess(out["suggested_weights"]["volume_price"], out["current_weights"]["volume_price"])
        self.assertAlmostEqual(sum(out["suggested_weights"].values()), 1.0, places=2)

    def test_weak_ic_uses_ols_then_decay(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.01, "sample_count": 20},
                {"factor": "volume_price", "ic": 0.005, "sample_count": 20},
                {"factor": "liquidity", "ic": None, "sample_count": 20},
            ],
        }
        ols = {
            "success": True,
            "coefficients": {"momentum": 0.25, "liquidity": -0.18},
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "liquidity": 0.2,
                "volatility": 0.1,
            },
            ols_report=ols,
            weak_ic_decay=0.015,
            ols_delta=0.02,
            ic_mode="single",
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["delta_sources"]["momentum"], "ols")
        self.assertEqual(out["deltas"]["momentum"], 0.02)
        self.assertEqual(out["delta_sources"]["volume_price"], "weak_ic_decay")
        self.assertEqual(out["deltas"]["volume_price"], -0.015)
        self.assertEqual(out["delta_sources"]["liquidity"], "ols")
        self.assertEqual(out["deltas"]["liquidity"], -0.02)
        self.assertNotIn("volatility", out["delta_sources"])

    def test_cs_ic_requires_icir_and_scales(self):
        exp = {
            "success": True,
            "factors": [
                {"factor": "momentum", "ic": 0.08, "icir": 0.6, "sample_count": 40},
                {"factor": "volume_price", "ic": 0.08, "icir": 0.1, "sample_count": 40},
                {"factor": "liquidity", "ic": -0.05, "icir": -0.5, "sample_count": 40},
            ],
        }
        out = suggest_weights_from_ic(
            exp,
            current_weights={
                "momentum": 0.4,
                "volume_price": 0.3,
                "liquidity": 0.2,
                "volatility": 0.1,
            },
            ic_mode="cs_ic",
            max_delta=0.03,
            min_icir=0.25,
        )
        self.assertEqual(out["ic_mode"], "cs_ic")
        self.assertEqual(out["delta_sources"]["momentum"], "cs_ic")
        self.assertAlmostEqual(out["deltas"]["momentum"], 0.036, places=3)
        self.assertEqual(out["delta_sources"]["volume_price"], "weak_ic_decay")
        self.assertEqual(out["delta_sources"]["liquidity"], "cs_ic")
        self.assertLess(out["deltas"]["liquidity"], 0)

    def test_group_cap_and_freeze_zero(self):
        from core.signal.weight_suggest import apply_group_caps

        capped, warns = apply_group_caps(
            {
                "momentum": 0.35,
                "ma_slope": 0.2,
                "technical_pattern": 0.1,
                "weekly_confirm": 0.05,
                "value": 0.3,
            },
            {
                "trend": ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
                "value_quality": ["value"],
            },
            max_group_share=0.45,
        )
        self.assertTrue(warns)
        trend_sum = sum(
            capped[k]
            for k in ("momentum", "ma_slope", "technical_pattern", "weekly_confirm")
        )
        self.assertLessEqual(trend_sum, 0.451)
        out = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [
                    {"factor": "momentum", "ic": 0.2, "sample_count": 20},
                    {"factor": "money_flow", "ic": 0.2, "sample_count": 20},
                ],
            },
            current_weights={"momentum": 0.5, "money_flow": 0.0, "value": 0.5},
            ic_mode="single",
        )
        self.assertEqual(out["suggested_weights"]["money_flow"], 0.0)
        self.assertIn("money_flow", out["frozen_zero_factors"])


# --- from test_p11_quant.py ---
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
        self.assertNotIn("predicted_score_cal", row)
        self.assertNotIn("score_calibration_applied", row)
        self.assertIsNotNone(row.get("entry_price"))
        self.assertTrue(str(row.get("factor_weights_note") or ""))
        # next_open：意图价→开盘价应还原缺口，ŷ_oo_rem 不再等于裸 ŷ_oo
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

class TestTurnoverStats(unittest.TestCase):
    def test_two_way_turnover_and_over_limit(self):
        sell = [{"amount": 10000}]
        buy = [{"amount": 10000}, {"amount": 10000}]
        t = compute_turnover_stats(
            sell, buy, equity_before=100000.0, max_turnover_pct=10.0
        )
        self.assertEqual(t["turnover_pct"], 15.0)
        self.assertTrue(t["over_limit"])
        self.assertEqual(t["buy_amount"], 20000.0)
        self.assertEqual(t["sell_amount"], 10000.0)

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


# --- from test_p12_quant.py ---
class TestPortfolioDailySummary(unittest.TestCase):
    def test_summarize_offline(self):
        stock_bars = {"600519": _aligned_bars("a"), "600036": _aligned_bars("b")}
        fake = {
            "success": True,
            "params": {"engine": "paper_replay"},
            "metrics": {
                "total_return_pct": 1.0,
                "win_rate_pct": 50.0,
                "trade_count": 2,
                "max_drawdown_pct": 1.0,
            },
            "equity_curve": [{"date": "2026-01-20", "equity": 1_000_000}],
            "note": "引擎=paper_replay",
        }
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            return_value=fake,
        ):
            out = summarize_portfolio_backtest(codes=["600519", "600036"], min_score=40)
        self.assertTrue(out["success"])
        self.assertIn("total_return_pct", out)
        self.assertIn("equity_curve_tail", out)
        self.assertEqual(out.get("engine"), "paper_replay")

class TestWeightPreview(unittest.TestCase):
    def test_config_diff_for_preview(self):
        suggestion = suggest_weights_from_ic(
            {
                "success": True,
                "factors": [{"factor": "momentum", "ic": 0.09, "sample_count": 20}],
            }
        )
        diff = format_weight_config_diff(suggestion)
        self.assertTrue(diff.get("success"))
        self.assertIn("weights", diff.get("patch") or {})


# --- from test_p13_quant.py ---
class TestThresholdSuggest(unittest.TestCase):
    def test_suggest_from_oos_best_min_score(self):
        oos = {
            "success": True,
            "best_params": {"min_score": 60, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 5, "win_rate_pct": 52.0}},
            "split": {"train_end": 20, "valid_end": 30, "test_end": 40},
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": 45, "wait": 55, "probe": 68},
            max_delta=3.0,
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out.get("score_scale"), "heuristic")
        self.assertGreaterEqual(out["suggested_thresholds"]["wait"], 55)

    def test_predicted_scale_skips_heuristic_oos(self):
        oos = {
            "success": True,
            "score_scale": "heuristic_0_100",
            "best_params": {"min_score": 50, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 10, "win_rate_pct": 58.66}},
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": -0.5, "wait": 0.0, "probe": 0.3},
            max_delta=3.0,
        )
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped_apply"))
        self.assertEqual(out.get("score_scale"), "predicted")
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.0)
        self.assertEqual(out["suggested_thresholds"]["probe"], 0.3)
        self.assertEqual(out["deltas"]["wait"], 0.0)
        self.assertIn("ŷ%", (out.get("rationale") or [""])[0])
        diff = format_threshold_config_diff(out)
        self.assertTrue(diff["success"])
        self.assertTrue(diff.get("skipped_apply"))
        self.assertEqual(diff.get("patch"), {})
        self.assertEqual(diff.get("changes"), {})

    def test_yhat_oos_suggests_wait_step(self):
        oos = {
            "success": True,
            "score_scale": "predicted_yhat",
            "best_params": {"wait": 0.5, "min_score": 0.5, "horizon_days": 3},
            "test": {"metrics": {"trade_count": 8, "win_rate_pct": 55.0}},
            "model_source": "fit_train",
        }
        out = suggest_stance_thresholds_from_oos(
            oos,
            current_thresholds={"avoid": -0.5, "wait": 0.0, "probe": 0.3},
            max_delta=0.5,
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.5)
        self.assertGreater(out["deltas"]["wait"], 0)
        diff = format_threshold_config_diff(out)
        self.assertTrue(diff["success"])
        self.assertFalse(diff.get("skipped_apply"))
        self.assertIn("stance_thresholds", diff["patch"])

    def test_format_threshold_diff(self):
        suggestion = suggest_stance_thresholds_from_oos(
            {
                "success": True,
                "best_params": {"min_score": 62},
                "test": {"metrics": {"trade_count": 6, "win_rate_pct": 48}},
            },
            current_thresholds={"avoid": 45, "wait": 55, "probe": 68},
        )
        diff = format_threshold_config_diff(suggestion)
        self.assertTrue(diff["success"])
        self.assertIn("stance_thresholds", diff["patch"])

class TestQuantInterpret(unittest.TestCase):
    def test_compact_report(self):
        report = {
            "success": True,
            "weight_suggest": {
                "success": True,
                "current_weights": {"momentum": 0.4},
                "suggested_weights": {"momentum": 0.43},
                "rationale": ["x"],
            },
            "portfolio_backtest_summary": {"success": True, "total_return_pct": 1.2},
        }
        compact = compact_quant_report(report)
        self.assertIn("weight_suggest", compact)
        self.assertIn("portfolio_backtest_summary", compact)

    def test_interpret_mocked_llm(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {
            "choices": [{"message": {"content": "1. 因子 IC 平稳\n2. 纸面略低于回测"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        llm.get_response_content.return_value = "1. 因子 IC 平稳\n2. 纸面略低于回测"

        out = interpret_quant_report({"success": True}, llm_client=llm)
        self.assertTrue(out["success"])
        self.assertIn("interpretation", out)


# --- from test_p14_quant.py ---
class TestQuantSkill(unittest.TestCase):
    def test_missing_task(self):
        out = QuantEngine().run({})
        self.assertFalse(out["success"])
        self.assertIn("available_tasks", out)

    def test_daily_summary_mocked(self):
        engine = QuantEngine()
        mock_svc = MagicMock()
        mock_svc.load_last_daily.return_value = {
            "success": True,
            "weight_suggest": {"success": True, "current_weights": {"momentum": 0.4}},
        }
        engine._svc = mock_svc
        out = engine.run({"task": "daily_summary"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "daily_summary")

class TestMarkdownExport(unittest.TestCase):
    def test_render_markdown(self):
        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 2.5,
                "win_rate_pct": 55,
                "trade_count": 4,
                "loaded_stocks": ["600519"],
            },
            "weight_suggest": {
                "success": True,
                "current_weights": {"momentum": 0.4},
                "suggested_weights": {"momentum": 0.43},
                "rationale": ["momentum IC 偏高"],
            },
        }
        md = render_quant_report_markdown(report)
        self.assertIn("# 量化研究日报", md)
        self.assertIn("历史回测摘要", md)
        out = export_quant_report_markdown(report)
        self.assertTrue(out["success"])
        self.assertIn("content", out)

class TestWatchingThreshold(unittest.TestCase):
    def test_watching_aggregate_yhat_offline(self):
        oos_rows = [
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 0.0, "min_score": 0.0, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 52.0, "trade_count": 5}},
                "model_source": "mock",
            },
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 0.5, "min_score": 0.5, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 50.0, "trade_count": 4}},
                "model_source": "mock",
            },
            {
                "success": True,
                "score_scale": "predicted_yhat",
                "best_params": {"wait": 1.0, "min_score": 1.0, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 48.0, "trade_count": 4}},
                "model_source": "mock",
            },
        ]
        idx = {"i": 0}

        def fake_yhat(*_args, **_kwargs):
            row = oos_rows[min(idx["i"], len(oos_rows) - 1)]
            idx["i"] += 1
            return row

        yhat_th = {"avoid": -0.5, "wait": 0.0, "probe": 0.3}
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(_aligned_bars("x", n=80), "mock"),
        ), patch(
            "core.ports.market.query_quote",
            return_value={"success": True, "stock_code": "600519"},
        ), patch(
            "core.signal.threshold_suggest.get_stance_thresholds",
            return_value=yhat_th,
        ), patch(
            "core.signal.threshold_suggest.scan_yhat_wait_oos",
            side_effect=fake_yhat,
        ):
            out = suggest_stance_thresholds_from_watching_oos(
                ["600519", "600036", "300750"],
                lookback=80,
                max_stocks=3,
            )
        self.assertTrue(out["success"], out.get("error") or out)
        self.assertEqual(out["watching_aggregate"]["stock_count"], 3)
        self.assertEqual(out["watching_aggregate"]["median_best_wait"], 0.5)
        self.assertEqual(out["watching_aggregate"]["score_scale"], "predicted_yhat")
        self.assertFalse(out.get("skipped_apply"))
        self.assertEqual(out["suggested_thresholds"]["wait"], 0.5)

    def test_watching_aggregate_heuristic_offline(self):
        oos_rows = [
            {
                "success": True,
                "best_params": {"min_score": 55, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 52.0, "trade_count": 5}},
            },
            {
                "success": True,
                "best_params": {"min_score": 60, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 50.0, "trade_count": 4}},
            },
            {
                "success": True,
                "best_params": {"min_score": 65, "horizon_days": 3},
                "test": {"metrics": {"win_rate_pct": 48.0, "trade_count": 4}},
            },
        ]
        idx = {"i": 0}

        def fake_oos(*_args, **_kwargs):
            row = oos_rows[min(idx["i"], len(oos_rows) - 1)]
            idx["i"] += 1
            return row

        heuristic_th = {"avoid": 45.0, "wait": 55.0, "probe": 68.0}
        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=(_aligned_bars("x", n=80), "mock"),
        ), patch(
            "core.ports.market.query_quote",
            return_value={"success": True, "stock_code": "600519"},
        ), patch(
            "core.signal.threshold_suggest.get_stance_thresholds",
            return_value=heuristic_th,
        ), patch(
            "core.backtest.engine.scan_signal_parameters_oos",
            side_effect=fake_oos,
        ):
            out = suggest_stance_thresholds_from_watching_oos(
                ["600519", "600036", "300750"],
                lookback=80,
                max_stocks=3,
            )
        self.assertTrue(out["success"], out.get("error") or out)
        self.assertEqual(out["watching_aggregate"]["stock_count"], 3)
        self.assertEqual(out["watching_aggregate"]["median_best_min_score"], 60.0)
        self.assertEqual(out["watching_aggregate"]["score_scale"], "heuristic_0_100")


# --- from test_p45_quant.py ---
class TestP45NewFactors(unittest.TestCase):
    def test_registry_has_six_factors(self):
        names = {f["name"] for f in list_factors()}
        self.assertIn("reversal", names)
        self.assertIn("liquidity", names)

    def test_factor_descriptions(self):
        rows = list_factors()
        self.assertTrue(all(f.get("description") for f in rows))
        by_name = {f["name"]: f for f in rows}
        self.assertIn("涨跌幅", by_name["momentum"]["description"])
        self.assertIn("舆情", by_name["alt_sentiment"]["description"])

    def test_default_weights_sum_to_one(self):
        weights = DEFAULT_SIGNAL_CONFIG["weights"]
        self.assertIn("reversal", weights)
        self.assertIn("liquidity", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)

    def test_reversal_prefers_mild_pullback(self):
        bars = _rising_bars()
        for i, b in enumerate(bars[-4:]):
            b["close"] = bars[-5]["close"] * (1 - 0.01 * (i + 1))
        score_pullback, _ = score_reversal(bars)
        score_rising, _ = score_reversal(_rising_bars())
        self.assertGreater(score_pullback, score_rising)

    def test_liquidity_scores_active_turnover(self):
        bars = _rising_bars()
        score, meta = score_liquidity(bars)
        self.assertGreaterEqual(score, 55)
        self.assertIsNotNone(meta.get("turnover_ratio"))

    def test_score_bars_includes_new_sub_scores(self):
        result = score_bars(_rising_bars(), quote={"change_raw": 1.0})
        subs = result["sub_scores"]
        self.assertIn("reversal", subs)
        self.assertIn("liquidity", subs)
        self.assertIn("reversal", result["factor_contrib"])

    def test_factor_experiment_includes_new_factors(self):
        bars = _rising_bars()
        while len(bars) < 35:
            last = dict(bars[-1])
            last["date"] = f"extra-{len(bars)}"
            last["close"] = last["close"] + 0.2
            bars.append(last)
        out = run_factor_experiment(bars, min_history=12)
        names = {r["factor"] for r in out["factors"]}
        self.assertIn("reversal", names)
        self.assertIn("liquidity", names)

    def test_compute_configured_factors_respects_weights(self):
        bars = _rising_bars()
        subs, contribs, _ = compute_configured_factors(
            bars,
            weights={"reversal": 1.0},
            last_change=1.0,
        )
        self.assertEqual(list(subs.keys()), ["reversal"])
        self.assertAlmostEqual(contribs["reversal"], subs["reversal"], places=1)

    def test_live_config_has_six_weights(self):
        cfg = load_signal_config(reload=True)
        self.assertIn("reversal", cfg["weights"])
        self.assertIn("liquidity", cfg["weights"])

    def test_alpha158_migrates_to_alpha_oo(self):
        """旧盘 factor_groups / weights 的 alpha158 键加载时改写为 alpha_oo。"""
        import json
        import tempfile
        from unittest.mock import patch

        from core.signal import config as cfg_mod

        payload = {
            "factor_groups": {"pv_derived": ["alpha158"]},
            "weights": {"alpha158": 0.0},
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(payload, fh)
            path = fh.name
        cfg_mod._cached = None
        try:
            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": path}):
                got = load_signal_config(reload=True)
            self.assertIn("alpha_oo", got["factor_groups"]["pv_derived"])
            self.assertNotIn("alpha158", got["factor_groups"]["pv_derived"])
            self.assertIn("alpha_oo", got["weights"])
            self.assertNotIn("alpha158", got["weights"])
        finally:
            cfg_mod._cached = None
            try:
                os.unlink(path)
            except OSError:
                pass
            load_signal_config(reload=True)


# --- from test_p46_quant.py ---
class TestP46FundamentalFactors(unittest.TestCase):
    def test_registry_has_fundamental_factors(self):
        names = {f["name"] for f in list_factors()}
        self.assertGreaterEqual(len(names), 8)
        self.assertIn("value", names)
        self.assertIn("quality", names)
        self.assertIn("growth", names)
        self.assertIn("size", names)

    def test_default_weights_include_fundamentals(self):
        weights = DEFAULT_SIGNAL_CONFIG["weights"]
        self.assertIn("value", weights)
        self.assertIn("quality", weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)

    def test_value_scores_reasonable_pe(self):
        score, meta = score_value(fundamentals={"pe": 18.0, "pb": 1.5})
        self.assertGreater(score, 60)
        self.assertEqual(meta["value_pe"], 18.0)

    def test_value_neutral_without_data(self):
        score, meta = score_value(fundamentals=None)
        self.assertEqual(score, 50.0)
        self.assertIsNone(meta["value_pe"])
        self.assertTrue(meta.get("omit_sub_score"))

    def test_fund_missing_omitted_from_sub_scores(self):
        bars = _rising_bars()
        subs, _c, meta = compute_configured_factors(
            bars,
            weights={"value": 0.05, "quality": 0.04, "growth": 0.03, "momentum": 0.2},
            fundamentals={},
            required_keys=["value", "quality", "growth", "momentum"],
        )
        self.assertNotIn("value", subs)
        self.assertNotIn("quality", subs)
        self.assertNotIn("growth", subs)
        self.assertIn("momentum", subs)
        self.assertTrue(meta.get("omit_sub_score"))

    def test_quality_scores_high_roe(self):
        score, meta = score_quality(fundamentals={"roe": 22.0, "profit_growth": 15.0})
        self.assertGreaterEqual(score, 64)
        self.assertEqual(meta["quality_roe"], 22.0)
        self.assertNotIn("quality_profit_growth", meta)

    def test_score_bars_includes_fundamental_sub_scores(self):
        fundamentals = {"pe": 16.0, "pb": 1.2, "roe": 18.0, "profit_growth": 12.0}
        result = score_bars(_rising_bars(), quote={"change_raw": 1.0}, fundamentals=fundamentals)
        subs = result["sub_scores"]
        self.assertIn("value", subs)
        self.assertIn("quality", subs)
        self.assertGreater(subs["value"], 50)
        self.assertGreater(subs["quality"], 50)

    def test_normalize_fundamentals_metrics(self):
        raw = {"metrics": {"pe_ttm": 12.5, "roe": 15.0, "total_mv": 1.2e11}}
        out = normalize_fundamentals_metrics(raw)
        self.assertEqual(out["pe_ttm"], 12.5)
        self.assertEqual(out["roe"], 15.0)
        self.assertEqual(out["market_cap"], 1.2e11)

    @patch("core.signal.live_features.resolve_live_fundamentals")
    @patch("core.signal.score_stock.fetch_daily_bars")
    @patch("core.signal.score_stock.query_quote")
    def test_score_stock_passes_fundamentals(self, mock_quote, mock_bars, mock_fund):
        from core.signal.score_stock import score_stock

        mock_quote.return_value = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "price": "1800",
            "change": "+1%",
            "change_raw": 1.0,
            "price_raw": 1800.0,
        }
        mock_bars.return_value = (_rising_bars(), "mock")
        mock_fund.return_value = {
            "metrics": {"pe": 20.0, "pb": 2.0, "roe": 25.0, "profit_growth": 10.0},
            "fundamentals_pit": {"ok": True, "mode": "as_of"},
        }

        with patch(
            "core.valuation_em.merge_cached_valuation", side_effect=lambda c, m, **k: m
        ), patch(
            "core.fundamentals_pit.merge_local_fundamentals_snapshot",
            side_effect=lambda c, m=None: m,
        ), patch(
            "core.signal.live_features.fetch_live_index_bars",
            return_value={"ok": False, "bars": [], "reason": "test"},
        ):
            out = score_stock("600519", bypass_quality_gate=True, skip_sentiment=True)
        self.assertTrue(out["success"])
        subs = out["signal_item"]["sub_scores"]
        self.assertIn("value", subs)
        self.assertIn("quality", subs)
        self.assertIn("sector", out["signal_item"])

    def test_live_config_has_fundamental_weights(self):
        cfg = load_signal_config(reload=True)
        self.assertGreaterEqual(len(cfg["weights"]), 8)
        self.assertIn("value", cfg["weights"])
        self.assertIn("growth", cfg["weights"])
        self.assertTrue(cfg.get("fundamentals", {}).get("enabled"))


# --- from test_p68_quant.py ---
class TestP68QuantToolConfigParams(unittest.TestCase):
    def test_tool_config_has_offline_flag(self):
        path = os.path.join(ROOT, "skills", "quant", "tool_config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        props = cfg["parameters"]["properties"]
        self.assertIn("offline", props)
        self.assertFalse(props["offline"].get("default"))
        self.assertNotIn("include_portfolio_neutral_compare", props)
        self.assertNotIn("top_k", props)

    def test_engine_honors_offline_interpret(self):
        out = QuantEngine().run(
            {"task": "interpret", "use_saved": False, "offline": True, "stock_code": "茅台"}
        )
        self.assertEqual(out.get("task"), "interpret")
        self.assertEqual(out.get("source"), "rule_based")

    def test_readme_documents_offline(self):
        path = os.path.join(ROOT, "skills", "quant", "README.md")
        if not os.path.isfile(path):
            self.skipTest("skills/quant/README.md 已下线")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("offline", text)
        self.assertNotIn("include_portfolio_neutral_compare", text)


# --- from test_p70_quant.py ---
class TestP70InterpretOfflineApi(unittest.TestCase):
    def test_interpret_api_passes_offline_flag(self):
        try:
            from fastapi.testclient import TestClient

            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock_out = {
            "success": True,
            "source": "rule_based",
            "interpretation": "历史回测：规则解读测试",
        }
        with patch.object(deps.quant, "interpret_report", return_value=mock_out) as mocked:
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/interpret",
                json={"use_saved": True, "offline": True},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["source"], "rule_based")
        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        self.assertTrue(kwargs.get("offline"))

    def test_quant_service_offline_interpret(self):
        from quant.services.quant_interpret import build_rule_based_interpret
        from quant.services.quant_service import QuantService

        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 1.2,
                "win_rate_pct": 50,
                "trade_count": 2,
            }
        }
        out = QuantService().interpret_report(report, use_saved=False, offline=True)
        expected = build_rule_based_interpret(report)
        self.assertEqual(out.get("source"), expected.get("source"))


# --- from test_p77_quant.py ---
class TestP77InterpretCrossSectionScore(unittest.TestCase):
    def _report_with_cs(self):
        return {
            "cross_section": {
                "success": True,
                "book": [
                    {"stock_code": "600519", "stock_name": "贵州茅台", "score": 72.5},
                    {"stock_code": "600036", "stock_name": "招商银行", "score": 61.0},
                ],
                "neutralization": {"applied": True, "method": "zscore"},
            }
        }

    def test_summarize_cross_section_scores(self):
        sm = summarize_cross_section_scores(self._report_with_cs()["cross_section"])
        self.assertIsNotNone(sm)
        self.assertEqual(sm["ranked_count"], 2)
        self.assertIn("72.5", sm["summary_line"])

    def test_compact_includes_cross_section(self):
        compact = compact_quant_report(self._report_with_cs())
        self.assertIn("cross_section", compact)
        self.assertEqual(compact["cross_section"]["ranked_count"], 2)

    def test_rule_based_interpret_mentions_score(self):
        out = build_rule_based_interpret(self._report_with_cs())
        self.assertIn("ŷ", out["interpretation"])
        self.assertIn("不等于买入", out["interpretation"])


# --- from test_p86_quant.py ---
class TestP86FactorOls(unittest.TestCase):
    def test_compute_factor_ols_on_mock_bars(self):
        bars = rising_bars(100)
        report = compute_factor_ols_report(bars, horizon_days=3, min_history=12)
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("task"), "factor_ols")
        self.assertIsInstance(report.get("coefficients"), dict)
        self.assertGreater(report.get("sample_count") or 0, 0)
        self.assertIn("current_weights", report)
        self.assertIn("不自动写 signal_config", report.get("note") or "")

    def test_quant_skill_factor_ols_task(self):
        engine = QuantEngine()
        with apply_case_mocks(
            {
                "quotes": {
                    "600519": {
                        "success": True,
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "market": "CN",
                    }
                },
                "daily_bars": "rising_100",
                "resolve_market_code": ["CN", "600519"],
            }
        ):
            out = engine.run(
                {
                    "task": "factor_ols",
                    "stock_code": "600519",
                    "lookback": 100,
                    "horizon_days": 3,
                }
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("task"), "factor_ols")
        self.assertIn("coefficients", out)

    def test_qr_ols_recovers_known_beta(self):
        from core.research.factor_ols_fit import _fit_ols_once

        xs = []
        ys = []
        for i in range(30):
            a = float(i)
            b = float(i % 7) - 3.0
            xs.append({"a": a, "b": b})
            ys.append(2.0 + 3.0 * a - 1.5 * b)
        fit = _fit_ols_once(xs, ys, ["a", "b"])
        self.assertIsNotNone(fit)
        assert fit is not None
        self.assertEqual(fit.get("solver"), "qr")
        self.assertAlmostEqual(fit["intercept"], 2.0, places=4)
        self.assertAlmostEqual(fit["beta"][1], 3.0, places=4)
        self.assertAlmostEqual(fit["beta"][2], -1.5, places=4)
        self.assertAlmostEqual(fit["r_squared"] or 0.0, 1.0, places=4)

    def test_qr_ols_rejects_exact_collinear(self):
        from core.research.factor_ols_fit import _fit_ols_once, _ols_with_intercept

        xs = [{"a": float(i), "b": float(2 * i)} for i in range(20)]
        ys = [1.0 + 0.5 * float(i) for i in range(20)]
        self.assertIsNone(_fit_ols_once(xs, ys, ["a", "b"]))
        fit = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[])
        self.assertIsNotNone(fit)
        assert fit is not None
        self.assertEqual(fit.get("solver"), "qr")
        self.assertEqual(fit.get("active_features"), ["a"])
        self.assertIn("b", fit.get("dropped_collinear") or [])

    def test_ridge_keeps_collinear_and_shrinks(self):
        from core.research.factor_ols_fit import _fit_ols_once, _ols_with_intercept

        xs = []
        ys = []
        for i in range(40):
            a = float(i) + 0.01 * (i % 3)
            b = 2.0 * a + 0.02 * ((i % 5) - 2)
            xs.append({"a": a, "b": b})
            ys.append(1.0 + 0.4 * a + 0.1 * b)
        ols = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[], ridge_lambda=0.0)
        ridge = _ols_with_intercept(xs, ys, ["a", "b"], excluded=[], ridge_lambda=5.0)
        self.assertIsNotNone(ridge)
        assert ridge is not None
        self.assertEqual(ridge.get("solver"), "ridge")
        self.assertEqual(ridge.get("ridge_lambda"), 5.0)
        self.assertEqual(set(ridge.get("active_features") or []), {"a", "b"})
        self.assertEqual(ridge.get("dropped_collinear") or [], [])
        # 精确共线时纯 OLS 会剔列；Ridge 保留两列
        exact = [{"a": float(i), "b": float(2 * i)} for i in range(20)]
        y_exact = [1.0 + 0.5 * float(i) for i in range(20)]
        self.assertIsNone(_fit_ols_once(exact, y_exact, ["a", "b"], ridge_lambda=0.0))
        ridge_exact = _fit_ols_once(exact, y_exact, ["a", "b"], ridge_lambda=1.0)
        self.assertIsNotNone(ridge_exact)
        assert ridge_exact is not None
        self.assertEqual(ridge_exact.get("solver"), "ridge")
        # 相对无正则 OLS（若可拟合），Ridge 斜率 L2 范数应更小
        if ols is not None and set(ols.get("active_features") or []) == {"a", "b"}:
            def slope_l2(fit):
                return sum(float(fit["coefficients"][k]) ** 2 for k in ("a", "b"))

            self.assertLess(slope_l2(ridge), slope_l2(ols) + 1e-9)

    def test_prepare_drops_constant_on_complete_panel(self):
        from core.research.factor_ols_fit import _prepare_complete_panel

        # a 在全样本有波动；完整行上 a 常数 → 应剔除
        xs = [
            {"a": 1.0, "b": 1.0, "c": 2.0},
            {"a": 9.0, "b": None, "c": 3.0},
            {"a": 1.0, "b": 2.0, "c": 4.0},
            {"a": 1.0, "b": 3.0, "c": 5.0},
            {"a": 1.0, "b": 4.0, "c": 6.0},
            {"a": 1.0, "b": 5.0, "c": 7.0},
            {"a": 1.0, "b": 6.0, "c": 8.0},
            {"a": 1.0, "b": 7.0, "c": 9.0},
            {"a": 1.0, "b": 8.0, "c": 1.0},
            {"a": 1.0, "b": 9.0, "c": 2.0},
        ]
        ys = [float(i) for i in range(len(xs))]
        xs_c, ys_c, active, excluded, meta = _prepare_complete_panel(
            xs, ys, ["a", "b", "c"], min_samples_over_p=2
        )
        self.assertIsNotNone(xs_c)
        self.assertNotIn("a", active)
        self.assertIn("a", meta["dropped_constant"])
        self.assertIn("b", active)
        self.assertIn("c", active)

    def test_prepare_keeps_narrow_column(self):
        from core.research.factor_ols_fit import _prepare_complete_panel

        xs = []
        for i in range(20):
            xs.append(
                {
                    "low": 50.0 + (0.1 if i % 2 else -0.1),
                    "hi1": 40.0 + float(i),
                    "hi2": 60.0 - float(i) * 1.2,
                }
            )
        ys = [float(i) for i in range(len(xs))]
        _xs_c, _ys_c, active, _excl, meta = _prepare_complete_panel(
            xs, ys, ["low", "hi1", "hi2"], min_samples_over_p=2
        )
        self.assertIn("low", active)
        self.assertIn("hi1", active)
        self.assertIn("hi2", active)
        self.assertNotIn("low", meta.get("dropped_constant") or [])

    def test_prepare_impute_keys_keeps_incomplete_rows(self):
        from core.research.factor_ols_fit import _prepare_complete_panel

        xs = []
        for i in range(24):
            xs.append(
                {
                    "path": 10.0 + float(i),
                    "shape_x": None if i < 10 else 0.2 + 0.02 * float(i),
                }
            )
        ys = [float(i) for i in range(len(xs))]
        orig_missing = xs[0]["shape_x"]
        xs_drop, _ys_d, _act_d, _ex_d, meta_drop = _prepare_complete_panel(
            xs, ys, ["path", "shape_x"], min_samples_over_p=2
        )
        self.assertIsNone(orig_missing)
        self.assertEqual(len(xs_drop or []), 14)
        self.assertFalse(meta_drop.get("imputed_keys"))

        xs_keep, ys_keep, active, _excl, meta = _prepare_complete_panel(
            xs,
            ys,
            ["path", "shape_x"],
            min_samples_over_p=2,
            impute_keys=["shape_x"],
        )
        self.assertIsNone(xs[0]["shape_x"], msg="不得改写调用方行")
        self.assertIsNotNone(xs_keep)
        self.assertEqual(len(xs_keep or []), 24)
        self.assertEqual(len(ys_keep or []), 24)
        self.assertEqual(set(active), {"path", "shape_x"})
        self.assertIn("shape_x", meta.get("imputed_keys") or [])
        self.assertEqual((meta.get("impute_n_filled") or {}).get("shape_x"), 10)
        mu = float((meta.get("impute_means") or {}).get("shape_x"))
        filled = [row["shape_x"] for row in (xs_keep or [])[:10]]
        self.assertTrue(filled)
        self.assertTrue(all(abs(float(v) - mu) < 1e-9 for v in filled))


# --- from test_p94_quant.py ---
class TestP94QuantServiceMixins(unittest.TestCase):
    def test_facade_exposes_domain_methods(self):
        from quant.services.quant_service import QuantService
        from quant.services.quant_service_config import QuantConfigMixin
        from quant.services.quant_service_factors import QuantFactorMixin
        from quant.services.quant_service_ops import QuantOpsMixin
        from quant.services.quant_service_replay import QuantPortfolioMixin

        self.assertTrue(issubclass(QuantService, QuantConfigMixin))
        self.assertTrue(issubclass(QuantService, QuantFactorMixin))
        self.assertTrue(issubclass(QuantService, QuantPortfolioMixin))
        self.assertTrue(issubclass(QuantService, QuantOpsMixin))
        qs = QuantService()
        for name in (
            "config_summary",
            "run_factor_experiment",
            "run_portfolio_backtest",
            "start_portfolio_backtest_job",
            "run_t0_backtest",
            "start_t0_backtest_job",
            "load_last_t0_backtest",
            "build_daily_report",
            "load_last_daily",
        ):
            self.assertTrue(callable(getattr(qs, name)), name)

    def test_web_routers_mounted(self):
        try:
            from fastapi.testclient import TestClient

            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(app)
        paths = set(app.openapi().get("paths") or {})
        for path in (
            "/api/health",
            "/api/chat",
            "/api/paper",
            "/api/quant/config",
            "/api/quant/t0-backtest",
            "/api/quant/last-t0-backtest",
            "/api/daily/presets",
            "/api/watching",
            "/api/evals/cases",
        ):
            self.assertIn(path, paths)

        res = client.get("/api/quant/config")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

    def test_docs_mark_upgrade_as_archive(self):
        path = os.path.join(ROOT, "docs", "archive", "quant-upgrade.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("历史归档", text)
        self.assertIn("P94", text)


if __name__ == "__main__":
    unittest.main()
