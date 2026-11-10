"""Research OOS: predicted_score arm vs flat 0% baseline."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.signal.config import load_signal_config, signal_config_overlay
from core.signal.return_score import resolve_research_rank_mode
from core.signal.weight_oos_gate import (
    OOS_PRODUCT_SEMANTICS,
    evaluate_research_oos,
)
from core.signal.weight_suggest import format_weight_config_diff


class TestSignalConfigOverlay(unittest.TestCase):
    def test_overlay_weights_temporary(self):
        base = load_signal_config()
        mom0 = float((base.get("weights") or {}).get("momentum") or 0)
        with signal_config_overlay({"weights": {"momentum": 0.99}}):
            ov = load_signal_config()
            self.assertAlmostEqual(float(ov["weights"]["momentum"]), 0.99)
        again = load_signal_config()
        self.assertAlmostEqual(float(again["weights"]["momentum"]), mom0)

class TestResearchRankMode(unittest.TestCase):
    def test_resolve_maps_heuristic_to_predicted(self):
        self.assertEqual(resolve_research_rank_mode("heuristic"), "predicted_score")
        self.assertEqual(resolve_research_rank_mode("predicted_score"), "predicted_score")

class TestWeightOosGate(unittest.TestCase):
    def test_gate_pass_when_research_oos_better(self):
        def fake_load(codes, lookback=90, fetch_fundamentals=False):
            bars = {
                "a": [{"date": f"2024-01-{i:02d}", "close": 10 + i * 0.01} for i in range(1, 60)],
                "b": [{"date": f"2024-01-{i:02d}", "close": 20 + i * 0.02} for i in range(1, 60)],
                "c": [{"date": f"2024-01-{i:02d}", "close": 30 + i * 0.01} for i in range(1, 60)],
            }
            return bars, [], {}

        def fake_bt(stock_bars, **kwargs):
            curve = [
                {"date": "d1", "equity": 100},
                {"date": "d2", "equity": 105},
                {"date": "d3", "equity": 112},
                {"date": "d4", "equity": 120},
            ]
            return {
                "success": True,
                "metrics": {"total_return_pct": curve[-1]["equity"] - 100},
                "equity_curve": curve,
                "trades": [{"id": 1}],
                "params": {"rank_mode": "predicted_score"},
            }

        model = {
            "intercept": 0.0,
            "coefficients": {"momentum": 0.1},
            "z_means": {},
            "z_stds": {},
            "feature_zscore": True,
        }
        captured = {}

        def _capture_bt(stock_bars, **kwargs):
            captured.update(kwargs)
            return fake_bt(stock_bars, **kwargs)

        with patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=fake_load,
        ), patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["a", "b", "c"]},
        ), patch(
            "core.backtest.topk_probe.run_oos_topk_curve",
            side_effect=_capture_bt,
        ):
            out = evaluate_research_oos(
                codes=["a", "b", "c"],
                research_models_by_code={"a": model, "b": model, "c": model},
                baseline_weights={"momentum": 0.5, "value": 0.5},
                lookback=60,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["passed"])
        self.assertEqual(out["reason"], "oos_not_worse")
        self.assertEqual(out["baseline_rank_mode"], "flat_zero")
        self.assertEqual(out["research_rank_mode"], "predicted_score")
        self.assertIn("平坦 0%", OOS_PRODUCT_SEMANTICS)
        self.assertIs(captured.get("return_model"), model)
        self.assertTrue(captured.get("freeze_return_model"))

    def test_gate_reuses_preloaded_stock_bars(self):
        """上游传入 bars 时不再二次 load_portfolio_stock_bars。"""
        model = {
            "intercept": 0.0,
            "coefficients": {"momentum": 0.1},
            "z_means": {},
            "z_stds": {},
            "feature_zscore": True,
        }
        bars = {
            "a": [{"date": f"2024-01-{i:02d}", "close": 10 + i * 0.01} for i in range(1, 60)],
            "b": [{"date": f"2024-01-{i:02d}", "close": 20 + i * 0.02} for i in range(1, 60)],
            "c": [{"date": f"2024-01-{i:02d}", "close": 30 + i * 0.01} for i in range(1, 60)],
        }

        def fake_bt(stock_bars, **kwargs):
            return {
                "success": True,
                "metrics": {"total_return_pct": 5.0},
                "equity_curve": [
                    {"date": "d1", "equity": 100},
                    {"date": "d2", "equity": 103},
                    {"date": "d3", "equity": 104},
                    {"date": "d4", "equity": 105},
                ],
                "trades": [{"id": 1}],
                "params": {},
            }

        with patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
        ) as load_mock, patch(
            "core.backtest.topk_probe.run_oos_topk_curve",
            side_effect=fake_bt,
        ):
            out = evaluate_research_oos(
                codes=["a", "b", "c"],
                research_models_by_code={"a": model, "b": model, "c": model},
                baseline_weights={"momentum": 0.5, "value": 0.5},
                lookback=60,
                stock_bars=bars,
            )
        load_mock.assert_not_called()
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out.get("stock_count"), 3)
        self.assertIsInstance(out.get("reason"), str)

    def test_diff_apply_note_reflects_gate(self):
        sug = {
            "success": True,
            "current_weights": {"momentum": 0.5, "value": 0.5},
            "suggested_weights": {"momentum": 0.6, "value": 0.4},
            "deltas": {},
            "oos_gate": {
                "ok": True,
                "passed": False,
                "skipped": False,
                "reason": "oos_worse_-3.0pp",
            },
            "promote_ready": False,
        }
        diff = format_weight_config_diff(sug)
        self.assertIn("未过", diff["apply_note"])
        self.assertFalse(diff["promote_ready"])

    def test_no_trades_counts_as_skipped(self):
        from core.signal.weight_oos_gate import _compare_arms

        base = {
            "success": True,
            "trade_count": 80,
            "oos": {"oos_return_pct": 5.0, "failed": False},
        }
        research = {
            "success": True,
            "trade_count": 0,
            "oos": {"reason": "equity_curve_too_short", "failed": True},
        }
        out = _compare_arms(
            base,
            research,
            tol=1.0,
            lookback=80,
            top_k=2,
            horizon_days=1,
            stock_count=12,
            codes=["a"],
        )
        self.assertTrue(out["skipped"])
        self.assertFalse(out["passed"])
        self.assertEqual(out["reason"], "research_no_trades")

    def test_cluster_rank_only_ignores_is_oos_gap(self):
        from core.signal.weight_oos_gate import _compare_arms

        base = {
            "success": True,
            "trade_count": 80,
            "oos": {"oos_return_pct": 3.5, "is_return_pct": -11.0, "failed": False},
        }
        research = {
            "success": True,
            "trade_count": 11,
            "oos": {
                "oos_return_pct": 13.6,
                "is_return_pct": 41.8,
                "failed": True,
                "fail_reason": "oos_underperform_gap_-28.2pp",
            },
        }
        blocked = _compare_arms(
            base, research, tol=1.0, lookback=80, top_k=2, horizon_days=1,
            stock_count=11, codes=["a"], require_clean_is_oos=True,
        )
        self.assertFalse(blocked["passed"])
        allowed = _compare_arms(
            base, research, tol=1.0, lookback=80, top_k=2, horizon_days=1,
            stock_count=11, codes=["a"], require_clean_is_oos=False,
        )
        self.assertTrue(allowed["passed"])
        self.assertEqual(allowed["reason"], "oos_not_worse_is_gap")

    def test_weight_suggest_oos_decoupled_from_yhat(self):
        from core.signal.weight_oos_gate import (
            YHAT_WEIGHTS_DECOUPLED,
            evaluate_weight_suggestion_oos,
        )
        from core.signal.weight_suggest import format_weight_config_diff

        out = evaluate_weight_suggestion_oos(
            {"momentum": 0.5},
            {"momentum": 0.6},
            codes=["a", "b", "c"],
            lookback=60,
        )
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("passed"))
        self.assertEqual(out.get("reason"), YHAT_WEIGHTS_DECOUPLED)
        diff = format_weight_config_diff(
            {
                "success": True,
                "current_weights": {"momentum": 0.5},
                "suggested_weights": {"momentum": 0.6},
                "deltas": {},
                "oos_gate": out,
                "promote_ready": True,
            }
        )
        self.assertTrue(diff.get("promote_ready"))
        self.assertIn("LightGBM", diff.get("apply_note") or "")


class TestScoreViewLgbPayload(unittest.TestCase):
    def test_active_return_model_payload_keeps_lgb_meta(self):
        from core.research.oo_lgb import OoLightgbmScorer
        from core.signal.score_view import active_return_model_payload

        scorer = OoLightgbmScorer(
            {
                "schema": "oo_lgb_v1",
                "solver": "lightgbm",
                "feature_names": ["momentum"],
                "feature_importance": [{"key": "momentum", "gain": 3.0}],
                "booster_b64": "xx",
                "coefficients": {},
            },
            model_role="live",
        )
        pay = active_return_model_payload(
            global_model=scorer, return_model_source="global"
        )
        self.assertEqual(pay.get("feature_names"), ["momentum"])
        self.assertTrue(pay.get("has_booster"))
        self.assertEqual(pay.get("backend"), "lightgbm")
        self.assertNotIn("booster_b64", pay)


if __name__ == "__main__":
    unittest.main()
