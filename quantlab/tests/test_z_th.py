"""z_τh_* canon / 裸名 / z_τc_* 别名。"""

from __future__ import annotations

import unittest


class ZThAliasTests(unittest.TestCase):
    def test_stamp_and_feature_get_roundtrip(self):
        from core.signal.factors.z_th import (
            Z_TH_FIT_FEATURES,
            Z_TH_PREFIX,
            feature_get,
            stamp_z_th_aliases,
            to_legacy_key,
            to_z_th_key,
        )

        self.assertTrue(all(k.startswith(Z_TH_PREFIX) for k in Z_TH_FIT_FEATURES))
        self.assertIn("z_τh_gap_pct", Z_TH_FIT_FEATURES)
        self.assertNotIn("z_τh_ret_last_5m", Z_TH_FIT_FEATURES)

        row = {"gap_pct": 1.25, "theme_day": 1.0}
        stamp_z_th_aliases(row)
        self.assertEqual(row["z_τh_gap_pct"], 1.25)
        self.assertEqual(row["z_τh_theme_day"], 1.0)
        self.assertEqual(feature_get(row, "z_τh_gap_pct"), 1.25)
        self.assertEqual(feature_get({"gap_pct": 0.5}, "z_τh_gap_pct"), 0.5)
        self.assertEqual(feature_get({"z_τh_gap_pct": 0.7}, "gap_pct"), 0.7)
        self.assertEqual(feature_get({"z_τc_gap_pct": 0.9}, "z_τh_gap_pct"), 0.9)
        self.assertEqual(to_z_th_key("gap_pct"), "z_τh_gap_pct")
        self.assertEqual(to_z_th_key("z_τc_gap_pct"), "z_τh_gap_pct")
        self.assertEqual(to_legacy_key("z_τh_gap_pct"), "gap_pct")

        from_tc = {"z_τc_gap_pct": 2.0}
        stamp_z_th_aliases(from_tc)
        self.assertEqual(from_tc["z_τh_gap_pct"], 2.0)
        self.assertEqual(from_tc["gap_pct"], 2.0)

        # 裸名优先：覆盖陈旧 z_τh_*
        conflict = {"gap_pct": 1.0, "z_τh_gap_pct": 9.0}
        stamp_z_th_aliases(conflict)
        self.assertEqual(conflict["z_τh_gap_pct"], 1.0)
        self.assertEqual(conflict["gap_pct"], 1.0)
        # z_τc 次之：覆盖陈旧 z_τh_*
        conflict2 = {"z_τc_gap_pct": 2.0, "z_τh_gap_pct": 9.0}
        stamp_z_th_aliases(conflict2)
        self.assertEqual(conflict2["z_τh_gap_pct"], 2.0)
        self.assertEqual(conflict2["gap_pct"], 2.0)

    def test_compact_feature_get_resolves_z_th(self):
        from core.research.horizon_prob import predict_p_up_rows
        from core.research.tc_ridge_compact import feature_get as compact_get
        from core.signal.factors.z_th import feature_get, to_z_th_key

        self.assertEqual(compact_get({"gap_pct": 1.2}, "z_τh_gap_pct"), 1.2)
        self.assertEqual(compact_get({"z_τc_gap_pct": 0.8}, "z_τh_gap_pct"), 0.8)
        # 旧裸名 / z_τc_* 系数 ← 仅有 z_τh_* 的行
        self.assertEqual(compact_get({"z_τh_gap_pct": 3.0}, "gap_pct"), 3.0)
        self.assertEqual(compact_get({"z_τh_gap_pct": 3.0}, "z_τc_gap_pct"), 3.0)
        # raw_* 勿包成 z_τh_raw_*
        self.assertEqual(to_z_th_key("raw_ath_GAP"), "raw_ath_GAP")
        self.assertEqual(feature_get({"raw_ath_GAP": 3.0}, "raw_ath_GAP"), 3.0)
        self.assertIsNone(feature_get({"gap_pct": 1.0}, "raw_ath_GAP"))

        old_fit = {
            "coefficients": {"gap_pct": 1.0},
            "intercept": 0.0,
            "active_features": ["gap_pct"],
            "zscore_means": {},
            "zscore_stds": {},
        }
        preds = predict_p_up_rows(old_fit, [{"z_τh_gap_pct": 2.0}])
        self.assertEqual(len(preds), 1)
        self.assertIsNotNone(preds[0])
        # sigmoid(2) ≈ 0.8808
        self.assertAlmostEqual(float(preds[0]), 0.880797, places=4)

    def test_oc_shape_drop_matches_compact(self):
        """z_th 硬编码集合须与 tc_ridge_compact 同源（避免 research↔factors 环）。"""
        from core.research.tc_ridge_compact import TAU_HORIZON_DROP_OC_SHAPE
        from core.signal.factors import z_th

        self.assertEqual(z_th._OC_SHAPE_DROP, TAU_HORIZON_DROP_OC_SHAPE)

    def test_horizon_fit_keys_are_z_th_union_ath(self):
        from core.research.horizon_ridge import _horizon_ridge_config
        from core.research.tc_ridge_compact import TAU_HORIZON_DROP_OC_SHAPE

        for head in ("t30", "t45", "t60", "t75", "t90"):
            cfg = _horizon_ridge_config(head)
            self.assertEqual(cfg["schema"], f"{head}_ridge_v4")
            zf = list(cfg["z_features"])
            self.assertTrue(zf)
            self.assertEqual(
                cfg["feature_pack_formula"],
                "z_τh_* ∪ raw_ath_* ∪ z_τc path ∪ raw_atc 5/20",
            )
            for k in zf:
                self.assertTrue(
                    str(k).startswith("z_τh_")
                    or str(k).startswith("raw_ath_")
                    or str(k).startswith("z_τc_")
                    or str(k).startswith("raw_atc_"),
                    msg=k,
                )
            # OC 形状按后缀丢（含 z_τh_range_pct）
            for bare in TAU_HORIZON_DROP_OC_SHAPE:
                self.assertNotIn(bare, zf)
                self.assertNotIn(f"z_τh_{bare}", zf)
            # 短窗已升格到 raw_ath_*，不再进 z_τh_*
            self.assertNotIn("ret_last_5m", zf)
            self.assertNotIn("z_τh_ret_last_5m", zf)
            self.assertIn("raw_ath_RET_LAST_5M", zf)
            self.assertIn("z_τh_gap_pct", zf)
            self.assertIn("z_τh_tau_lag1", zf)

if __name__ == "__main__":
    unittest.main()
