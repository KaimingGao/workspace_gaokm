"""Fixed-event nowcast + 一维 Kalman 剩余收益融合。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestNowcastKf(unittest.TestCase):
    def test_remaining_at_tau_matches_eod_helper(self):
        from core.signal.dual_score import eod_remaining_at_tau
        from core.signal.nowcast_kf import remaining_at_tau

        self.assertAlmostEqual(remaining_at_tau(2.0, None), 2.0)
        self.assertAlmostEqual(remaining_at_tau(2.0, 0.0), 2.0)
        mapped = remaining_at_tau(2.0, 1.0)
        self.assertAlmostEqual(mapped, eod_remaining_at_tau(2.0, 1.0))
        self.assertLess(mapped, 2.0)

    def test_kalman_no_obs_keeps_prior(self):
        from core.signal.nowcast_kf import kalman_nowcast

        pack = kalman_nowcast(
            y_eod=1.0,
            y_tau=None,
            realized_eod_to_now=0.0,
            prior_var=1.0,
            obs_var=1.0,
            q_process=0.05,
        )
        self.assertAlmostEqual(pack["predicted_score_nowcast"], 1.0)
        self.assertEqual(pack["nowcast_as_of"], "eod")
        self.assertIsNone(pack["nowcast_K"])

    def test_weak_obs_small_gain(self):
        from core.signal.nowcast_kf import kalman_nowcast

        pack = kalman_nowcast(
            y_eod=1.0,
            y_tau=5.0,
            realized_eod_to_now=0.0,
            prior_var=0.1,
            obs_var=10.0,
            q_process=0.0,
        )
        self.assertLess(pack["nowcast_K"], 0.05)
        self.assertAlmostEqual(pack["predicted_score_nowcast"], 1.0, places=1)

    def test_equal_var_is_midpoint_without_q(self):
        from core.signal.nowcast_kf import kalman_fusion_weights, kalman_nowcast

        we, wt, k, _note = kalman_fusion_weights(
            prior_var=1.0, obs_var=1.0, q_process=0.0
        )
        self.assertAlmostEqual(we, 0.5, places=5)
        self.assertAlmostEqual(wt, 0.5, places=5)
        self.assertAlmostEqual(k, 0.5, places=5)
        pack = kalman_nowcast(
            y_eod=0.0,
            y_tau=2.0,
            realized_eod_to_now=0.0,
            prior_var=1.0,
            obs_var=1.0,
            q_process=0.0,
        )
        self.assertAlmostEqual(pack["predicted_score_nowcast"], 1.0, places=5)

    def test_nowcast_expresses_prev_close_with_gap(self):
        from core.signal.nowcast_kf import (
            compound_pct,
            kalman_fusion_weights,
            remaining_at_tau,
            run_live_nowcast,
        )

        gap, y_eod, y_tau = 1.0, 2.0, 1.0
        pack = run_live_nowcast(
            y_eod=y_eod,
            y_tau=y_tau,
            gap_pct=gap,
            as_of="open",
            taus=["eod", "open"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.0,
        )
        _we, _wt, k, _ = kalman_fusion_weights(
            prior_var=1.0, obs_var=1.0, q_process=0.0
        )
        # 内部在剩余窗滤波，再 compound 抬回昨收（与线性 CC 混权差在 round-6）
        x_rem = (1.0 - k) * remaining_at_tau(y_eod, gap) + k * y_tau
        expect = compound_pct(gap, x_rem)
        self.assertEqual(pack.get("nowcast_vs"), "prev_close")
        self.assertAlmostEqual(pack["predicted_score_nowcast"], expect, delta=1e-3)
        self.assertAlmostEqual(
            pack["predicted_score_nowcast"],
            (1.0 - k) * y_eod + k * compound_pct(gap, y_tau),
            delta=1e-3,
        )
        self.assertAlmostEqual(pack["nowcast_x_prior"], y_eod, places=5)

    def test_apply_tau_nowcast_vs_prev_close(self):
        from core.signal.dual_score import apply_tau_score_fields
        from core.signal.nowcast_kf import compound_pct, kalman_fusion_weights, remaining_at_tau

        item = {"predicted_score": 2.0}
        apply_tau_score_fields(
            item,
            rem_yhat=1.0,
            gap_pct=1.0,
            feats={"gap_pct": 1.0},
            config={
                "dual_score": {
                    "eod_residual_var": 1.0,
                    "nowcast": {"q_process": 0.0},
                }
            },
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        self.assertEqual(item.get("nowcast_vs"), "prev_close")
        _we, _wt, k, _ = kalman_fusion_weights(
            prior_var=1.0, obs_var=1.0, q_process=0.0
        )
        x_rem = (1.0 - k) * remaining_at_tau(2.0, 1.0) + k * 1.0
        expect = compound_pct(1.0, x_rem)
        self.assertAlmostEqual(item["predicted_score_nowcast"], expect, places=4)
        self.assertAlmostEqual(item["nowcast_x_prior"], 2.0, places=4)

    def test_nordhaus_efficient_revisions_near_zero(self):
        from core.signal.nowcast_kf import nordhaus_revision_slope

        priors = [0.0, 0.2, -0.1, 0.5, -0.4, 0.1]
        posts = [p + 0.01 * ((i % 3) - 1) for i, p in enumerate(priors)]
        slope = nordhaus_revision_slope(priors, posts)
        self.assertIsNotNone(slope)
        self.assertLess(abs(slope), 0.2)

    def test_apply_tau_writes_nowcast_does_not_change_eod(self):
        from core.signal.dual_score import apply_tau_score_fields, rank_key_for_item

        item = {"predicted_score": 0.8, "score": 0.8}
        apply_tau_score_fields(
            item,
            rem_yhat=0.2,
            gap_pct=1.0,
            feats={"gap_pct": 1.0},
            config={"dual_score": {"w_mode": "fixed", "w_eod": 0.5, "w_tau": 0.5}},
        )
        self.assertEqual(item["predicted_score"], 0.8)
        self.assertIsNotNone(item.get("predicted_score_nowcast"))
        self.assertTrue(item.get("nowcast_revisions"))
        blend = item["predicted_score_blend"]
        self.assertAlmostEqual(rank_key_for_item(item), blend)

    def test_eod_next_nowcast_still_observes_tau(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 1.2, "score": 1.2}
        apply_tau_score_fields(
            item,
            rem_yhat=0.8,
            gap_pct=2.0,
            feats={"gap_pct": 2.0},
            fuse_intraday=False,
        )
        self.assertEqual(item["nowcast_as_of"], "open")
        self.assertIsNotNone(item.get("nowcast_K"))
        self.assertNotAlmostEqual(item["predicted_score_nowcast"], 1.2, places=3)
        self.assertAlmostEqual(item["nowcast_x_prior"], 1.2, places=4)

    def test_use_as_rank_key_opt_in(self):
        from core.signal.dual_score import apply_tau_score_fields, rank_key_for_item

        item = {"predicted_score": 0.8}
        cfg = {
            "dual_score": {
                "nowcast": {"use_as_rank_key": True, "q_process": 0.0},
                "eod_residual_var": 1.0,
            }
        }
        apply_tau_score_fields(item, rem_yhat=0.2, gap_pct=0.0, feats={}, config=cfg)
        self.assertAlmostEqual(
            rank_key_for_item(item, config=cfg),
            item["predicted_score_nowcast"],
        )

    def test_w_mode_kalman_weights(self):
        from core.signal.dual_score import resolve_fusion_weights

        we, wt, note = resolve_fusion_weights(
            {
                "w_mode": "kalman",
                "eod_residual_var": 1.0,
                "nowcast": {"q_process": 0.0},
            },
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        self.assertAlmostEqual(we + wt, 1.0, places=5)
        self.assertAlmostEqual(we, 0.5, places=5)
        self.assertIn("kalman", note)

    def test_q_process_zero_survives_merge(self):
        from core.signal.dual_score import get_dual_score_cfg
        from core.signal.nowcast_kf import as_process_q, merge_nowcast_cfg

        self.assertEqual(as_process_q(0), 0.0)
        self.assertEqual(as_process_q(0.0), 0.0)
        self.assertAlmostEqual(as_process_q(None), 0.05)
        merged = merge_nowcast_cfg({"q_process": 0.0})
        self.assertEqual(merged["q_process"], 0.0)
        self.assertFalse(merged.get("write_shadow"))
        merged_on = merge_nowcast_cfg({"enabled": True})
        self.assertTrue(merged_on.get("write_shadow"))
        merged_off = merge_nowcast_cfg({"enabled": False, "write_shadow": True})
        self.assertTrue(merged_off.get("write_shadow"))
        cfg = get_dual_score_cfg(
            {"dual_score": {"nowcast": {"q_process": 0.0}}}
        )
        self.assertEqual(cfg["nowcast"]["q_process"], 0.0)

    def test_apply_tau_uses_rem_residual_var_for_nowcast_k(self):
        from core.signal.dual_score import apply_tau_score_fields

        cfg = {
            "dual_score": {
                "w_mode": "fixed",
                "eod_residual_var": 1.0,
                "nowcast": {"q_process": 0.0},
            }
        }
        item_default = {"predicted_score": 1.0}
        apply_tau_score_fields(
            item_default, rem_yhat=3.0, gap_pct=0.0, feats={}, config=cfg
        )
        item_tight = {"predicted_score": 1.0}
        apply_tau_score_fields(
            item_tight,
            rem_yhat=3.0,
            gap_pct=0.0,
            feats={},
            config=cfg,
            rem_model_doc={"oos": {"residual_var": 0.1}},
        )
        self.assertGreater(item_tight["nowcast_K"], item_default["nowcast_K"])
        self.assertAlmostEqual(item_tight["nowcast_K"], 1.0 / 1.1, places=4)

    def test_rank_key_field_opt_in(self):
        from core.signal.dual_score import rank_key_field

        self.assertEqual(rank_key_field(config={"dual_score": {}}), "predicted_score_blend")
        self.assertEqual(
            rank_key_field(
                config={"dual_score": {"nowcast": {"use_as_rank_key": True}}}
            ),
            "predicted_score_nowcast",
        )

    def test_nowcast_shadow_book_ranks_by_kf(self):
        from core.signal.dual_score import build_nowcast_shadow_book

        rows = [
            {"stock_code": "000001", "predicted_score_nowcast": 0.1},
            {"stock_code": "000002", "predicted_score_nowcast": 0.9},
            {"stock_code": "000003"},
        ]
        book, meta = build_nowcast_shadow_book(rows, max_names=2, eod_book=rows[:2])
        self.assertEqual(book[0]["stock_code"], "000002")
        self.assertEqual(meta["rank_key"], "predicted_score_nowcast")
        self.assertEqual(meta["missing_nowcast_count"], 1)
        self.assertIsNone(meta.get("nordhaus_revision_slope"))

    def test_live_path_pits_unreached_minute_tau(self):
        from core.signal.nowcast_kf import live_tau_path, normalize_tau_label

        self.assertEqual(normalize_tau_label("open"), "open")
        self.assertEqual(normalize_tau_label("2026-08-16T09:45:00+08:00"), "09:45")
        self.assertEqual(
            live_tau_path(["eod", "open", "09:45"], "open"),
            ["eod", "open"],
        )
        self.assertEqual(
            live_tau_path(["eod", "open", "09:45"], "09:45"),
            ["eod", "open", "09:45"],
        )
        self.assertEqual(
            live_tau_path(["eod", "open", "09:45", "14:00"], "14:00"),
            ["eod", "open", "14:00"],
        )

    def test_seq_two_point_matches_wrapper(self):
        from core.signal.nowcast_kf import kalman_nowcast, run_live_nowcast

        two = kalman_nowcast(
            y_eod=0.0,
            y_tau=2.0,
            realized_eod_to_now=0.0,
            prior_var=1.0,
            obs_var=1.0,
            q_process=0.0,
        )
        live = run_live_nowcast(
            y_eod=0.0,
            y_tau=2.0,
            gap_pct=0.0,
            as_of="open",
            taus=["eod", "open"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.0,
        )
        self.assertAlmostEqual(
            two["predicted_score_nowcast"], live["predicted_score_nowcast"]
        )
        self.assertAlmostEqual(two["nowcast_K"], live["nowcast_K"])
        self.assertEqual(live["nowcast_path"], ["eod", "open"])
        self.assertEqual(sum(1 for r in live["nowcast_revisions"] if r.get("updated")), 1)

    def test_seq_minute_adds_q_once_per_clock_observes_once(self):
        from core.signal.nowcast_kf import run_live_nowcast

        two = run_live_nowcast(
            y_eod=1.0,
            y_tau=3.0,
            gap_pct=0.0,
            ret_open_to_tau=0.0,
            as_of="open",
            taus=["eod", "open", "09:45"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.05,
        )
        three = run_live_nowcast(
            y_eod=1.0,
            y_tau=3.0,
            gap_pct=0.0,
            ret_open_to_tau=0.0,
            as_of="09:45",
            taus=["eod", "open", "09:45"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.05,
        )
        self.assertEqual(two["nowcast_as_of"], "open")
        self.assertEqual(three["nowcast_as_of"], "09:45")
        self.assertEqual(three["nowcast_path"], ["eod", "open", "09:45"])
        self.assertEqual(sum(1 for r in three["nowcast_revisions"] if r.get("updated")), 1)
        self.assertGreater(three["nowcast_K"], two["nowcast_K"])

    def test_theme_q_boost_increases_gain(self):
        from core.signal.nowcast_kf import adaptive_process_q, run_live_nowcast

        q0, n0 = adaptive_process_q(0.05, theme_day=0)
        q1, n1 = adaptive_process_q(0.05, theme_day=1)
        self.assertEqual(n0, "base")
        self.assertIn("theme", n1)
        self.assertAlmostEqual(q1, 0.1)
        qz, nz = adaptive_process_q(0.0, theme_day=1, gap_pct=5.0)
        self.assertEqual(qz, 0.0)
        self.assertEqual(nz, "q=0")
        quiet = run_live_nowcast(
            y_eod=1.0,
            y_tau=5.0,
            gap_pct=0.0,
            as_of="open",
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.05,
            theme_day=0,
        )
        theme = run_live_nowcast(
            y_eod=1.0,
            y_tau=5.0,
            gap_pct=0.0,
            as_of="open",
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.05,
            theme_day=1,
        )
        self.assertGreater(theme["nowcast_K"], quiet["nowcast_K"])
        self.assertGreater(theme["nowcast_q"], quiet["nowcast_q"])

    def test_rem_obs_var_prefers_theme_bucket(self):
        from core.signal.nowcast_kf import rem_obs_var

        doc = {
            "oos": {
                "residual_var": 1.0,
                "by_theme": {
                    "theme": {"residual_var": 0.2, "n": 20},
                    "normal": {"residual_var": 4.0, "n": 80},
                },
            }
        }
        self.assertAlmostEqual(rem_obs_var(doc, theme_day=1), 0.2)
        self.assertAlmostEqual(rem_obs_var(doc, theme_day=0), 4.0)
        self.assertAlmostEqual(
            rem_obs_var(
                {
                    "oos": {
                        "residual_var": 1.0,
                        "by_tau": {"09:45": {"residual_var": 0.5}},
                    }
                },
                tau="09:45",
            ),
            0.5,
        )

    def test_minute_obs_remapped_to_remaining_window(self):
        from core.signal.nowcast_kf import remaining_at_tau, run_live_nowcast

        pack = run_live_nowcast(
            y_eod=2.0,
            y_tau=2.0,
            gap_pct=0.0,
            ret_open_to_tau=1.0,
            as_of="09:45",
            taus=["eod", "open", "09:45"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}, "horizon_mode": "open_to_close"},
            q_process=0.0,
        )
        z = remaining_at_tau(2.0, 1.0)
        obs = [r for r in pack["nowcast_revisions"] if r.get("updated")]
        self.assertEqual(len(obs), 1)
        self.assertAlmostEqual(obs[0]["yhat"], z)
        self.assertEqual(obs[0]["tau"], "09:45")

    def test_tau_to_close_rem_no_double_remap(self):
        from core.signal.nowcast_kf import run_live_nowcast

        rem = {
            "horizon_mode": "tau_to_close",
            "y_spec": {"tau": "09:45", "formula": "close[T]/price[09:45]-1"},
            "oos": {"residual_var": 1.0},
        }
        pack = run_live_nowcast(
            y_eod=2.0,
            y_tau=0.5,
            gap_pct=0.0,
            ret_open_to_tau=1.0,
            as_of="09:45",
            taus=["eod", "open"],
            prior_var=1.0,
            rem_model_doc=rem,
            q_process=0.0,
            allow_minute=True,
        )
        obs = [r for r in pack["nowcast_revisions"] if r.get("updated")]
        self.assertEqual(pack["nowcast_path"], ["eod", "open", "09:45"])
        self.assertEqual(len(obs), 1)
        self.assertAlmostEqual(obs[0]["yhat"], 0.5)
        self.assertFalse(pack["nowcast_rem_oc"])

    def test_auto_extend_taus_when_minute_asof(self):
        from core.signal.nowcast_kf import ensure_asof_in_taus, run_live_nowcast

        self.assertEqual(
            ensure_asof_in_taus(["eod", "open"], "09:45", allow_minute=True),
            ["eod", "open", "09:45"],
        )
        self.assertEqual(
            ensure_asof_in_taus(["eod", "open"], "09:45", allow_minute=False),
            ["eod", "open"],
        )
        pack = run_live_nowcast(
            y_eod=1.0,
            y_tau=2.0,
            gap_pct=0.0,
            ret_open_to_tau=0.0,
            as_of="09:45",
            taus=["eod", "open"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.0,
            allow_minute=True,
        )
        self.assertEqual(pack["nowcast_as_of"], "09:45")

    def test_apply_tau_aligns_oc_rem_at_minute(self):
        from core.signal.dual_score import apply_tau_score_fields
        from core.signal.nowcast_kf import remaining_at_tau

        item = {"predicted_score": 2.0}
        apply_tau_score_fields(
            item,
            rem_yhat=2.0,
            gap_pct=0.0,
            feats={"ret_open_to_tau": 1.0, "gap_pct": 0.0},
            as_of_tau="09:45",
            y_spec_override={
                "tau": "09:45",
                "formula": "close[T]/price[09:45]-1",
            },
            config={
                "dual_score": {
                    "enable_minute_tau": True,
                    "w_eod": 0.5,
                    "w_tau": 0.5,
                    "nowcast": {"q_process": 0.0, "taus": ["eod", "open"]},
                }
            },
            rem_model_doc={
                "horizon_mode": "open_to_close",
                "oos": {"residual_var": 1.0},
            },
        )
        expect = remaining_at_tau(2.0, 1.0)
        self.assertAlmostEqual(item["predicted_score_tau"], expect)
        self.assertAlmostEqual(item["predicted_score_eod_rem"], expect)
        self.assertEqual(item["nowcast_as_of"], "09:45")
        self.assertEqual(item["nowcast_path"], ["eod", "open", "09:45"])
        self.assertAlmostEqual(item["formula_terms_tau"]["y_tau_raw"], 2.0)

    def test_skip_minute_tau_without_ret(self):
        from core.signal.nowcast_kf import run_live_nowcast

        pack = run_live_nowcast(
            y_eod=1.0,
            y_tau=2.0,
            gap_pct=0.0,
            ret_open_to_tau=None,
            as_of="09:45",
            taus=["eod", "open", "09:45"],
            prior_var=1.0,
            rem_model_doc={"oos": {"residual_var": 1.0}},
            q_process=0.0,
        )
        self.assertEqual(pack["nowcast_path"], ["eod", "open"])
        self.assertEqual(pack["nowcast_as_of"], "open")

    def test_apply_tau_theme_q_and_nordhaus_shadow(self):
        from core.signal.dual_score import apply_tau_score_fields, build_nowcast_shadow_book

        cfg = {
            "dual_score": {
                "nowcast": {"q_process": 0.05, "taus": ["eod", "open"]},
                "eod_residual_var": 1.0,
            }
        }
        rem = {"oos": {"residual_var": 1.0}}
        quiet = apply_tau_score_fields(
            {"predicted_score": 1.0},
            rem_yhat=3.0,
            gap_pct=0.0,
            feats={"theme_day": 0, "gap_pct": 0.0},
            config=cfg,
            rem_model_doc=rem,
        )
        theme = apply_tau_score_fields(
            {"predicted_score": 1.0},
            rem_yhat=3.0,
            gap_pct=0.0,
            feats={"theme_day": 1, "gap_pct": 0.0},
            config=cfg,
            rem_model_doc=rem,
        )
        self.assertGreater(theme["nowcast_K"], quiet["nowcast_K"])
        rows = [
            {
                "stock_code": "000001",
                "predicted_score_nowcast": 0.2,
                "nowcast_x_prior": 0.0,
            },
            {
                "stock_code": "000002",
                "predicted_score_nowcast": 0.4,
                "nowcast_x_prior": 1.0,
            },
            {
                "stock_code": "000003",
                "predicted_score_nowcast": 0.1,
                "nowcast_x_prior": -0.5,
            },
            {
                "stock_code": "000004",
                "predicted_score_nowcast": 0.8,
                "nowcast_x_prior": 0.5,
            },
        ]
        _book, meta = build_nowcast_shadow_book(rows, max_names=4)
        self.assertIsNotNone(meta.get("nordhaus_revision_slope"))

    def test_w_mode_kalman_uses_theme_q(self):
        from core.signal.dual_score import resolve_fusion_weights

        quiet = resolve_fusion_weights(
            {
                "w_mode": "kalman",
                "eod_residual_var": 1.0,
                "nowcast": {"q_process": 0.05},
            },
            feats={"theme_day": 0, "gap_pct": 0.0},
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        theme = resolve_fusion_weights(
            {
                "w_mode": "kalman",
                "eod_residual_var": 1.0,
                "nowcast": {"q_process": 0.05},
            },
            feats={"theme_day": 1, "gap_pct": 0.0},
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        self.assertGreater(theme[1], quiet[1])

    def test_w_mode_variance_falls_back_when_rem_missing(self):
        from core.signal.dual_score import resolve_fusion_weights

        we, wt, note = resolve_fusion_weights(
            {"w_mode": "variance", "w_eod": 0.3, "w_tau": 0.7},
            rem_model_doc=None,
        )
        self.assertAlmostEqual(we, 0.3)
        self.assertAlmostEqual(wt, 0.7)
        self.assertIn("tau_missing", note)

        we2, wt2, note2 = resolve_fusion_weights(
            {"w_mode": "kalman", "w_eod": 0.4, "w_tau": 0.6},
            rem_model_doc={},
        )
        self.assertAlmostEqual(we2, 0.4)
        self.assertAlmostEqual(wt2, 0.6)
        self.assertIn("tau_missing", note2)

    def test_resolve_eod_prior_var_prefers_cluster_rmse(self):
        from core.signal.nowcast_kf import resolve_eod_prior_var

        ve, src = resolve_eod_prior_var(
            cfg_var=1.0,
            prior_var=None,
            cluster_report={"k_selection": {"mean_holdout_rmse": 0.5}},
        )
        self.assertAlmostEqual(ve, 0.25)
        self.assertEqual(src, "cluster_holdout_rmse2")
        ve2, src2 = resolve_eod_prior_var(
            cfg_var=1.0,
            prior_var=2.0,
            cluster_report={"k_selection": {"mean_holdout_rmse": 0.5}},
        )
        self.assertAlmostEqual(ve2, 2.0)
        self.assertEqual(src2, "prior_var")

    def test_cluster_holdout_caches_missing_rmse(self):
        import json
        import os
        import tempfile
        from unittest.mock import patch

        from core.signal import nowcast_kf as kf

        kf._CLUSTER_VAR_CACHE.update({"mtime": None, "var": None, "ready": False})
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"success": True, "note": "no rmse"}, f)
            loads = {"n": 0}
            real_load = json.load

            def _counting_load(*args, **kwargs):
                loads["n"] += 1
                return real_load(*args, **kwargs)

            with patch("core.paths.CLUSTER_LAST_REPORT_PATH", path), patch(
                "json.load", side_effect=_counting_load
            ):
                self.assertIsNone(kf.cluster_holdout_residual_var())
                self.assertIsNone(kf.cluster_holdout_residual_var())
            self.assertEqual(loads["n"], 1)
            self.assertTrue(kf._CLUSTER_VAR_CACHE.get("ready"))
        finally:
            kf._CLUSTER_VAR_CACHE.update({"mtime": None, "var": None, "ready": False})
            try:
                os.remove(path)
            except OSError:
                pass

    def test_apply_tau_records_prior_var_src(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 1.0}
        apply_tau_score_fields(
            item,
            rem_yhat=0.5,
            gap_pct=0.0,
            feats={},
            config={
                "dual_score": {
                    "eod_residual_var": 1.0,
                    "nowcast": {"prior_var": 0.8, "q_process": 0.0},
                }
            },
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        w = item.get("dual_score_weights") or {}
        self.assertAlmostEqual(w.get("eod_prior_var"), 0.8)
        self.assertEqual(w.get("eod_prior_var_src"), "prior_var")


if __name__ == "__main__":
    unittest.main()

