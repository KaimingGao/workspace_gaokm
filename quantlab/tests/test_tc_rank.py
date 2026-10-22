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
            # 全局 μ/σ（非截面）：不依赖 L2
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

    def test_score_codes_z_full_pool_rank_only_allowed(self):
        """L2 全池 μ/σ；名次只落在允许档。"""
        from core.research.day_cube import build_day_cube
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
            "zscore_scope": "cross_section",
            "zscore_universe": "pool",
            "zscore_means": {},
            "zscore_stds": {},
        }
        items = [
            {"stock_code": "A1", "features_tau": {"z_τc_gap_pct": 0.0}, "y_oo_fuse": 0.1, "lake_score_day": "2024-05-01"},
            {"stock_code": "A2", "features_tau": {"z_τc_gap_pct": 10.0}, "y_oo_fuse": 0.2, "lake_score_day": "2024-05-01"},
            {
                "stock_code": "B1",
                "features_tau": {"z_τc_gap_pct": 1000.0},
                "y_tc_rank": 3,
                "y_oo_fuse": 0.3,
                "lake_score_day": "2024-05-01",
            },
        ]
        cube = build_day_cube(
            "2024-05-01",
            {it["stock_code"]: dict(it["features_tau"]) for it in items},
            names=["z_τc_gap_pct"],
            clock="open",
        )
        apply_tc_rank_scores(
            items,
            model_doc={"return_model": ridge},
            score_codes=["A1", "A2"],
            day_cube=cube,
        )
        by = {it["stock_code"]: it for it in items}
        self.assertNotIn("y_tc_rank", by["B1"])
        self.assertEqual(by["B1"]["features_tau"]["z_τc_gap_pct"], 1000.0)
        self.assertEqual(by["B1"]["y_oo_fuse"], 0.3)
        self.assertEqual(by["A2"]["y_tc_rank"], 1)
        self.assertEqual(by["A1"]["y_tc_rank"], 2)
        self.assertEqual(by["A2"]["y_tc_rank_n"], 2)
        self.assertLess(float(by["A2"]["y_tc_rank_score"]), 0.0)
        self.assertEqual(by["A2"]["y_oo_fuse"], 0.2)

    def test_legacy_tier_a_stamp_still_uses_full_pool_z(self):
        """旧 zscore_universe=tier_a 不再缩 z：B 极值把 A2 相对分拉负。"""
        from core.research.day_cube import build_day_cube
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
            "zscore_scope": "cross_section",
            "zscore_universe": "tier_a",
            "train_universe": "tier_a",
            "tier_codes": ["A1", "A2"],
            "zscore_means": {},
            "zscore_stds": {},
        }
        items = [
            {"stock_code": "A1", "features_tau": {"z_τc_gap_pct": 0.0}, "y_oo_fuse": 0.1, "lake_score_day": "2024-05-01"},
            {"stock_code": "A2", "features_tau": {"z_τc_gap_pct": 10.0}, "y_oo_fuse": 0.2, "lake_score_day": "2024-05-01"},
            {
                "stock_code": "B1",
                "features_tau": {"z_τc_gap_pct": 1000.0},
                "y_tc_rank": 3,
                "y_oo_fuse": 0.3,
                "lake_score_day": "2024-05-01",
            },
        ]
        cube = build_day_cube(
            "2024-05-01",
            {it["stock_code"]: dict(it["features_tau"]) for it in items},
            names=["z_τc_gap_pct"],
            clock="open",
        )
        apply_tc_rank_scores(
            items,
            model_doc={"return_model": ridge},
            score_codes=["A1", "A2"],
            day_cube=cube,
        )
        by = {it["stock_code"]: it for it in items}
        self.assertNotIn("y_tc_rank", by["B1"])
        self.assertEqual(by["A2"]["y_tc_rank"], 1)
        self.assertLess(float(by["A2"]["y_tc_rank_score"]), 0.0)
        self.assertEqual(by["A2"]["y_oo_fuse"], 0.2)

    def test_l2_cube_universe_controls_mu_sigma(self):
        """全池 Cube vs A-only Cube：μ/σ 跟 Cube 宇宙，不再进批现算。"""
        from core.research.day_cube import build_day_cube
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
            "zscore_scope": "cross_section",
            "zscore_universe": "pool",
            "zscore_means": {},
            "zscore_stds": {},
        }
        base = [
            {"stock_code": "A1", "features_tau": {"z_τc_gap_pct": 0.0}, "lake_score_day": "2024-05-01"},
            {"stock_code": "A2", "features_tau": {"z_τc_gap_pct": 10.0}, "lake_score_day": "2024-05-01"},
            {"stock_code": "B1", "features_tau": {"z_τc_gap_pct": 1000.0}, "lake_score_day": "2024-05-01"},
        ]
        cube_full = build_day_cube(
            "2024-05-01",
            {it["stock_code"]: dict(it["features_tau"]) for it in base},
            names=["z_τc_gap_pct"],
            clock="open",
        )
        mixed = [dict(x) for x in base]
        apply_tc_rank_scores(
            mixed,
            model_doc={"return_model": ridge},
            score_codes=["A1", "A2"],
            day_cube=cube_full,
        )
        self.assertLess(float(mixed[1]["y_tc_rank_score"]), 0.0)

        cube_a = build_day_cube(
            "2024-05-01",
            {
                "A1": {"z_τc_gap_pct": 0.0},
                "A2": {"z_τc_gap_pct": 10.0},
            },
            names=["z_τc_gap_pct"],
            clock="open",
        )
        locked = [dict(x) for x in base]
        apply_tc_rank_scores(
            locked,
            model_doc={"return_model": ridge},
            score_codes=["A1", "A2"],
            day_cube=cube_a,
        )
        by = {it["stock_code"]: it for it in locked}
        self.assertNotIn("y_tc_rank", by["B1"])
        self.assertEqual(by["A2"]["y_tc_rank"], 1)
        self.assertGreater(float(by["A2"]["y_tc_rank_score"]), 0.0)

    def test_ridge_keeps_small_std_drops_constant(self):
        """不按原始标准差剔列；真常数仍剔除。"""
        import numpy as np

        from core.research.oo_rank_lambdarank import _fit_ridge_baseline

        names = [
            "z_τc_gap_pct",
            "raw_atc_GAP",
            "raw_atc_PVMA5",
            "raw_atc_PVSTD5",
            "raw_atc_RET_OT",
        ]
        rng = np.random.default_rng(0)
        days = []
        for i in range(12):
            n = 8
            gap = rng.normal(0.0, 1.0, size=n)
            atc = rng.normal(0.0, 0.4, size=n)
            vol = rng.normal(100.0, 20.0, size=n)
            vol_sd = rng.normal(80.0, 15.0, size=n)
            const = np.full(n, 0.0)
            ys = (0.3 * gap + 0.2 * atc + rng.normal(0.0, 0.05, size=n)).tolist()
            days.append(
                {
                    "date": f"2024-03-{i + 1:02d}",
                    "codes": [f"c{j}" for j in range(n)],
                    "ys": ys,
                    "names": list(names),
                    "X": np.column_stack([gap, atc, vol, vol_sd, const]),
                }
            )
        kept = _fit_ridge_baseline(
            days, feature_names=names, cross_section_zscore=False
        )
        self.assertTrue(kept.get("success"), kept)
        active = set(kept.get("active_features") or [])
        self.assertIn("z_τc_gap_pct", active)
        self.assertIn("raw_atc_GAP", active)
        self.assertIn("raw_atc_PVMA5", active)
        self.assertIn("raw_atc_PVSTD5", active)
        self.assertNotIn("raw_atc_RET_OT", active)

    def test_load_research_does_not_fall_back_to_live(self):
        """研究套缺失时不得读执行套。"""
        import json
        import os
        import tempfile
        from unittest.mock import patch

        from core.research.holdout import research_model_path
        from core.research.tc_rank import load_tc_rank_model

        live_doc = {
            "success": True,
            "schema": "tc_rank_v1",
            "return_model": {
                "backend": "ridge",
                "solver": "ridge",
                "target": "tc_rank",
                "coefficients": {"z_τc_gap_pct": 1.0},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "tc_rank_model.json")
            with open(live, "w", encoding="utf-8") as f:
                json.dump(live_doc, f)
            with patch(
                "core.research.tc_rank.tc_rank_model_path",
                return_value=live,
            ):
                self.assertIsNone(load_tc_rank_model(prefer_research=True))
                self.assertIsNotNone(load_tc_rank_model(prefer_research=False))
                research = research_model_path(live)
                os.makedirs(os.path.dirname(research), exist_ok=True)
                research_doc = dict(live_doc)
                research_doc["return_model"] = dict(
                    live_doc["return_model"],
                    coefficients={"z_τc_gap_pct": 2.0},
                )
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(research_doc, f)
                loaded = load_tc_rank_model(prefer_research=True)
                self.assertAlmostEqual(
                    float(
                        ((loaded or {}).get("return_model") or {})
                        .get("coefficients", {})
                        .get("z_τc_gap_pct")
                    ),
                    2.0,
                )

if __name__ == "__main__":
    unittest.main()
