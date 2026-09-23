"""四窗口标签：ŷ_oo PIT、ŷ_τc 几何、ĉ。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars():
    return [
        {"date": "2026-01-01", "open": 10.0, "close": 10.5},
        {"date": "2026-01-02", "open": 10.6, "close": 10.8},
        {"date": "2026-01-03", "open": 11.0, "close": 10.9},
        {"date": "2026-01-04", "open": 11.2, "close": 11.5},
    ]


class ForwardOoTests(unittest.TestCase):
    def test_forward_oo_uses_open(self):
        from core.signal.yhat_windows import forward_oo_pct

        bars = _bars()
        y = forward_oo_pct(bars, 1, 1)
        self.assertAlmostEqual(y, (11.0 / 10.6 - 1.0) * 100.0, places=6)

    def test_pit_window_excludes_t_close(self):
        from core.signal.yhat_windows import pit_oo_window_quote

        bars = _bars()
        window, quote = pit_oo_window_quote(bars, 2, 0)
        self.assertEqual(len(window), 2)
        self.assertNotIn(bars[2], window)
        self.assertAlmostEqual(quote["price_raw"], 11.0)
        self.assertAlmostEqual(quote["change_raw"], (11.0 / 10.8 - 1.0) * 100.0, places=4)


class PcGeometryTests(unittest.TestCase):
    def test_y_r_pct_is_close_over_price(self):
        from core.research.tau_panel import y_r_pct

        self.assertAlmostEqual(y_r_pct(10.0, 10.5), (10.5 / 10.0 - 1.0) * 100.0, places=6)

    def test_pick_y_τc_inverts_legacy_y_r(self):
        from core.signal.yhat_windows import invert_price_over_close, pick_y_τc

        raw = 5.0
        self.assertAlmostEqual(pick_y_τc({"y_r": raw}), invert_price_over_close(raw), places=6)
        self.assertAlmostEqual(pick_y_τc({"y_to": 1.2, "y_r": 5.0}), 1.2, places=6)
        self.assertAlmostEqual(pick_y_τc({"y_pc": 1.2, "y_r": 5.0}), 1.2, places=6)
        self.assertAlmostEqual(pick_y_τc({"y_τc": 1.2, "y_r": 5.0}), 1.2, places=6)
        self.assertAlmostEqual(pick_y_τc({"y_tc": 1.2, "y_r": 5.0}), 1.2, places=6)

    def test_write_y_τc_stamps_primary_only(self):
        from core.signal.yhat_windows import pick_y_τc, write_y_τc

        dest = {}
        write_y_τc(dest, 1.25)
        self.assertAlmostEqual(dest["y_τc"], 1.25, places=6)
        self.assertAlmostEqual(dest["predicted_score_τc"], 1.25, places=6)
        self.assertNotIn("y_tc", dest)
        self.assertNotIn("y_to", dest)
        self.assertNotIn("y_pc", dest)
        self.assertAlmostEqual(pick_y_τc(dest), 1.25, places=6)
        self.assertAlmostEqual(pick_y_τc({"y_tc": 1.25}), 1.25, places=6)

    def test_stamp_omits_y_to_y_pc(self):
        from core.signal.yhat_windows import stamp_window_scores

        out = stamp_window_scores({"y_τc": 1.1, "y_oo": 0.5, "y_oc": 0.4})
        self.assertAlmostEqual(out["y_τc"], 1.1, places=6)
        self.assertNotIn("y_to", out)
        self.assertNotIn("y_pc", out)

    def test_write_y_tc_hat_without_formula_stamps_new_τc(self):
        from core.research.tc_ridge import write_y_tc_hat
        from core.signal.yhat_windows import pick_y_τc

        dest = {}
        write_y_tc_hat(dest, -0.8)
        self.assertAlmostEqual(dest["y_r"], -0.8, places=6)
        self.assertAlmostEqual(dest["y_τc"], -0.8, places=6)
        self.assertAlmostEqual(pick_y_τc(dest), -0.8, places=6)
        low = str((dest.get("y_spec_τc") or {}).get("formula") or "").replace(" ", "").lower()
        self.assertLess(low.find("close"), low.find("price"))

    def test_pick_y_τc_uses_new_formula_without_invert(self):
        from core.signal.yhat_windows import pick_y_τc

        item = {
            "y_r": 1.5,
            "y_spec_r": {"formula": "close[T]/price[τ]-1"},
        }
        self.assertAlmostEqual(pick_y_τc(item), 1.5, places=6)

    def test_write_y_tc_hat_legacy_formula_inverts(self):
        from core.research.tc_ridge import write_y_tc_hat
        from core.signal.yhat_windows import invert_price_over_close, pick_y_τc

        dest = {}
        write_y_tc_hat(dest, 1.0, formula="price[τ]/close[T]-1")
        self.assertAlmostEqual(dest["y_r"], 1.0, places=6)
        self.assertAlmostEqual(pick_y_τc(dest), invert_price_over_close(1.0), places=6)

    def test_pick_y_τc_respects_close_over_price_target(self):
        from core.signal.yhat_windows import pick_y_τc

        self.assertAlmostEqual(
            pick_y_τc({"y_r": -0.6, "target": "close_over_price_tau"}),
            -0.6,
            places=6,
        )

    def test_scores_from_item_keeps_negative_y_τc(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "y_tau_oc": 1.2,
                "y_τc": -0.7,
                "predicted_score_r": -0.7,
                "y_r": -0.7,
                "y_spec_r": {"formula": "close[T]/price[τ]-1"},
            }
        )
        self.assertAlmostEqual(sc.get("y_τc"), -0.7, places=6)
        self.assertAlmostEqual(sc.get("y_r"), -0.7, places=6)


class CloseHatTests(unittest.TestCase):
    def test_estimate_ignores_y_τc_and_fusion_weights(self):
        from core.t0.close_band import estimate_close_px

        est = estimate_close_px(
            {"y_tau": 1.0, "y_τc": 2.0},
            open_px=100.0,
            prev_close=100.0,
            price_tau=50.0,
            cfg={"fusion_w_τc": 1.0, "residual_w_oc": 0.0},
        )
        self.assertTrue(est["ok"])
        self.assertAlmostEqual(est["close_px"], 102.0, places=4)
        self.assertEqual(est.get("c_hat_source"), "y_oc")
        from core.signal.yhat_windows import remaining_oc

        rem = remaining_oc(1.0, open_px=100.0, price_tau=50.0)
        rem_band = remaining_oc(2.0, open_px=100.0, price_tau=50.0)
        self.assertAlmostEqual(est.get("y_τc"), 2.0, places=6)
        self.assertAlmostEqual(est.get("y_τc_ridge"), 2.0, places=6)
        self.assertEqual(est.get("y_τc_source"), "ridge")
        self.assertAlmostEqual(est.get("remaining_oc"), rem, places=6)
        self.assertAlmostEqual(est.get("r_hat"), rem_band, places=6)
        self.assertAlmostEqual(est.get("r_hat"), (102.0 / 50.0 - 1.0) * 100.0, places=6)

    def test_estimate_does_not_fuse_remaining_into_hat(self):
        from core.t0.close_band import estimate_close_px
        from core.signal.yhat_windows import remaining_oc

        open_px, price_tau = 100.0, 101.0
        y_oc, y_τc = 2.0, 0.9
        rem = remaining_oc(y_oc, open_px=open_px, price_tau=price_tau)
        rem_band = remaining_oc(4.0, open_px=open_px, price_tau=price_tau)
        est = estimate_close_px(
            {"y_tau": y_oc, "y_τc": y_τc},
            open_px=open_px,
            prev_close=100.0,
            price_tau=price_tau,
            cfg={"fusion_w_τc": 0.5, "residual_w_oc": 0.5},
        )
        self.assertTrue(est["ok"])
        self.assertEqual(est.get("c_hat_source"), "y_oc")
        self.assertAlmostEqual(est["close_px"], open_px * (1.0 + 4.0 / 100.0), places=4)
        self.assertAlmostEqual(est["remaining_oc"], rem, places=5)
        self.assertAlmostEqual(est["r_hat"], rem_band, places=5)
        self.assertAlmostEqual(est["r_hat"], (est["close_px"] / price_tau - 1.0) * 100.0, places=5)
        self.assertAlmostEqual(est["y_τc"], y_τc, places=6)
        self.assertAlmostEqual(est.get("y_τc_ridge"), y_τc, places=6)
        self.assertEqual(est.get("y_τc_source"), "ridge")

    def test_estimate_falls_back_to_oc(self):
        from core.t0.close_band import estimate_close_px

        est = estimate_close_px({"y_tau": 1.0}, open_px=100.0, prev_close=90.0)
        self.assertAlmostEqual(est["close_px"], 102.0, places=4)
        self.assertEqual(est.get("c_hat_source"), "y_oc")

    def test_r_hat_matches_c_tau_over_price(self):
        from core.t0.close_band import estimate_close_px, r_hat_from_c_tau_px
        from core.signal.yhat_windows import remaining_oc

        open_px, price_tau = 32.5, 32.27
        y_oc = -0.913934
        cfg = {"t0_y_oc_target_scale": 10.0}
        est = estimate_close_px(
            {"y_tau": y_oc, "y_τc": -0.2089},
            open_px=open_px,
            prev_close=open_px,
            price_tau=price_tau,
            cfg=cfg,
        )
        self.assertTrue(est["ok"])
        self.assertAlmostEqual(est["close_px"], 32.5 * (1.0 - 0.0913934), places=2)
        self.assertAlmostEqual(est["y_oc_target"], -9.13934, places=5)
        rem_raw = remaining_oc(y_oc, open_px=open_px, price_tau=price_tau)
        self.assertAlmostEqual(est["remaining_oc"], rem_raw, places=4)
        self.assertAlmostEqual(
            est["r_hat"],
            r_hat_from_c_tau_px(est["close_px"], price_tau),
            places=4,
        )
        self.assertLess(est["r_hat"], -5.0)
        self.assertGreater(est["remaining_oc"], -1.0)


class StampRemainingYtcTests(unittest.TestCase):
    def test_stamp_remaining_goes_negative_when_price_above_hat(self):
        from core.signal.yhat_windows import remaining_oc, stamp_remaining_y_τc

        dest = {
            "y_oc": 0.5,
            "y_τc": 1.4,
            "y_r": 1.4,
            "features_tau": {"ret_open_to_tau": 2.0},
        }
        rem = remaining_oc(0.5, 2.0)
        self.assertLess(rem, 0.0)
        got = stamp_remaining_y_τc(dest)
        self.assertAlmostEqual(got, rem, places=6)
        self.assertAlmostEqual(dest["y_τc"], 1.4, places=6)
        self.assertAlmostEqual(dest["y_τc_ridge"], 1.4, places=6)
        self.assertEqual(dest["y_τc_source"], "ridge")
        self.assertAlmostEqual(dest["r_hat"], rem, places=6)
        self.assertAlmostEqual(dest["remaining_oc"], rem, places=6)
        self.assertAlmostEqual(dest["y_r"], 1.4, places=6)

    def test_stamp_restores_ridge_when_ytc_was_remaining(self):
        from core.signal.yhat_windows import remaining_oc, stamp_remaining_y_τc

        rem = remaining_oc(0.5, 2.0)
        dest = {
            "y_oc": 0.5,
            "y_τc": rem,
            "y_τc_ridge": 1.4,
            "y_τc_source": "remaining_oc",
            "features_tau": {"ret_open_to_tau": 2.0},
        }
        stamp_remaining_y_τc(dest)
        self.assertAlmostEqual(dest["y_τc"], 1.4, places=6)
        self.assertEqual(dest["y_τc_source"], "ridge")
        self.assertAlmostEqual(dest["r_hat"], rem, places=6)

    def test_scores_from_item_keeps_ridge_and_stamps_remaining_as_r_hat(self):
        from core.t0.score_policy import scores_from_item
        from core.signal.yhat_windows import remaining_oc

        sc = scores_from_item(
            {
                "y_tau_oc": 0.5,
                "y_τc": 1.4,
                "predicted_score_r": 1.4,
                "y_r": 1.4,
                "features_tau": {"ret_open_to_tau": 2.0},
            }
        )
        rem = remaining_oc(0.5, 2.0)
        self.assertAlmostEqual(sc.get("y_τc"), 1.4, places=6)
        self.assertAlmostEqual(sc.get("y_τc_ridge"), 1.4, places=6)
        self.assertEqual(sc.get("y_τc_source"), "ridge")
        self.assertAlmostEqual(sc.get("r_hat"), rem, places=6)
        self.assertAlmostEqual(sc.get("remaining_oc"), rem, places=6)
        self.assertLess(sc.get("r_hat"), 0.0)
        self.assertAlmostEqual(sc.get("y_r"), 1.4, places=6)


class StampPrimaryTests(unittest.TestCase):
    def test_apply_co_writes_y_co(self):
        from core.signal.dual_score.co import apply_co_score_fields

        item = apply_co_score_fields({}, co_yhat=0.8)
        self.assertAlmostEqual(item["y_co"], 0.8)
        self.assertAlmostEqual(item["predicted_score_co"], 0.8)
        self.assertNotIn("y_on", item)

    def test_ranking_compounds_oc_with_co(self):
        from core.signal.yhat_windows import oc_with_co, ranking_pct

        y_oc, y_co = 1.0, 1.0
        right = oc_with_co(y_oc, y_co, 1.0)
        self.assertAlmostEqual(right, ((1.01 * 1.01) - 1.0) * 100.0, places=6)
        self.assertAlmostEqual(oc_with_co(1.0, 2.0, 0.0), 1.0)
        item = {"y_oo": 2.0, "y_oc": 1.0, "y_co": 1.0}
        self.assertAlmostEqual(ranking_pct(item), 1.5, places=6)
        self.assertAlmostEqual(
            ranking_pct(item, w_oo=0.5, w_oc=0.5, w_co=1.0),
            0.5 * 2.0 + 0.5 * right,
            places=6,
        )
        self.assertAlmostEqual(
            ranking_pct(item, w_oo=0.0, w_oc=1.0, w_co=1.0),
            right,
            places=6,
        )

    def test_formula_ranking_expands_geometric_compound(self):
        from core.signal.yhat_windows import FORMULA_RANKING

        self.assertEqual(
            FORMULA_RANKING,
            "w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)",
        )

    def test_pick_y_oo_ignores_ranking_alias_y_fuse(self):
        from core.signal.yhat_windows import pick_y_oo, ranking_pct

        item = {"predicted_score": 2.0, "y_oc": 0.4, "y_fuse": 1.2}
        self.assertAlmostEqual(pick_y_oo(item), 2.0, places=6)
        self.assertAlmostEqual(ranking_pct(item), 1.2, places=6)
        only_fuse = {"y_fuse": 1.2, "y_oc": 0.4}
        self.assertIsNone(pick_y_oo(only_fuse))
        self.assertAlmostEqual(ranking_pct(only_fuse), 0.4, places=6)

    def test_stamp_window_scores_applies_remaining_ranking(self):
        from core.signal.yhat_windows import stamp_window_scores

        fused = stamp_window_scores({"y_oo": 2.0, "y_oc": 2.0})
        self.assertAlmostEqual(fused["ranking"], 2.0, places=6)
        rem = stamp_window_scores(
            {"y_oo": 2.0, "y_oc": 2.0, "day_open": 100.0, "price_tau": 100.5}
        )
        rot = (100.5 / 100.0 - 1.0) * 100.0
        self.assertAlmostEqual(rem["ranking"], 2.0 - rot, places=6)

    def test_remaining_ranking_deducts_open_to_price_tau(self):
        from core.signal.yhat_windows import ranking_pct, remaining_ranking_pct

        fused = ranking_pct({"y_oo": 2.0, "y_oc": 2.0})
        self.assertAlmostEqual(fused, 2.0, places=6)
        self.assertAlmostEqual(
            remaining_ranking_pct(fused, open_px=100.0, price_tau=100.0),
            2.0,
            places=6,
        )
        rot = (100.5 / 100.0 - 1.0) * 100.0
        rem = remaining_ranking_pct(fused, open_px=100.0, price_tau=100.5)
        self.assertAlmostEqual(rem, fused - rot, places=6)
        self.assertLess(rem, fused)
        self.assertAlmostEqual(remaining_ranking_pct(fused), fused, places=6)

    def test_realized_ranking_is_next_open_minus_tau_over_open(self):
        from core.signal.yhat_windows import realized_ranking_pct

        open_t, tau, nxt = 100.0, 100.5, 102.0
        realized_oo = (nxt / open_t - 1.0) * 100.0
        got = realized_ranking_pct(realized_oo, open_px=open_t, price_tau=tau)
        self.assertAlmostEqual(got, (nxt - tau) / open_t * 100.0, places=6)
        self.assertIsNone(realized_ranking_pct(None, open_px=open_t, price_tau=tau))
        self.assertAlmostEqual(
            realized_ranking_pct(realized_oo, open_px=open_t),
            realized_oo,
            places=6,
        )
        self.assertAlmostEqual(
            realized_ranking_pct(realized_oo, open_px=open_t, price_tau=open_t),
            realized_oo,
            places=6,
        )


class ResidualFusionTests(unittest.TestCase):
    def test_remaining_maps_oc_to_price_tau(self):
        from core.signal.yhat_windows import remaining_oc, ret_open_to_tau_pct

        rot = ret_open_to_tau_pct(100.0, 101.0)
        self.assertAlmostEqual(rot, 1.0, places=6)
        rem = remaining_oc(2.0, open_px=100.0, price_tau=101.0)
        expect = ((1.02 / 1.01) - 1.0) * 100.0
        self.assertAlmostEqual(rem, expect, places=5)

    def test_unmapped_oc_not_mixed_with_tc(self):
        from core.signal.yhat_windows import residual_pct

        self.assertAlmostEqual(
            residual_pct({"y_τc": 1.1, "y_oc": 4.0}),
            1.1,
            places=6,
        )

    def test_inv_var_prefers_tighter_head(self):
        from core.signal.yhat_windows import residual_inv_var_weights, residual_pct

        item = {
            "y_τc": 1.0,
            "y_oc": 3.0,
            "ret_open_to_tau": 0.0,
            "residual_var_τc": 0.25,
            "residual_var_oc": 1.0,
        }
        w_tc, w_oc = residual_inv_var_weights(item, 0.0)
        self.assertAlmostEqual(w_tc, 0.8, places=6)
        self.assertAlmostEqual(w_oc, 0.2, places=6)
        fused = residual_pct(item, cfg={"residual_w_mode": "inv_var"})
        self.assertAlmostEqual(fused, 0.8 * 1.0 + 0.2 * 3.0, places=6)

    def test_residual_weights_do_not_leak_ranking_w_oc(self):
        from core.signal.yhat_windows import residual_weights_from_cfg

        w_tc, w_oc = residual_weights_from_cfg({"fusion_w_oc": 0.9, "fusion_w_τc": 0.5})
        self.assertAlmostEqual(w_tc, 0.5, places=6)
        self.assertAlmostEqual(w_oc, 0.5, places=6)
        w_tc, w_oc = residual_weights_from_cfg({"fusion_w_τc": 0.7, "residual_w_oc": 0.3})
        self.assertAlmostEqual(w_tc, 0.7, places=6)
        self.assertAlmostEqual(w_oc, 0.3, places=6)


if __name__ == "__main__":
    unittest.main()
