"""ŷ 几何：剩余窗映射、方差、ŷ_oo 先验。Kalman / nowcast 已退役。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestYhatGeom(unittest.TestCase):
    def test_remaining_at_tau_matches_eod_helper(self):
        from core.signal.dual_score import eod_remaining_at_tau
        from core.signal.yhat_geom import remaining_at_tau

        self.assertAlmostEqual(remaining_at_tau(2.0, None), 2.0)
        self.assertAlmostEqual(remaining_at_tau(2.0, 0.0), 2.0)
        mapped = remaining_at_tau(2.0, 1.0)
        self.assertAlmostEqual(mapped, eod_remaining_at_tau(2.0, 1.0))
        self.assertLess(mapped, 2.0)

    def test_compound_pct(self):
        from core.signal.yhat_geom import compound_pct

        self.assertAlmostEqual(compound_pct(1.0, 1.0), ((1.01 * 1.01) - 1.0) * 100.0)
        self.assertIsNone(compound_pct(None, None))

    def test_align_rem_yhat_oc_at_minute(self):
        from core.signal.yhat_geom import align_rem_yhat_to_clock, remaining_at_tau

        oc = {"horizon_mode": "open_to_close"}
        expect = remaining_at_tau(2.0, 1.0)
        self.assertAlmostEqual(
            align_rem_yhat_to_clock(
                2.0, rem_model_doc=oc, ret_open_to_tau=1.0, clock="09:45"
            ),
            expect,
        )
        self.assertAlmostEqual(
            align_rem_yhat_to_clock(
                2.0, rem_model_doc=oc, ret_open_to_tau=1.0, clock="open"
            ),
            2.0,
        )

    def test_align_rem_tau_to_close_no_double_remap(self):
        from core.signal.yhat_geom import align_rem_yhat_to_clock, rem_label_is_open_to_close

        rem = {
            "horizon_mode": "tau_to_close",
            "y_spec": {"tau": "09:45", "formula": "close[T]/price[09:45]-1"},
        }
        self.assertFalse(rem_label_is_open_to_close(rem))
        self.assertAlmostEqual(
            align_rem_yhat_to_clock(
                0.5, rem_model_doc=rem, ret_open_to_tau=1.0, clock="09:45"
            ),
            0.5,
        )

    def test_rem_obs_var_prefers_theme_bucket(self):
        from core.signal.yhat_geom import rem_obs_var

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

    def test_resolve_oo_prior_var_prefers_cluster_rmse(self):
        from core.signal.yhat_geom import resolve_eod_prior_var, resolve_oo_prior_var

        ve, src = resolve_oo_prior_var(
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
        import tempfile
        from unittest.mock import patch

        from core.signal import yhat_geom as yg

        yg._CLUSTER_VAR_CACHE.update({"mtime": None, "var": None, "ready": False})
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
                self.assertIsNone(yg.cluster_holdout_residual_var())
                self.assertIsNone(yg.cluster_holdout_residual_var())
            self.assertEqual(loads["n"], 1)
            self.assertTrue(yg._CLUSTER_VAR_CACHE.get("ready"))
        finally:
            yg._CLUSTER_VAR_CACHE.update({"mtime": None, "var": None, "ready": False})
            try:
                os.remove(path)
            except OSError:
                pass

    def test_apply_tau_writes_y_oo_not_nowcast(self):
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
        self.assertAlmostEqual(item.get("y_oo"), 0.8)
        self.assertAlmostEqual(item.get("predicted_score_oo"), 0.8)
        self.assertNotIn("predicted_score_nowcast", item)
        self.assertNotIn("y_nowcast", item)
        blend = item["predicted_score_blend"]
        self.assertAlmostEqual(rank_key_for_item(item), blend)

    def test_apply_tau_aligns_oc_rem_at_minute(self):
        from core.signal.dual_score import apply_tau_score_fields
        from core.signal.yhat_geom import remaining_at_tau

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
                }
            },
            rem_model_doc={
                "horizon_mode": "open_to_close",
                "oos": {"residual_var": 1.0},
            },
        )
        expect = remaining_at_tau(2.0, 1.0)
        self.assertNotIn("predicted_score_tau", item)
        self.assertAlmostEqual(item["y_tau"], expect)
        self.assertAlmostEqual(item["predicted_score_eod_rem"], expect)
        self.assertAlmostEqual(item["formula_terms_tau"]["y_tau_raw"], 2.0)
        self.assertNotIn("nowcast_as_of", item)

    def test_w_mode_kalman_falls_back_to_fixed(self):
        from core.signal.dual_score import resolve_fusion_weights

        we, wt, note = resolve_fusion_weights(
            {"w_mode": "kalman", "w_eod": 0.4, "w_tau": 0.6},
            rem_model_doc={},
        )
        self.assertAlmostEqual(we, 0.4)
        self.assertAlmostEqual(wt, 0.6)
        self.assertEqual(note, "fixed")

    def test_w_mode_variance_falls_back_when_rem_missing(self):
        from core.signal.dual_score import resolve_fusion_weights

        we, wt, note = resolve_fusion_weights(
            {"w_mode": "variance", "w_eod": 0.3, "w_tau": 0.7},
            rem_model_doc=None,
        )
        self.assertAlmostEqual(we, 0.3)
        self.assertAlmostEqual(wt, 0.7)
        self.assertIn("tau_missing", note)

    def test_apply_tau_records_prior_var_src(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 1.0}
        apply_tau_score_fields(
            item,
            rem_yhat=0.5,
            gap_pct=0.0,
            feats={},
            config={"dual_score": {"eod_residual_var": 1.0}},
            rem_model_doc={"oos": {"residual_var": 1.0}},
        )
        w = item.get("dual_score_weights") or {}
        self.assertIn(w.get("eod_prior_var_src"), ("eod_residual_var", "cluster_holdout_rmse2", "default"))
        self.assertEqual(w.get("w_oo"), w.get("w_eod"))


if __name__ == "__main__":
    unittest.main()
