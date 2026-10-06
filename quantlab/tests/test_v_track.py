"""V0–V5 策略验证升级轨单测。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta


class TestV0SampleDiscipline(unittest.TestCase):
    def test_real_vs_synthetic_coverage(self):
        from core.sample_ops import fundamentals_history_coverage, seed_fundamentals_history_ladder
        from core.store import snapshot_cache_path

        with tempfile.TemporaryDirectory() as td:
            code = "V001"
            path = snapshot_cache_path("fundamentals", code, td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "kind": "fundamentals",
                        "code": code,
                        "fetched_at": "2026-07-01T12:00:00",
                        "data": {"metrics": {"pe": 12, "pb": 1.2}},
                        "history": [
                            {
                                "as_of": "2026-07-01",
                                "metrics": {"pe": 12, "pb": 1.2},
                                "data_source": "test",
                            }
                        ],
                    },
                    f,
                )
            seed_fundamentals_history_ladder(
                codes=[code], store_dir=td, quarters=2, write=True, day_step=90
            )
            cov = fundamentals_history_coverage(store_dir=td, codes=[code])
            self.assertEqual(cov["multi_point"], 1)
            self.assertEqual(cov["synthetic_multi_point"], 1)
            self.assertEqual(cov["real_multi_point"], 0)

    def test_empty_universe_report(self):
        from core.validation_universe import empty_fundamentals_report, resolve_validation_codes

        out = resolve_validation_codes(
            watching_codes=["A", "B"],
            universe={"exclude_codes": ["B"], "include_only": []},
        )
        self.assertEqual(out["codes"], ["A", "B"])
        self.assertEqual(out["source"], "watching")
        empty = empty_fundamentals_report(codes=["NOPE999"])
        self.assertIn("NOPE999", empty["empty_codes"])

    def test_ingest_real_history_drops_synthetic(self):
        from unittest.mock import patch

        from core.sample_ops import (
            fundamentals_history_coverage,
            ingest_real_fundamentals_history,
        )
        from core.store import snapshot_cache_path

        with tempfile.TemporaryDirectory() as td:
            code = "600036"
            path = snapshot_cache_path("fundamentals", code, td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "kind": "fundamentals",
                        "code": code,
                        "fetched_at": "2026-07-01T12:00:00",
                        "data": {"metrics": {"roe": 1.0}},
                        "history": [
                            {
                                "as_of": "2025-01-01",
                                "metrics": {"roe": 1.0},
                                "data_source": "synthetic_demo_ladder",
                                "synthetic_demo": True,
                            },
                            {
                                "as_of": "2026-07-01",
                                "metrics": {"roe": 1.0},
                                "data_source": "akshare_financial_indicator",
                            },
                        ],
                    },
                    f,
                )
            series = [
                {"as_of": "2024-03-31", "roe": 10.0, "profit_growth": 1.0, "source": "akshare_financial_indicator"},
                {"as_of": "2024-06-30", "roe": 11.0, "profit_growth": 1.1, "source": "akshare_financial_indicator"},
                {"as_of": "2024-09-30", "roe": 12.0, "profit_growth": 1.2, "source": "akshare_financial_indicator"},
            ]
            with patch(
                "adapters.fundamentals.engine.fetch_cn_financial_series",
                return_value=series,
            ):
                out = ingest_real_fundamentals_history(
                    codes=[code], store_dir=td, write=True, drop_synthetic=True
                )
            self.assertEqual(out["updated_count"], 1)
            cov = fundamentals_history_coverage(store_dir=td, codes=[code])
            self.assertEqual(cov["real_multi_point"], 1)
            self.assertEqual(cov["synthetic_multi_point"], 0)
            self.assertGreaterEqual(cov["rows"][0]["real_points"], 2)


class TestV1FitGapAndCosts(unittest.TestCase):
    def test_qp_lite_unavailable_falls_back(self):
        from core.portfolio_optimize import optimize_weights

        out = optimize_weights(
            [
                {"stock_code": "600036", "score": 70, "sector": "银行"},
                {"stock_code": "601318", "score": 66, "sector": "保险"},
            ],
            weight_mode="qp_lite",
            apply_market_vol=False,
            max_position_pct=20,
            max_sector_pct=40,
        )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("requested_weight_mode"), "qp_lite")
        # 无 cvxpy 时应回退 score_budget 且 qp.available=False
        if not (out.get("qp") or {}).get("available"):
            self.assertEqual(out.get("weight_mode"), "score_budget")
            self.assertFalse((out.get("qp") or {}).get("available"))
        self.assertGreaterEqual(int(out.get("count") or 0), 1)

    def test_fit_gap_hints(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(
            realization={"status": "unavailable", "reason": "short"},
            cost_compare={"return_gap_pp": 2.5, "avg_impact_bps": 12},
            source_audit={"fallback_count": 2, "status": "warn"},
        )
        codes = {h["code"] for h in out["hints"]}
        self.assertIn("realization_unavailable", codes)
        self.assertIn("cost_gap", codes)
        self.assertIn("impact", codes)

    def test_impact_monotonic(self):
        from core.backtest.costs import estimate_impact_cost

        low = estimate_impact_cost(10_000, 1_000_000)
        high = estimate_impact_cost(500_000, 1_000_000)
        self.assertGreaterEqual(high, low)


class TestV2GapRiskAndPromoteNote(unittest.TestCase):
    def test_gap_risk_factor(self):
        from core.signal.factors.gap_risk import score_gap_risk

        bars = [
            {"close": 10.0, "open": 10.0},
            {"close": 10.1, "open": 10.0},
            {"close": 10.2, "open": 11.0},  # ~8.9% gap up
        ]
        score, meta = score_gap_risk(bars)
        self.assertTrue(meta.get("ok"))
        self.assertLess(score, 50)


class TestV3WeightCompareAndOutcomeGate(unittest.TestCase):
    def test_weight_mode_compare(self):
        from core.weight_mode_compare import compare_weight_modes

        out = compare_weight_modes(
            [
                {"stock_code": "A", "score": 90, "sector": "X"},
                {"stock_code": "B", "score": 80, "sector": "Y"},
                {"stock_code": "C", "score": 70, "sector": "X"},
            ]
        )
        self.assertIn("score_budget", out["modes"])
        self.assertIn("risk_parity_lite", out["modes"])

    def test_effectiveness_pending_under_20(self):
        from core.sample_ops import sample_status

        paper = {
            "snapshots": [],
            "operation_log": [
                {
                    "type": "risk_block",
                    "ts": "2026-07-29T10:00:00",
                    "detail": "x",
                    "meta": {"codes": ["max_position"], "outcome": "true_positive"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            # isolate empty fund store by pointing coverage to empty dir via monkeypatch hard —
            # sample_status uses default store; just assert risk gate field
            st = sample_status(paper=paper)
            self.assertTrue(st["risk_blocks"]["effectiveness_pending"])
            self.assertIsNone(st["risk_blocks"]["effectiveness_rate"])


class TestV4ValidationPack(unittest.TestCase):
    def test_build_pack(self):
        from core.validation_pack import build_validation_pack, render_validation_pack_markdown

        out = build_validation_pack(
            backtest_result={
                "success": True,
                "metrics": {"total_return_pct": 1.2},
                "cost_compare": {"return_gap_pp": 0.5, "avg_impact_bps": 3},
                "source_audit": {"status": "ok", "fallback_count": 0},
            },
            signal_config={"weights": {"momentum": 1.0}},
            sample_status={"discipline": {"warnings": ["demo"]}},
        )
        self.assertTrue(out["ok"])
        self.assertIn("fingerprint", out["pack"])
        md = render_validation_pack_markdown(out)
        self.assertIn("策略验证包", md)


class TestV5MaturityGate(unittest.TestCase):
    def test_gate_shape(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.8,
                    "empty": 0,
                    "total": 10,
                    "real_multi_point": 8,
                    "synthetic_multi_point": 0,
                },
                "ttm": {"real_events": 5, "seeded_events": 1},
                "paper_snapshots": {"enough_for_sharpe": True, "count": 40, "densified_count": 0},
                "risk_blocks": {"labeled_count": 20, "block_count": 20},
                "discipline": {"warnings": []},
            },
            north_star={"realization": {"status": "ok", "corr": 0.55, "tracking_error_pct": 1.2}},
            core_paths={"ok": True},
        )
        self.assertTrue(out["ready_for_n6_review"])
        self.assertEqual(out["hard_passed"], out["hard_total"])
        self.assertIn("next_actions", out)

    def test_unavailable_realization_blocks_ready(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.9,
                    "empty": 0,
                    "total": 10,
                    "real_multi_point": 9,
                    "synthetic_multi_point": 0,
                },
                "ttm": {"real_events": 3, "seeded_events": 0},
                "paper_snapshots": {"enough_for_sharpe": True, "count": 40, "densified_count": 0},
                "risk_blocks": {"labeled_count": 0, "block_count": 0},
                "discipline": {"warnings": []},
            },
            north_star={"realization": {"status": "unavailable", "reason": "curves_too_short"}},
            core_paths={"ok": True},
        )
        self.assertFalse(out["ready_for_n6_review"])
        by_id = {i["id"]: i for i in out["items"]}
        self.assertFalse(by_id["realization_readable"]["ok"])
        self.assertTrue(any("paper_daily" in a for a in (out.get("next_actions") or [])))

    def test_fit_gap_includes_scope_and_paper_ops(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(
            realization={"status": "unavailable", "reason": "no_date_overlap"},
            cost_compare={"return_gap_pp": 2.0},
            paper_ops={"cost_model": "zero", "weight_mode": "score_budget"},
            backtest_params={"cost_model": "simple_cn", "weight_mode": "equal", "horizon_days": 3},
            universe={"candidate_count": 4, "dropped_thin_count": 2},
        )
        codes = {h["code"] for h in out["hints"]}
        self.assertIn("live_vs_bt_scope", codes)
        self.assertIn("cost_model_mismatch", codes)
        self.assertIn("weight_mode_mismatch", codes)
        self.assertIn("small_universe", codes)
        self.assertGreaterEqual(out["warn_count"], 1)


if __name__ == "__main__":
    unittest.main()
