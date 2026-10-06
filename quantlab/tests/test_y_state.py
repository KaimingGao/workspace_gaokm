"""Y(τ) 高维状态：装配、校验、买入闸。"""

from __future__ import annotations

import unittest


class TestYState(unittest.TestCase):
    def test_conflict_check(self):
        from core.signal.y_state import build_y_state, CHECK_CONFLICT

        st = build_y_state(
            {
                "predicted_score": -1.0,
                "predicted_score_eod": -1.0,
                "predicted_score_eod_rem": -1.0,
                "predicted_score_tau": 0.8,
                "predicted_score_blend": -0.1,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
            },
            config={"y_state": {"eps_sign": 0.05, "disagree_warn": 99}},
        )
        self.assertEqual(st["check"], CHECK_CONFLICT)
        self.assertTrue(st["sign_conflict"])
        self.assertAlmostEqual(st["disagree"], 1.8, places=5)
        self.assertEqual(st["eod_trust"], 0.0)

    def test_ok_when_aligned(self):
        from core.signal.y_state import build_y_state, CHECK_OK

        st = build_y_state(
            {
                "predicted_score": 0.4,
                "predicted_score_eod_rem": 0.4,
                "predicted_score_tau": 0.3,
                "predicted_score_blend": 0.35,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
            },
            config={"y_state": {"disagree_warn": 0.5, "sigma_warn": 99}},
        )
        self.assertEqual(st["check"], CHECK_OK)
        self.assertAlmostEqual(st["eod_trust"], 1.0)

    def test_missing_tau(self):
        from core.signal.y_state import build_y_state, CHECK_MISSING_TAU, buy_passes_y_check

        item = {
            "predicted_score": 1.0,
            "predicted_score_eod_rem": 1.0,
            "predicted_score_tau": None,
            "dual_score_window": "intraday",
        }
        st = build_y_state(item)
        self.assertEqual(st["check"], CHECK_MISSING_TAU)
        item["y_check"] = st["check"]
        ok, reason = buy_passes_y_check(
            item, config={"y_state": {"filter_buys": True, "block_on": ["missing_tau"]}}
        )
        self.assertFalse(ok)
        self.assertIn("missing_tau", reason or "")

    def test_filter_buys_off(self):
        from core.signal.y_state import buy_passes_y_check

        ok, _ = buy_passes_y_check(
            {"y_check": "conflict", "dual_score_window": "intraday"},
            config={"y_state": {"filter_buys": False}},
        )
        self.assertTrue(ok)

    def test_eod_next_skips_hard_conflict(self):
        from core.signal.y_state import build_y_state, CHECK_OK

        st = build_y_state(
            {
                "predicted_score_eod_rem": -1.0,
                "predicted_score_tau": 1.0,
                "dual_score_head": "blend",
                "dual_score_window": "eod_next",
            },
            config={"y_state": {"sigma_warn": 99}},
        )
        self.assertEqual(st["check"], CHECK_OK)

    def test_stamp_fields(self):
        from core.signal.y_state import stamp_y_state

        item = {
            "predicted_score": 0.2,
            "predicted_score_eod_rem": 0.2,
            "predicted_score_tau": 0.15,
            "predicted_score_blend": 0.175,
            "dual_score_head": "blend",
            "dual_score_window": "intraday",
        }
        stamp_y_state(item, config={"y_state": {"disagree_warn": 0.5, "sigma_warn": 99}})
        self.assertEqual(item.get("y_check"), "ok")
        self.assertIn("y_state", item)
        self.assertIsNotNone(item.get("y_disagree"))

    def test_sigma_from_nowcast_P(self):
        from core.signal.y_state import build_y_state

        st = build_y_state(
            {
                "predicted_score_eod_rem": 0.2,
                "predicted_score_tau": 0.15,
                "predicted_score_blend": 0.175,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
                "nowcast_P": 2.25,  # variance → σ=1.5
            },
            config={"y_state": {"disagree_warn": 99, "sigma_warn": 99}},
        )
        self.assertAlmostEqual(st["sigma_path"], 1.5, places=5)
        self.assertEqual(st["sigma_src"], "nowcast_P")

    def test_tau_to_close_oc_adj(self):
        from core.signal.y_state import build_y_state, resolve_tau_to_close_segment

        tc, src = resolve_tau_to_close_segment(
            y_tau=2.0,
            eod_rem=1.0,
            ret_open_to_tau=1.0,
            rem_is_open_to_close=True,
        )
        # (1.02/1.01 - 1)*100 ≈ 0.990
        self.assertEqual(src, "tau_oc_adj")
        self.assertAlmostEqual(tc, (1.02 / 1.01 - 1.0) * 100.0, places=4)

        st = build_y_state(
            {
                "predicted_score_eod_rem": 0.5,
                "predicted_score_tau": 2.0,
                "predicted_score_blend": 1.0,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
                "features_tau": {"ret_open_to_tau": 1.0, "gap_pct": 0.5},
                "formula_terms_tau": {"rem_oc": True},
            },
            config={"y_state": {"disagree_warn": 99, "sigma_warn": 99}},
        )
        self.assertIsNotNone(st["segments"]["tau_to_close"])
        self.assertEqual(st["segments"]["tau_to_close_src"], "tau_oc_adj")

    def test_scale_weights_by_trust(self):
        from core.signal.y_state import scale_weights_by_oo_trust

        w, meta = scale_weights_by_oo_trust(
            {"A": 20.0, "B": 10.0},
            {"A": 0.5, "B": 1.0},
            config={"y_state": {"scale_weights": True}},
        )
        self.assertTrue(meta.get("applied"))
        self.assertAlmostEqual(w["A"], 10.0, places=4)
        self.assertAlmostEqual(w["B"], 10.0, places=4)
        self.assertEqual(meta.get("n_scaled"), 1)

        w2, meta2 = scale_weights_by_oo_trust(
            {"A": 20.0},
            {"A": 0.5},
            config={"y_state": {"scale_weights": False}},
        )
        self.assertFalse(meta2.get("applied"))
        self.assertAlmostEqual(w2["A"], 20.0, places=4)

    def test_optimize_applies_eod_trust(self):
        from core.portfolio_optimize import optimize_weights

        cands = [
            {
                "stock_code": "600519",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_eod_rem": 2.0,
                "predicted_score_tau": 1.8,
                "predicted_score_blend": 1.9,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
                "sector": "白酒",
                "y_check": "low_conf",
                "eod_trust": 0.5,
            },
            {
                "stock_code": "600036",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_eod": 2.0,
                "predicted_score_eod_rem": 2.0,
                "predicted_score_tau": 1.9,
                "predicted_score_blend": 1.95,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
                "sector": "银行",
                "y_check": "ok",
                "eod_trust": 1.0,
            },
        ]
        out = optimize_weights(
            cands,
            max_position_pct=40.0,
            max_sector_pct=50.0,
            max_positions=5,
            min_score=0.0,
            weight_mode="score_budget",
            apply_market_vol=False,
            apply_regime_scale=False,
            sector_map={"600519": "白酒", "600036": "银行"},
        )
        self.assertTrue(out.get("ok"))
        yt = out.get("y_trust_scale") or {}
        self.assertTrue(yt.get("applied"))
        wa = float((out.get("weights_pct") or {}).get("600519") or 0)
        wb = float((out.get("weights_pct") or {}).get("600036") or 0)
        # 同分预算下 trust=0.5 的票目标仓应约为另一只的一半
        if wb > 0.05:
            self.assertLess(wa + 1e-6, wb)

    def test_book_fields_expose_y(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score": 0.5,
                "predicted_score_eod_rem": 0.5,
                "predicted_score_tau": -0.8,
                "predicted_score_blend": -0.15,
                "dual_score_head": "blend",
                "dual_score_window": "intraday",
            }
        )
        self.assertIn("y_check", out)
        self.assertEqual(out.get("y_check"), "conflict")
        self.assertIn("y_tau_to_close", out)

if __name__ == "__main__":
    unittest.main()
