"""ExecutionSpec resolve · StrategySpec overlays.t0。"""

from __future__ import annotations

import unittest


class TestExecutionResolve(unittest.TestCase):
    def test_paper_direction_fallback_dual_y(self):
        from core.execution import resolve_effective_execution

        bundle = resolve_effective_execution(strategy="short_conservative", channel="paper")
        self.assertTrue(bundle["ok"])
        self.assertEqual(bundle["t0"]["direction"], "dual_y")
        self.assertEqual(bundle["t0_sources"].get("direction"), "runtime_fallback")
        self.assertEqual(bundle["t0"]["fill_mode"], "trigger")
        self.assertAlmostEqual(float(bundle["t0"]["t0_ratio"]), 1.0)
        self.assertTrue(bundle["effective_hash"])
        self.assertIn("dual_y", bundle["summary"])

    def test_backtest_path_fallback(self):
        from core.execution import resolve_effective_execution

        no_m = resolve_effective_execution(strategy="short_conservative", channel="backtest", has_minute=False)
        self.assertEqual(no_m["t0"]["path_mode"], "first_touch")
        with_m = resolve_effective_execution(strategy="short_conservative", channel="backtest", has_minute=True)
        self.assertEqual(with_m["t0"]["path_mode"], "first_touch")

    def test_paper_override_wins(self):
        from core.execution import resolve_effective_execution

        paper = {"strategy_id": "short_conservative", "rules": {"t0": {"t0_ratio": 0.25, "direction": "sell_then_buy"}}}
        bundle = resolve_effective_execution(paper=paper, channel="paper")
        self.assertAlmostEqual(float(bundle["t0"]["t0_ratio"]), 1.0)
        # 旧选向已下线，一律收敛 dual_y
        self.assertEqual(bundle["t0"]["direction"], "dual_y")
        self.assertEqual(bundle["t0_sources"].get("t0_ratio"), "paper")

    def test_request_path_enter_migrates_to_hl(self):
        """旧 y_path_enter overlay 迁到 y_hl_enter，入场闸生效。"""
        from core.execution import resolve_t0_rules, strip_execution_meta
        from core.t0.close_band import close_band_enter_skip_reason

        paper = {
            "strategy_id": "short_conservative",
            "rules": {
                "t0": {
                    "y_path_enter": 0.01,
                }
            },
        }
        raw = resolve_t0_rules(
            paper=paper,
            rules={
                "y_path_enter": 0.2,
                "y_tau_enter": 0.01,
                "y_enter_alt_enabled": False,
            },
            channel="backtest",
            has_minute=True,
        )
        cfg = strip_execution_meta(raw)
        self.assertNotIn("y_path_enter", cfg)
        self.assertAlmostEqual(float(cfg["y_hl_enter"]), 0.2)
        self.assertAlmostEqual(float(cfg["y_hl_enter_buy_then_sell"]), 0.2)
        skip = close_band_enter_skip_reason(
            {"y_tau": 0.54, "y_path": 0.18},
            cfg,
            direction="buy_then_sell",
            r_pct=-0.067,
        )
        self.assertIsNotNone(skip)
        self.assertIn("y_hl", skip)

    def test_request_explicit_hl_enter_kept(self):
        from core.execution import resolve_t0_rules, strip_execution_meta

        cfg = strip_execution_meta(
            resolve_t0_rules(
                rules={
                    "y_hl_enter": 0.2,
                    "y_hl_enter_buy_then_sell": 0.05,
                },
                channel="backtest",
                has_minute=True,
            )
        )
        self.assertNotIn("y_path_enter", cfg)
        self.assertAlmostEqual(float(cfg["y_hl_enter"]), 0.2)
        self.assertAlmostEqual(float(cfg["y_hl_enter_buy_then_sell"]), 0.05)

    def test_validate_patch_migrates_path_enter(self):
        from core.execution import validate_execution_patch

        ok, norm, errs = validate_execution_patch({"t0": {"y_path_enter": 0.2}})
        self.assertTrue(ok, errs)
        self.assertNotIn("y_path_enter", norm["t0"])
        self.assertAlmostEqual(float(norm["t0"]["y_hl_enter"]), 0.2)
        self.assertAlmostEqual(float(norm["t0"]["y_hl_enter_buy_then_sell"]), 0.2)
        self.assertAlmostEqual(float(norm["t0"]["y_hl_enter_sell_then_buy"]), 0.2)
        from core.execution import resolve_t0_rules, strip_execution_meta

        raw = resolve_t0_rules(
            strategy="short_conservative",
            rules={"direction": "buy_then_sell", "path_mode": "adverse"},
            channel="backtest",
            has_minute=True,
        )
        cfg = strip_execution_meta(raw)
        self.assertEqual(cfg["direction"], "dual_y")
        # 研究回测强制 first_touch（日线路径已下线）
        self.assertEqual(cfg["path_mode"], "first_touch")
        self.assertEqual(raw["_execution_meta"]["t0_sources"]["direction"], "request→dual_y")
        self.assertIn("first_touch", str(raw["_execution_meta"].get("t0_sources", {}).get("path_mode") or ""))

    def test_strategy_spec_includes_execution(self):
        from core.strategy import get_strategy_spec

        spec = get_strategy_spec("short_conservative")
        self.assertEqual(spec["version"], "1.2.0")
        self.assertIn("execution", spec)
        t0 = (spec["execution"].get("overlays") or {}).get("t0") or {}
        self.assertTrue(t0.get("enabled"))
        self.assertAlmostEqual(float(t0.get("t0_ratio")), 0.35)

    def test_conservative_t0_ratio(self):
        from core.strategy import get_strategy_spec

        spec = get_strategy_spec("short_conservative")
        t0 = (spec["execution"].get("overlays") or {}).get("t0") or {}
        self.assertAlmostEqual(float(t0.get("t0_ratio")), 0.35)

    def test_apply_strategy_writes_t0(self):
        from core.strategy import apply_strategy_to_paper

        paper = {"rules": {}, "cash": 100000, "cost_model": "simple_cn"}
        apply_strategy_to_paper(paper, "short_conservative")
        self.assertIn("t0", paper["rules"])
        self.assertAlmostEqual(float(paper["rules"]["t0"]["t0_ratio"]), 0.35)
        self.assertEqual(paper.get("execution_version"), "1.0.0")
        self.assertEqual(paper["rules"].get("execution_mode"), "next_open")
        self.assertTrue(paper["rules"]["t0"].get("enabled"))

    def test_public_view(self):
        from core.execution import execution_public_view, resolve_effective_execution

        view = execution_public_view(resolve_effective_execution(strategy="short_conservative"))
        self.assertTrue(view["ok"])
        self.assertIn("direction", view["t0"])
        self.assertIn("must_cover_same_day", view["t0"])
        self.assertTrue(view["t0"]["must_cover_same_day"])
        self.assertNotIn("note", view["t0"])

    def test_reset_overlay_restores_t0_enabled(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            reset_paper_execution_overlay,
            resolve_effective_execution,
        )

        paper = {"strategy_id": "short_conservative", "rules": {}, "cash": 100000}
        apply_execution_patch_to_paper(paper, {"t0": {"enabled": False}, "coupling": {}})
        self.assertFalse(paper["rules"]["t0"]["enabled"])
        reset_paper_execution_overlay(paper)
        self.assertTrue(paper["rules"]["t0"]["enabled"])
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertTrue(view["t0"]["enabled"])

    def test_public_view_must_cover_after_patch(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch(
            {"t0": {"must_cover_same_day": True}, "coupling": {}}
        )
        self.assertTrue(ok, errs)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        apply_execution_patch_to_paper(paper, norm)
        view = execution_public_view(resolve_effective_execution(paper=paper, channel="paper"))
        self.assertTrue(view["t0"]["must_cover_same_day"])

    def test_public_view_keeps_residual_weights(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "fusion_w_tc": 0.8,
                    "fusion_w_τc": 0.8,
                    "residual_w_oc": 0.2,
                    "residual_w_mode": "fixed",
                }
            }
        )
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(float(norm["t0"]["fusion_w_tc"]), 0.8)
        self.assertAlmostEqual(float(norm["t0"]["residual_w_oc"]), 0.2)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        apply_execution_patch_to_paper(paper, norm)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["fusion_w_tc"]), 0.8)
        self.assertAlmostEqual(float(view["t0"]["fusion_w_τc"]), 0.8)
        self.assertAlmostEqual(float(view["t0"]["residual_w_oc"]), 0.2)
        self.assertEqual(view["t0"]["residual_w_mode"], "fixed")

    def test_public_view_drops_y_tc_strong(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )
        from core.t0.config import load_t0_rules

        ok, norm, errs = validate_execution_patch({"t0": {"y_tc_strong": 0.3}})
        self.assertTrue(ok, errs)
        self.assertNotIn("y_tc_strong", (norm.get("t0") or {}))
        paper = {
            "strategy_id": "short_conservative",
            "rules": {"t0": {"y_tc_validate": False, "y_tc_strong": 0.5}},
        }
        ok2, patch, errs2 = validate_execution_patch({"t0": {"y_t30_strong": 0.0}})
        self.assertTrue(ok2, errs2)
        applied = apply_execution_patch_to_paper(paper, patch)
        self.assertTrue(applied.get("ok"), applied)
        t0 = (paper.get("rules") or {}).get("t0") or {}
        self.assertNotIn("y_tc_validate", t0)
        self.assertNotIn("y_tc_strong", t0)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertNotIn("y_tc_strong", view["t0"])
        self.assertNotIn("y_tc_strong", load_t0_rules(t0))

    def test_public_view_keeps_y_t30_strong(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch({"t0": {"y_t30_strong": 0.2}})
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(float(norm["t0"]["y_t30_strong"]), 0.2)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied.get("ok"), applied)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["y_t30_strong"]), 0.2)

    def test_public_view_keeps_y_tw_strong(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch({"t0": {"y_tw_strong": 1.5}})
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(float(norm["t0"]["y_tw_strong"]), 1.5)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied.get("ok"), applied)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["y_tw_strong"]), 1.5)

    def test_public_view_keeps_y_t60_strong(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch({"t0": {"y_t60_strong": 0.2}})
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(float(norm["t0"]["y_t60_strong"]), 0.2)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied.get("ok"), applied)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["y_t60_strong"]), 0.2)

    def test_public_view_keeps_y_t90_strong(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch({"t0": {"y_t90_strong": 0.2}})
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(float(norm["t0"]["y_t90_strong"]), 0.2)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied.get("ok"), applied)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["y_t90_strong"]), 0.2)

    def test_public_view_keeps_y_t30_t60_enter(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "y_t30_enter": 0.4,
                    "y_t30_enter_alt": 0.2,
                    "y_t60_enter": 0.6,
                    "y_t60_enter_alt": 0.3,
                    "y_t90_enter": 0.7,
                    "y_t90_enter_alt": 0.4,
                }
            }
        )
        self.assertTrue(ok, errs)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied.get("ok"), applied)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertAlmostEqual(float(view["t0"]["y_t30_enter"]), 0.4)
        self.assertAlmostEqual(float(view["t0"]["y_t30_enter_alt"]), 0.2)
        self.assertAlmostEqual(float(view["t0"]["y_t60_enter"]), 0.6)
        self.assertAlmostEqual(float(view["t0"]["y_t60_enter_alt"]), 0.3)
        self.assertAlmostEqual(float(view["t0"]["y_t90_enter"]), 0.7)
        self.assertAlmostEqual(float(view["t0"]["y_t90_enter_alt"]), 0.4)

    def test_validate_patch_drops_y_tau_map(self):
        from core.execution import validate_execution_patch

        ok, norm, errs = validate_execution_patch(
            {"t0": {"y_tau_map": "trend", "y_score_source": "compute"}}
        )
        self.assertTrue(ok, errs)
        self.assertNotIn("y_tau_map", norm["t0"])
        self.assertEqual(norm["t0"]["y_score_source"], "compute")

    def test_validate_patch_allows_pm_chase_risk_keys(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_public_view,
            resolve_effective_execution,
            validate_execution_patch,
        )
        from core.t0.config import load_t0_rules

        defaults = load_t0_rules()
        self.assertEqual(defaults["t0_pm_degrade"], "13:00")
        self.assertEqual(defaults["t0_pm_degrade_buy_then_sell"], "13:00")
        self.assertEqual(defaults["t0_pm_degrade_sell_then_buy"], "13:00")
        self.assertEqual(defaults["t0_pm_chase_interval_min"], 5)
        self.assertEqual(defaults["t0_pm_chase_interval_min_sell_then_buy"], 5)
        self.assertEqual(defaults["t0_pm_chase_interval_min_buy_then_sell"], 5)
        self.assertAlmostEqual(defaults["t0_stop_pct_buy_then_sell"], 1.2)
        self.assertEqual(defaults["t0_stop_arm_bars"], 1)
        self.assertTrue(defaults["t0_stop_on_close"])
        self.assertAlmostEqual(defaults["t0_giveback_pct_buy_then_sell"], 0.6)
        self.assertAlmostEqual(defaults["t0_giveback_pct_sell_then_buy"], 0.6)
        self.assertAlmostEqual(defaults["t0_giveback_arm_pct"], 0.4)
        self.assertNotIn("y_block_tau_nowcast_sign", defaults)
        self.assertNotIn("t0_adverse_stop_pct", defaults)
        self.assertNotIn("t0_time_stop", defaults)

        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "t0_pm_degrade_sell_then_buy": "13:00",
                    "t0_pm_degrade_buy_then_sell": "14:00",
                    "t0_stop_pct_buy_then_sell": 1.2,
                    "t0_stop_arm_bars": 2,
                    "t0_stop_on_close": True,
                    "t0_pm_chase_interval_min": 10,
                }
            }
        )
        self.assertTrue(ok, errs)
        self.assertEqual(norm["t0"].get("t0_pm_degrade_buy_then_sell"), "14:00")
        self.assertAlmostEqual(float(norm["t0"]["t0_stop_pct_buy_then_sell"]), 1.2)
        self.assertEqual(norm["t0"]["t0_pm_chase_interval_min"], 10)
        paper = {"strategy_id": "short_conservative", "rules": {}}
        apply_execution_patch_to_paper(paper, norm)
        view = execution_public_view(
            resolve_effective_execution(paper=paper, channel="paper")
        )
        self.assertEqual(view["t0"]["t0_pm_degrade"], "14:00")
        self.assertEqual(view["t0"].get("t0_pm_degrade_buy_then_sell"), "14:00")
        self.assertAlmostEqual(float(view["t0"]["t0_stop_pct_buy_then_sell"]), 1.2)
        self.assertEqual(view["t0"]["t0_pm_chase_interval_min"], 10)
        self.assertNotIn("t0_adverse_stop_pct", view["t0"])
        self.assertNotIn("y_block_tau_nowcast_sign", view["t0"])

        ok_dead, norm_dead, errs_dead = validate_execution_patch(
            {
                "t0": {
                    "buy_trigger_pct_buy_then_sell": 0.7,
                    "sell_trigger_pct_sell_then_buy": 2.4,
                    "y_tau_entry_price_mult_buy_then_sell": 5.0,
                    "y_tau_entry_price_mult_sell_then_buy": 5.0,
                    "y_tau_entry_price_skip_buy_then_sell": True,
                    "y_tau_entry_price_bias_buy_then_sell": 0.5,
                    "y_tau_require_for_leg1": True,
                    "t0_pm_degrade_buy_then_sell": "14:00",
                }
            }
        )
        self.assertTrue(ok_dead, errs_dead)
        self.assertNotIn("buy_trigger_pct_buy_then_sell", norm_dead.get("t0") or {})
        self.assertNotIn("sell_trigger_pct_sell_then_buy", norm_dead.get("t0") or {})
        self.assertNotIn("y_tau_entry_price_mult_buy_then_sell", norm_dead.get("t0") or {})
        self.assertNotIn("y_tau_entry_price_mult_sell_then_buy", norm_dead.get("t0") or {})
        self.assertNotIn("y_tau_entry_price_skip_buy_then_sell", norm_dead.get("t0") or {})
        self.assertNotIn("y_tau_entry_price_bias_buy_then_sell", norm_dead.get("t0") or {})
        self.assertNotIn("y_tau_require_for_leg1", norm_dead.get("t0") or {})
        self.assertEqual(norm_dead["t0"].get("t0_pm_degrade_buy_then_sell"), "14:00")

    def test_validate_patch_accepts_price_bias_keys(self):
        from core.execution import validate_execution_patch

        ok, norm, errs = validate_execution_patch(
            {
                "t0": {
                    "y_tau_exit_price_bias_buy_then_sell": 1.0,
                    "y_tau_exit_price_bias_sell_then_buy": -1.0,
                    "t0_pm_chase_cap_leg1_buy_then_sell": True,
                }
            }
        )
        self.assertTrue(ok, errs)
        t0 = norm["t0"]
        self.assertAlmostEqual(float(t0["y_tau_exit_price_bias_buy_then_sell"]), 1.0)
        self.assertAlmostEqual(float(t0["y_tau_exit_price_bias_sell_then_buy"]), -1.0)
        self.assertTrue(t0["t0_pm_chase_cap_leg1_buy_then_sell"])

    def test_t0_backtest_request_defaults_match_product(self):
        from core.t0.config import load_t0_rules
        from web.schemas.paper import T0BacktestRequest

        req = T0BacktestRequest()
        d = load_t0_rules()
        self.assertEqual(req.lookback, 10)
        self.assertAlmostEqual(float(d["y_tau_exit_price_mult_buy_then_sell"]), 1.0)
        self.assertAlmostEqual(float(d["y_tau_exit_price_mult_sell_then_buy"]), 1.0)

    def test_validate_patch_drops_y_nowcast_oc_gate(self):
        from core.execution import validate_execution_patch

        ok, norm, errs = validate_execution_patch(
            {"t0": {"y_nowcast_oc_gate": False, "y_use_path": False}}
        )
        self.assertTrue(ok, errs)
        self.assertNotIn("y_nowcast_oc_gate", norm["t0"])
        self.assertNotIn("y_use_path", norm["t0"])
        self.assertNotIn("y_use_hl", norm["t0"])

    def test_rules_summary_omits_dropped_dual_y_gates(self):
        from quant.research.t0_backtest import _rules_summary

        out = _rules_summary(
            {
                "direction": "dual_y",
                "y_nowcast_oc_gate": False,
                "y_nc_strong": 6.0,
                "y_ratio_boost_cap": 2.0,
                "y_t30_enter_alt": 0.2,
                "y_τ30_enter_alt": 0.2,
                "y_t60_enter_alt": 0.3,
                "y_τ60_enter_alt": 0.3,
                "y_t90_enter_alt": 0.4,
                "y_τ90_enter_alt": 0.4,
            }
        )
        self.assertNotIn("y_nowcast_oc_gate", out)
        self.assertNotIn("y_nc_strong", out)
        self.assertNotIn("y_ratio_boost_cap", out)
        self.assertAlmostEqual(float(out["y_t30_enter_alt"]), 0.2)
        self.assertAlmostEqual(float(out["y_τ30_enter_alt"]), 0.2)
        self.assertAlmostEqual(float(out["y_t60_enter_alt"]), 0.3)
        self.assertAlmostEqual(float(out["y_τ60_enter_alt"]), 0.3)
        self.assertAlmostEqual(float(out["y_t90_enter_alt"]), 0.4)
        self.assertAlmostEqual(float(out["y_τ90_enter_alt"]), 0.4)

    def test_coerce_cfg_bool_and_dropped_oc_gate(self):
        from core.execution import resolve_t0_rules
        from core.t0.config import coerce_cfg_bool, load_t0_rules

        self.assertFalse(coerce_cfg_bool("false"))
        self.assertNotIn("y_nowcast_oc_gate", load_t0_rules({"y_nowcast_oc_gate": "false"}))
        resolved = resolve_t0_rules(
            paper={
                "strategy_id": "short_conservative",
                "rules": {"t0": {"y_nowcast_oc_gate": False}},
            },
            rules={"y_tau_enter": 0.02},
            channel="backtest",
            has_minute=True,
        )
        self.assertNotIn("y_nowcast_oc_gate", resolved)

    def test_validate_and_apply_patch(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            resolve_effective_execution,
            validate_execution_patch,
        )

        ok, norm, errs = validate_execution_patch(
            {
                "t0": {"t0_ratio": 0.3, "direction": "sell_then_buy"},
                "coupling": {"t0_vs_stance": "skip_if_avoid"},
            }
        )
        self.assertTrue(ok, errs)
        self.assertAlmostEqual(norm["t0"]["t0_ratio"], 1.0)
        self.assertEqual(norm["coupling"]["t0_vs_stance"], "skip_if_avoid")

        paper = {"strategy_id": "short_conservative", "rules": {}}
        applied = apply_execution_patch_to_paper(paper, norm)
        self.assertTrue(applied["ok"])
        bundle = resolve_effective_execution(paper=paper, channel="paper")
        self.assertEqual(bundle["t0"]["direction"], "dual_y")
        self.assertEqual(norm["t0"]["direction"], "dual_y")
        self.assertEqual(bundle["coupling"]["t0_vs_stance"], "skip_if_avoid")
        self.assertTrue(paper.get("t0_rules_locked"))

    def test_stance_coupling(self):
        from core.execution import stance_allows_t0

        self.assertTrue(stance_allows_t0("independent", "avoid")[0])
        self.assertFalse(stance_allows_t0("skip_if_avoid", "avoid")[0])
        self.assertTrue(stance_allows_t0("skip_if_avoid", "probe")[0])
        self.assertTrue(stance_allows_t0("only_if_hold", "buy_light")[0])
        self.assertFalse(stance_allows_t0("only_if_hold", "avoid")[0])

    def test_diff_against_strategy(self):
        from core.execution import (
            apply_execution_patch_to_paper,
            execution_diff_against_strategy,
        )

        paper = {"strategy_id": "short_conservative", "rules": {}}
        apply_execution_patch_to_paper(paper, {"t0": {"fill_mode": "mid"}})
        self.assertEqual(paper["rules"]["t0"]["fill_mode"], "mid")
        diff = execution_diff_against_strategy(paper)
        self.assertTrue(diff["changed"])
        paths = {c["path"] for c in diff["t0_changes"]}
        self.assertIn("fill_mode", paths)

    def test_coupling_skip_on_holdings(self):
        from core.t0.rules import simulate_t0_on_holdings

        paper = {
            "cash": 100000,
            "holdings": [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 100}
            ],
            "rules": {},
        }
        bar = {
            "date": "2024-01-02",
            "open": 100,
            "high": 105,
            "low": 95,
            "close": 102,
            "prev_close": 99,
        }
        out = simulate_t0_on_holdings(
            paper,
            bars_by_code={"600519": bar},
            rules={"direction": "sell_then_buy"},
            dry_run=True,
            stance_by_code={"600519": "avoid"},
            coupling={"t0_vs_stance": "skip_if_avoid"},
        )
        self.assertEqual(out.get("coupling_skip_count"), 1)
        self.assertTrue(out["results"][0].get("coupling_skip"))


if __name__ == "__main__":
    unittest.main()
