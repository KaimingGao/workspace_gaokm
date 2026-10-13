"""ŷ_τc_rank 研究头：空池拒绝，不落盘。"""

import unittest


class TestTcRank(unittest.TestCase):
    def test_empty_bars_rejected(self):
        from core.research.tc_rank import fit_tc_rank_report

        out = fit_tc_rank_report([])
        self.assertFalse(out.get("success"))
        self.assertEqual(out.get("task"), "tc_rank")
        self.assertIn("rows_insufficient", str(out.get("error") or ""))

    def test_apply_scores_leave_fuse_and_oo_rank(self):
        """名次只写 y_tc_rank；不改 fuse / y_oo_rank / 买序。"""
        from core.research.tc_rank import apply_tc_rank_scores

        ridge = {
            "success": True,
            "backend": "ridge",
            "solver": "ridge",
            "target": "tc_rank",
            "horizon_mode": "tc_rank",
            "intercept": 0.0,
            "coefficients": {"z_τc_gap_pct": 1.0},
            "feature_zscore": True,
            "zscore_means": {"z_τc_gap_pct": 0.0},
            "zscore_stds": {"z_τc_gap_pct": 1.0},
        }
        items = [
            {
                "stock_code": "000002",
                "y_oo_fuse": 0.02,
                "y_oo_rank": 1,
                "features_tau": {"z_τc_gap_pct": 0.1},
            },
            {
                "stock_code": "000001",
                "y_oo_fuse": 0.01,
                "y_oo_rank": 2,
                "features_tau": {"z_τc_gap_pct": 1.5},
            },
            {"stock_code": "000003", "y_oo_fuse": 0.03, "y_oo_rank": 3},
        ]
        apply_tc_rank_scores(items, model_doc={"return_model": ridge})
        by = {it["stock_code"]: it for it in items}
        self.assertEqual(by["000001"]["y_tc_rank"], 1)
        self.assertEqual(by["000002"]["y_tc_rank"], 2)
        self.assertEqual(by["000001"]["y_tc_rank_n"], 2)
        self.assertNotIn("y_tc_rank", by["000003"])
        self.assertEqual(by["000001"]["y_oo_rank"], 2)
        self.assertEqual(by["000002"]["y_oo_rank"], 1)
        self.assertAlmostEqual(by["000001"]["y_oo_fuse"], 0.01)
        self.assertEqual(
            (by["000001"].get("formula_terms_tc_rank") or {}).get("head"),
            "tc_rank",
        )


if __name__ == "__main__":
    unittest.main()
