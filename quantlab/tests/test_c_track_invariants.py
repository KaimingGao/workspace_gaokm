"""C 轨：状态层 SSOT（risk limits / validate_paper / cycle hygiene）+ C1.x 收口。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestC1RiskLimitsSSOT(unittest.TestCase):
    def test_default_limits_match_spec_fillins(self):
        from core.risk.limits import (
            DEFAULT_RISK_LIMITS,
            fill_risk_defaults,
            resolve_account_risk_limits,
        )
        from core.strategy import get_strategy_spec

        filled = fill_risk_defaults({})
        self.assertEqual(filled["max_drawdown_pct"], DEFAULT_RISK_LIMITS["max_drawdown_pct"])
        self.assertEqual(filled["max_position_pct"], DEFAULT_RISK_LIMITS["max_position_pct"])
        self.assertEqual(filled["max_sector_pct"], DEFAULT_RISK_LIMITS["max_sector_pct"])
        self.assertEqual(filled["max_positions"], DEFAULT_RISK_LIMITS["max_positions"])
        self.assertNotEqual(filled["max_positions"], 5)

        spec = get_strategy_spec("short_conservative")
        self.assertIn("max_drawdown_pct", spec["risk"])

        bad = resolve_account_risk_limits(strategy_id="___missing___")
        self.assertEqual(bad["max_positions"], DEFAULT_RISK_LIMITS["max_positions"])
        self.assertEqual(bad["max_drawdown_pct"], DEFAULT_RISK_LIMITS["max_drawdown_pct"])

    def test_short_conservative_uses_spec_explicit(self):
        from core.risk.limits import resolve_account_risk_limits

        lim = resolve_account_risk_limits(strategy_id="short_conservative")
        self.assertEqual(lim["max_drawdown_pct"], 15.0)
        self.assertEqual(lim["max_position_pct"], 20.0)
        self.assertEqual(lim["max_sector_pct"], 35.0)
        self.assertEqual(lim["max_positions"], 15)

    def test_checks_and_cycle_share_limits(self):
        from core.risk import check_account_risk
        from core.risk.limits import resolve_account_risk_limits

        paper = {
            "strategy_id": "short_conservative",
            "cash": 100000,
            "holdings": [],
            "cost_model": "simple_cn",
        }
        lim = resolve_account_risk_limits(paper=paper)
        gate = check_account_risk(
            paper,
            {"equity": 100000, "max_drawdown_pct": 5.0, "holdings": []},
        )
        self.assertEqual(gate["limits"]["max_positions"], lim["max_positions"])
        self.assertEqual(gate["limits"]["max_position_pct"], lim["max_position_pct"])

    def test_paper_rules_override_spec(self):
        from core.risk.limits import resolve_account_risk_limits

        paper = {
            "strategy_id": "short_conservative",
            "rules": {"max_positions": 10, "max_position_pct": 12.0},
        }
        lim = resolve_account_risk_limits(paper=paper)
        self.assertEqual(lim["max_positions"], 10)
        self.assertEqual(lim["max_position_pct"], 12.0)

    def test_pct_to_fraction(self):
        from core.risk.limits import pct_to_fraction

        self.assertAlmostEqual(pct_to_fraction(20, default=0.25), 0.20)
        self.assertAlmostEqual(pct_to_fraction(0.25, default=0.20), 0.25)
        self.assertAlmostEqual(pct_to_fraction(None, default=0.25), 0.25)
        self.assertAlmostEqual(pct_to_fraction(-3, default=0.25), 0.25)
        self.assertAlmostEqual(pct_to_fraction(float("nan"), default=0.25), 0.25)
        self.assertLessEqual(pct_to_fraction(250, default=0.25), 1.0)


class TestRobustLimits(unittest.TestCase):
    def test_garbage_rules_fall_back_to_defaults(self):
        from core.risk.limits import DEFAULT_RISK_LIMITS, resolve_account_risk_limits

        lim = resolve_account_risk_limits(
            strategy_id="___missing___",
            paper={"rules": {"max_positions": "nope", "max_drawdown_pct": float("nan")}},
        )
        self.assertEqual(lim["max_positions"], DEFAULT_RISK_LIMITS["max_positions"])
        self.assertEqual(lim["max_drawdown_pct"], DEFAULT_RISK_LIMITS["max_drawdown_pct"])

    def test_negative_and_huge_positions_clamped(self):
        from core.risk.limits import resolve_account_risk_limits

        lim = resolve_account_risk_limits(risk={"max_positions": -3, "max_position_pct": 999})
        self.assertGreaterEqual(lim["max_positions"], 1)
        self.assertLessEqual(lim["max_position_pct"], 100.0)

    def test_non_mapping_paper_safe(self):
        from core.risk.limits import DEFAULT_RISK_LIMITS, resolve_account_risk_limits

        lim = resolve_account_risk_limits(paper="broken", strategy_id="___missing___")  # type: ignore[arg-type]
        self.assertEqual(lim["max_positions"], DEFAULT_RISK_LIMITS["max_positions"])

    def test_bool_max_positions_ignored(self):
        from core.risk.limits import DEFAULT_RISK_LIMITS, resolve_account_risk_limits

        lim = resolve_account_risk_limits(
            strategy_id="___missing___",
            risk={"max_positions": True},
        )
        self.assertEqual(lim["max_positions"], DEFAULT_RISK_LIMITS["max_positions"])


class TestC1xKellyUnits(unittest.TestCase):
    def test_max_position_percent_as_fraction(self):
        from core.signal.factors.kelly import adaptive_position_sizing

        paper = {"strategy_id": "short_conservative", "trades": [], "cash": 1}
        # Spec/rules 百分数 20 → 上限 0.20；低分会再打折，但不得超过 0.20
        ratio = adaptive_position_sizing(
            paper, score=50.0, rules={"position_pct": 0.15, "max_position_pct": 20}
        )
        self.assertLessEqual(ratio, 0.20 + 1e-9)
        self.assertGreater(ratio, 0.0)

    def test_max_position_fraction_style_still_works(self):
        from core.signal.factors.kelly import adaptive_position_sizing

        paper = {"trades": [], "cash": 1}
        ratio = adaptive_position_sizing(
            paper, score=90.0, rules={"position_pct": 0.15, "max_position_pct": 0.25}
        )
        self.assertLessEqual(ratio, 0.25 + 1e-9)

    def test_portfolio_max_positions_from_spec(self):
        from core.signal.factors.kelly import calculate_position_for_portfolio

        paper = {"strategy_id": "short_conservative", "trades": [], "cash": 1}
        # 15 个已有评分 → Spec max_positions=15 → 满仓返回 0
        scores = [70.0] * 15
        got = calculate_position_for_portfolio(paper, 80.0, scores, rules={})
        self.assertEqual(got, 0.0)

    def test_nan_score_does_not_crash(self):
        from core.signal.factors.kelly import adaptive_position_sizing

        ratio = adaptive_position_sizing({"trades": []}, score=float("nan"), rules={})
        self.assertGreaterEqual(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)


class TestC2ValidatePaper(unittest.TestCase):
    def test_strips_min_score_on_load(self):
        from core.paper.ledger import load_paper, save_paper, validate_paper

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            raw = {
                "cash": 100000.0,
                "holdings": [],
                "trades": [],
                "rules": {"min_score": 55, "max_positions": 15},
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f)
            data = load_paper(path)
            self.assertNotIn("min_score", data.get("rules") or {})
            self.assertEqual((data.get("rules") or {}).get("max_positions"), 15)

            data["rules"]["min_hold_score"] = 45
            save_paper(data, path)
            with open(path, encoding="utf-8") as f:
                written = json.load(f)
            self.assertNotIn("min_score", written.get("rules") or {})
            self.assertNotIn("min_hold_score", written.get("rules") or {})

            warns = validate_paper({"cash": 1, "rules": {"min_score": 1}}, on_load=True)
            self.assertTrue(any("min_score" in w for w in warns))

    def test_load_save_load_toxic_gone(self):
        from core.paper.ledger import load_paper, save_paper

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cash": 1.0,
                        "holdings": [],
                        "trades": [],
                        "rules": {"min_score": 55, "max_positions": 8},
                    },
                    f,
                )
            p1 = load_paper(path)
            self.assertNotIn("min_score", p1["rules"])
            save_paper(p1, path)
            p2 = load_paper(path)
            self.assertNotIn("min_score", p2["rules"])
            self.assertNotIn("min_hold_score", p2["rules"])
            self.assertEqual(p2["rules"].get("max_positions"), 8)

    def test_rejects_bad_types(self):
        from core.paper.ledger import validate_paper

        with self.assertRaises(ValueError):
            validate_paper({"cash": 1, "holdings": {}}, on_load=True)
        with self.assertRaises(ValueError):
            validate_paper({"cash": 1, "rules": []}, on_load=True)
        with self.assertRaises(ValueError):
            validate_paper({"cash": float("nan")}, on_load=True)
        with self.assertRaises(ValueError):
            validate_paper({"cash": "abc"}, on_load=True)

    def test_drops_invalid_limit_scalars(self):
        from core.paper.ledger import validate_paper

        data = {
            "cash": "1000",
            "rules": {"max_positions": "x", "max_position_pct": float("inf"), "horizon_days": 3},
        }
        warns = validate_paper(data, on_load=False)
        self.assertEqual(data["cash"], 1000.0)
        self.assertNotIn("max_positions", data["rules"])
        self.assertNotIn("max_position_pct", data["rules"])
        self.assertEqual(data["rules"]["horizon_days"], 3)
        self.assertTrue(any("max_positions" in w for w in warns))


class TestC3CycleHygiene(unittest.TestCase):
    def test_resolve_strategy_spec_failure_does_not_merge_params(self):
        from core.paper.cycle import _resolve_strategy_spec

        paper = {"cash": 1, "rules": {"max_positions": 15}, "holdings": []}
        import core.strategy as strategy_mod

        orig = strategy_mod.apply_strategy_to_paper

        def _boom(*_a, **_k):
            raise RuntimeError("forced")

        strategy_mod.apply_strategy_to_paper = _boom  # type: ignore[assignment]
        try:
            _resolve_strategy_spec(paper, "short_conservative")
        finally:
            strategy_mod.apply_strategy_to_paper = orig  # type: ignore[assignment]

        rules = paper.get("rules") or {}
        self.assertNotIn("min_score", rules)
        self.assertNotEqual(rules.get("min_score", None), 72.0)

    def test_apply_success_rules_have_no_min_score(self):
        from core.strategy import apply_strategy_to_paper

        paper = {"cash": 1, "holdings": [], "rules": {}}
        apply_strategy_to_paper(paper, "short_conservative")
        rules = paper.get("rules") or {}
        self.assertNotIn("min_score", rules)
        self.assertNotIn("min_hold_score", rules)
        self.assertIn("max_positions", rules)


if __name__ == "__main__":
    unittest.main()
