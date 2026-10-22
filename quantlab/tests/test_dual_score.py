"""双层 predicted_score：字段契约与 F1 买入闸。"""

from __future__ import annotations

import unittest


class TestDualScoreFields(unittest.TestCase):
    def test_apply_tau_fields_sets_contract_keys(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 0.5, "score": 0.5}
        apply_tau_score_fields(
            item,
            rem_yhat=0.12,
            gap_pct=2.5,
            feats={"gap_pct": 2.5, "theme_day": 1.0, "momentum": 60.0},
            event_prior={"theme": True},
                        config={
                "dual_score": {
                    "fusion_mode": "blend",
                    "tau": "open",
                    "min_predicted_score_tau": 0.0,
                }
            },
        )
        self.assertEqual(item["predicted_score"], 0.5)
        self.assertNotIn("predicted_score_tau", item)
        self.assertNotIn("y_tau", item)
        self.assertEqual(item["y_τc"], 0.12)
        self.assertEqual(item["y_tau_raw"], 0.12)
        self.assertEqual(item["score_rem"], 0.12)
        self.assertEqual(item["as_of_tau"], "open")
        self.assertIn("formula", item["y_spec_tau"])
        self.assertEqual(item["features_tau"].get("gap_pct"), 2.5)
        self.assertNotIn("momentum", item["features_tau"])
        self.assertEqual(item["dual_score_fusion"], "blend")
        self.assertIn("formula_terms_tau", item)
        self.assertIn("score_formula_terms_tau", item)
        self.assertIsNotNone(item.get("predicted_score_eod_rem"))
        self.assertNotIn("predicted_score_nowcast", item)
        self.assertAlmostEqual(item.get("y_oo"), 0.5)

    def test_ensure_formula_terms_tau_from_eod_terms(self):
        """旧簿无 τ 组成时，从 EOD z + 组模型反推后与 rem 对齐。"""
        from unittest.mock import MagicMock, patch

        from core.signal.dual_score import ensure_formula_terms_tau

        eod_rm = MagicMock()
        eod_rm.z_means = {"momentum": 50.0}
        eod_rm.z_stds = {"momentum": 10.0}
        eod_rm.feature_zscore = True
        rem_doc = {
            "return_model": {
                "intercept": 0.1,
                "coefficients": {"momentum": 0.5},
                "active_features": ["momentum"],
                "zscore_means": {"momentum": 50.0},
                "zscore_stds": {"momentum": 10.0},
            }
        }
        item = {
            "stock_code": "000001",
            "score_formula_terms": {
                "terms": [
                    {
                        "key": "momentum",
                        "label": "动量",
                        "beta": 1.0,
                        "z": 1.0,
                        "contrib": 1.0,
                    }
                ]
            },
            "features_tau": {"gap_pct": 0.5},
        }
        with patch(
            "core.signal.dual_score.tau._eod_return_model_for_item", return_value=eod_rm
        ), patch(
            "core.research.tc_ridge.load_tau_model", return_value=rem_doc
        ):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        self.assertTrue(expl.get("terms"))
        self.assertAlmostEqual(float(expl["total"]), 0.1 + 0.5 * 1.0, places=4)

    def test_ensure_formula_terms_tau_refreshes_stale_gap(self):
        """簿上全缺特征的旧组成，后来有 gap 时必须重拆。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "return_model": {
                "intercept": 0.063,
                "coefficients": {
                    "momentum": 0.117,
                    "gap_pct": -0.12,
                    "sector_gap_breadth": 0.222,
                },
                "active_features": ["momentum", "gap_pct", "sector_gap_breadth"],
                "zscore_means": {"momentum": 0.0, "gap_pct": 0.0, "sector_gap_breadth": 0.0},
                "zscore_stds": {"momentum": 1.0, "gap_pct": 1.0, "sector_gap_breadth": 1.0},
            }
        }
        stale = {
            "intercept": 0.063,
            "terms": [
                {
                    "key": "momentum",
                    "label": "动量",
                    "beta": 0.117,
                    "z": 0.0,
                    "contrib": 0.0,
                    "note": "缺特征·z≈0",
                },
                {
                    "key": "gap_pct",
                    "label": "跳空 %",
                    "beta": -0.12,
                    "z": 0.0,
                    "contrib": 0.0,
                    "note": "缺特征·z≈0",
                },
            ],
            "total": 0.063,
        }
        item = {
            "formula_terms_tau": stale,
            "gap_pct": 0.313,
            "features_tau": {"gap_pct": 0.313},
        }
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertIn("gap_pct", by_key)
        self.assertNotIn("note", by_key["gap_pct"])
        self.assertAlmostEqual(float(by_key["gap_pct"]["z"]), 0.313, places=3)
        # 缺特征日线项不进 tip
        self.assertNotIn("momentum", by_key)

    def test_ensure_formula_terms_tau_refreshes_when_pack_arrives(self):
        """features_tau 已有开盘→τ，开盘 Z 组成必须重拆。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "return_model": {
                "intercept": 0.2,
                "coefficients": {
                    "theme_day": -0.05,
                    "ret_open_to_tau": -0.47,
                },
                "active_features": ["theme_day", "ret_open_to_tau"],
                "zscore_means": {"theme_day": 0.0, "ret_open_to_tau": 0.0},
                "zscore_stds": {"theme_day": 1.0, "ret_open_to_tau": 1.0},
            }
        }
        stale = {
            "intercept": 0.2,
            "terms": [
                {
                    "key": "theme_day",
                    "label": "主题日",
                    "beta": -0.05,
                    "z": -0.3,
                    "contrib": 0.015,
                }
            ],
            "total": 0.215,
        }
        item = {
            "formula_terms_tau": stale,
            "features_tau": {"theme_day": 0.0, "ret_open_to_tau": 1.25},
        }
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertIn("ret_open_to_tau", by_key)
        self.assertNotIn("note", by_key["ret_open_to_tau"])

    def test_ensure_formula_terms_tau_refreshes_when_inject_adds_loc_hl(self):
        """组成已有开盘→τ 时，注入 HL / 截面仍须重拆。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "return_model": {
                "intercept": 0.15,
                "coefficients": {
                    "ret_open_to_tau": 0.02,
                    "loc_hl": 0.03,
                    "sector_gap_breadth": -0.04,
                },
                "active_features": [
                    "ret_open_to_tau",
                    "loc_hl",
                    "sector_gap_breadth",
                ],
                "zscore_means": {
                    "ret_open_to_tau": 0.0,
                    "loc_hl": 0.0,
                    "sector_gap_breadth": 0.0,
                },
                "zscore_stds": {
                    "ret_open_to_tau": 1.0,
                    "loc_hl": 1.0,
                    "sector_gap_breadth": 1.0,
                },
            }
        }
        stale = {
            "intercept": 0.15,
            "total": 0.176,
            "terms": [
                {
                    "key": "ret_open_to_tau",
                    "label": "开盘→τ 收益 %",
                    "beta": 0.02,
                    "z": 1.3,
                    "contrib": 0.026,
                }
            ],
        }
        item = {
            "formula_terms_tau": stale,
            "features_tau": {
                "ret_open_to_tau": 1.3,
                "loc_hl": 0.69,
                "sector_gap_breadth": 0.008,
            },
        }
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertIn("loc_hl", by_key)
        self.assertIn("sector_gap_breadth", by_key)
        self.assertNotIn("note", by_key["loc_hl"])

    def test_ensure_formula_terms_tau_keeps_column_when_lake_row_is_raw(self):
        """未入模的开盘→τ 留在湖行上时，不得用 raw 价位比重拆盖掉表列。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "zscore_scope": "cross_section",
            "model_role": "live",
            "return_model": {
                "intercept": 0.085,
                "zscore_scope": "cross_section",
                "coefficients": {
                    "raw_atc_PMA20": 0.774,
                    "raw_atc_CLOSE0": 0.119,
                },
                "active_features": ["raw_atc_PMA20", "raw_atc_CLOSE0"],
            },
        }
        item = {
            "stock_code": "000001",
            "date": "2026-10-22",
            "y_τc": 0.32,
            "formula_terms_tau": {
                "intercept": 0.085,
                "total": 0.32,
                "terms": [
                    {
                        "key": "raw_atc_PMA20",
                        "beta": 0.774,
                        "z": 0.2,
                        "contrib": 0.155,
                    },
                    {
                        "key": "raw_atc_CLOSE0",
                        "beta": 0.119,
                        "z": 0.67,
                        "contrib": 0.08,
                    },
                ],
            },
            "features_tau": {
                "raw_atc_PMA20": 1.04,
                "raw_atc_CLOSE0": 1.0,
                "ret_open_to_tau": 0.0,
            },
        }
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        self.assertAlmostEqual(float(expl["total"]), 0.32, places=3)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertAlmostEqual(float(by_key["raw_atc_PMA20"]["z"]), 0.2, places=3)
        self.assertAlmostEqual(float(by_key["raw_atc_CLOSE0"]["z"]), 0.67, places=3)

    def test_ensure_formula_terms_tau_skips_raw_when_cs_z_missing(self):
        """模型含开盘→τ、但组不出截面 z 时，不把 raw≈1 乘 β。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "zscore_scope": "cross_section",
            "return_model": {
                "intercept": 0.085,
                "zscore_scope": "cross_section",
                "coefficients": {
                    "raw_atc_CLOSE0": 0.5,
                    "ret_open_to_tau": -0.2,
                },
                "active_features": ["raw_atc_CLOSE0", "ret_open_to_tau"],
            },
        }
        item = {
            "stock_code": "000001",
            "date": "2026-10-22",
            "y_τc": 0.32,
            "formula_terms_tau": {
                "intercept": 0.085,
                "total": 0.32,
                "terms": [
                    {
                        "key": "raw_atc_CLOSE0",
                        "beta": 0.5,
                        "z": 0.47,
                        "contrib": 0.235,
                    }
                ],
            },
            "features_tau": {"raw_atc_CLOSE0": 1.0, "ret_open_to_tau": 0.0},
        }
        with patch(
            "core.research.tc_ridge.load_tau_model", return_value=rem_doc
        ), patch(
            "core.t0.score_policy._tau_feature_row_for_refresh", return_value=None
        ):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        self.assertAlmostEqual(float(expl["total"]), 0.32, places=3)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertAlmostEqual(float(by_key["raw_atc_CLOSE0"]["z"]), 0.47, places=3)
        self.assertNotIn("ret_open_to_tau", by_key)

    def test_ensure_formula_terms_tau_rebuilds_from_saved_z(self):
        """已落盘的 raw 组成对不上表列时，用预测当时的截面 z 重拆。"""
        from unittest.mock import patch

        from core.signal.dual_score import ensure_formula_terms_tau

        rem_doc = {
            "zscore_scope": "cross_section",
            "return_model": {
                "intercept": 0.085,
                "zscore_scope": "cross_section",
                "coefficients": {"raw_atc_CLOSE0": 0.5},
                "active_features": ["raw_atc_CLOSE0"],
            },
        }
        item = {
            "stock_code": "000001",
            "date": "2026-10-22",
            "y_τc": 0.32,
            "features_tau": {"raw_atc_CLOSE0": 1.0, "ret_open_to_tau": 0.0},
            "features_tau_z": {"raw_atc_CLOSE0": 0.47},
            "formula_terms_tau": {
                "intercept": 0.085,
                "total": 0.585,
                "terms": [
                    {"key": "raw_atc_CLOSE0", "beta": 0.5, "z": 1.0, "contrib": 0.5}
                ],
            },
        }
        with patch("core.research.tc_ridge.load_tau_model", return_value=rem_doc):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertAlmostEqual(float(by_key["raw_atc_CLOSE0"]["z"]), 0.47, places=3)
        self.assertAlmostEqual(float(expl["total"]), 0.085 + 0.5 * 0.47, places=3)

    def test_explain_tau_prediction_lists_missing_keys(self):
        from core.research.tc_ridge import explain_tau_prediction

        doc = {
            "return_model": {
                "intercept": 0.1,
                "coefficients": {
                    "gap_pct": -0.04,
                    "range_pct": 0.02,
                    "ret_open_to_tau": -0.5,
                },
                "active_features": ["gap_pct", "range_pct", "ret_open_to_tau"],
                "zscore_means": {"gap_pct": 0.0, "range_pct": 1.0, "ret_open_to_tau": 0.4},
                "zscore_stds": {"gap_pct": 1.0, "range_pct": 1.0, "ret_open_to_tau": 1.0},
            }
        }
        expl = explain_tau_prediction(
            {"gap_pct": 0.5, "ret_open_to_tau": 0.0},
            model_doc=doc,
        )
        keys = {t["key"] for t in expl["terms"]}
        self.assertIn("gap_pct", keys)
        self.assertIn("ret_open_to_tau", keys)
        self.assertNotIn("range_pct", keys)
        self.assertEqual(expl.get("missing_n"), 1)
        self.assertIn("前缀振幅 %", expl.get("missing_keys") or [])

    def test_explain_tau_keeps_missing_raw_atc_in_diag(self):
        """缺 raw_atc_* 应进 missing_keys（不再被旧 aoo-only 过滤静默丢掉）。"""
        from core.research.tc_ridge import explain_tau_prediction

        doc = {
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "gap_pct": 0.1,
                    "raw_atc_YKLEN": 1.3,
                },
                "active_features": ["gap_pct", "raw_atc_YKLEN"],
                "zscore_means": {},
                "zscore_stds": {},
                "zscore_scope": "cross_section",
            },
            "zscore_scope": "cross_section",
        }
        expl = explain_tau_prediction({"gap_pct": 0.5}, model_doc=doc)
        self.assertEqual(expl.get("missing_n"), 1)
        miss = " ".join(expl.get("missing_keys") or [])
        self.assertTrue(
            "YKLEN" in miss or "raw_atc_YKLEN" in miss,
            miss,
        )
        keys = {t["key"] for t in expl["terms"]}
        self.assertIn("gap_pct", keys)
        self.assertNotIn("raw_atc_YKLEN", keys)

    def test_apply_tau_explain_feats_uses_cs_z_for_tip(self):
        """截面模型 tip 应用 explain_feats（z），勿用 raw 水平比挤掉 YKLEN。"""
        from core.signal.dual_score import apply_tau_score_fields

        doc = {
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "raw_atc_YKLEN": 1.3,
                    "raw_atc_YVWAP0": -0.7,
                },
                "active_features": ["raw_atc_YKLEN", "raw_atc_YVWAP0"],
                "zscore_means": {},
                "zscore_stds": {},
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        raw = {"raw_atc_YKLEN": 0.03, "raw_atc_YVWAP0": 1.0, "gap_pct": 0.0}
        # 截面 z：YKLEN 偏高、YVWAP0 近均值
        z_row = {"raw_atc_YKLEN": 1.2, "raw_atc_YVWAP0": 0.1, "gap_pct": 0.0}
        item = {"predicted_score": 1.0, "score": 1.0}
        apply_tau_score_fields(
            item,
            rem_yhat=1.5,
            gap_pct=0.0,
            feats=raw,
            explain_feats=z_row,
            rem_model_doc=doc,
                        config={"dual_score": {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed"}},
        )
        # features_tau 仍落 raw
        self.assertAlmostEqual(item["features_tau"]["raw_atc_YKLEN"], 0.03, places=5)
        terms = (item.get("formula_terms_tau") or {}).get("terms") or []
        by_key = {t["key"]: t for t in terms}
        self.assertIn("raw_atc_YKLEN", by_key)
        # z 口径下 YKLEN |contrib| 应大于 YVWAP0
        self.assertGreater(
            abs(float(by_key["raw_atc_YKLEN"]["contrib"])),
            abs(float(by_key["raw_atc_YVWAP0"]["contrib"])),
        )
        self.assertEqual(
            (item.get("formula_terms_tau") or {}).get("zscore_scope"),
            "cross_section",
        )
        self.assertAlmostEqual(item["features_tau_z"]["raw_atc_YVWAP0"], 0.1, places=5)

    def test_apply_tau_cs_without_z_keeps_pool_tip(self):
        """观察池补 τ 不再用 raw 价位比重拆，盖掉已经和表列对齐的组成。"""
        from core.signal.dual_score import apply_tau_score_fields

        doc = {
            "return_model": {
                "intercept": 0.085,
                "coefficients": {"raw_atc_YVWAP0": -0.531, "raw_atc_CLOSE0": 0.119},
                "active_features": ["raw_atc_YVWAP0", "raw_atc_CLOSE0"],
                "zscore_scope": "cross_section",
            },
            "zscore_scope": "cross_section",
        }
        item = {
            "predicted_score": 0.2,
            "y_τc": 0.32,
            "features_tau": {"raw_atc_YVWAP0": 1.0, "raw_atc_CLOSE0": 1.0, "ret_open_to_tau": 0.0},
            "formula_terms_tau": {
                "intercept": 0.085,
                "total": 0.32,
                "terms": [
                    {"key": "raw_atc_YVWAP0", "beta": -0.531, "z": -0.4, "contrib": 0.212},
                    {"key": "raw_atc_CLOSE0", "beta": 0.119, "z": 0.2, "contrib": 0.024},
                ],
            },
        }
        apply_tau_score_fields(
            item,
            rem_yhat=None,
            gap_pct=0.0,
            feats=item["features_tau"],
            rem_model_doc=doc,
            config={"dual_score": {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed"}},
        )
        by_key = {t["key"]: t for t in item["formula_terms_tau"]["terms"]}
        self.assertAlmostEqual(float(by_key["raw_atc_YVWAP0"]["z"]), -0.4, places=3)
        self.assertAlmostEqual(float(item["formula_terms_tau"]["total"]), 0.32, places=3)
        self.assertAlmostEqual(float(item["y_τc"]), 0.32, places=3)

    def test_rescore_tau_stamps_tip_from_cs_z(self):
        from unittest import mock

        from core.signal.cross_section_rescore import _rescore_tau

        doc = {
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "raw_atc_YKLEN": 1.3,
                    "raw_atc_YVWAP0": -0.7,
                },
                "active_features": ["raw_atc_YKLEN", "raw_atc_YVWAP0"],
                "zscore_means": {},
                "zscore_stds": {},
                "zscore_scope": "cross_section",
                "feature_zscore": True,
            },
            "zscore_scope": "cross_section",
        }
        items = [
            {
                "stock_code": "A",
                "gap_pct": 0.0,
                "features_tau": {
                    "raw_atc_YKLEN": 0.06,
                    "raw_atc_YVWAP0": 0.99,
                    "gap_pct": 0.0,
                },
            },
            {
                "stock_code": "B",
                "gap_pct": 0.0,
                "features_tau": {
                    "raw_atc_YKLEN": 0.02,
                    "raw_atc_YVWAP0": 1.01,
                    "gap_pct": 0.0,
                },
            },
        ]
        with mock.patch(
            "core.research.tc_ridge.load_tau_model", return_value=doc
        ), mock.patch(
            "core.research.feature_standardize.is_cross_section_zscore",
            return_value=True,
        ):
            _rescore_tau(items)
        terms = (items[0].get("formula_terms_tau") or {}).get("terms") or []
        keys = [t["key"] for t in terms]
        self.assertIn("raw_atc_YKLEN", keys)
        # A 的 YKLEN raw 更高 → 截面 z 更大 → tip 首项常为 YKLEN
        self.assertEqual(keys[0], "raw_atc_YKLEN")
        # 落盘特征仍是 raw
        self.assertAlmostEqual(items[0]["features_tau"]["raw_atc_YKLEN"], 0.06, places=5)
    def test_eod_remaining_and_residual_stack(self):
        from core.signal.dual_score import (
            apply_tau_score_fields,
            oo_remaining_at_tau,
            fuse_remaining_heads,
            realized_t1_to_tau_pct,
        )

        self.assertAlmostEqual(realized_t1_to_tau_pct(1.0), 1.0, places=6)
        self.assertAlmostEqual(
            realized_t1_to_tau_pct(1.0, 1.0),
            ((1.01 * 1.01) - 1.0) * 100.0,
            places=5,
        )
        rem = oo_remaining_at_tau(2.0, 1.0)
        self.assertAlmostEqual(rem, ((1.02 / 1.01) - 1.0) * 100.0, places=5)
        self.assertAlmostEqual(
            fuse_remaining_heads(rem, 0.3), (rem + 0.3) / 2.0, places=5
        )

        item = {"predicted_score": 2.0, "score": 2.0}
        apply_tau_score_fields(
            item,
            rem_yhat=0.3,
            gap_pct=1.0,
            feats={"gap_pct": 1.0},
                        config={
                "dual_score": {
                    "w_eod": 0.5,
                    "w_tau": 0.5,
                    "w_mode": "fixed",
                }
            },
        )
        self.assertAlmostEqual(item["realized_t1_to_tau"], 1.0, places=5)
        self.assertAlmostEqual(item["predicted_score_eod_rem"], rem, places=5)
        self.assertNotIn("predicted_score_tau_delta", item)
        self.assertNotIn("predicted_score_tau", item)
        self.assertNotIn("y_tau", item)
        self.assertAlmostEqual(item["y_τc"], 0.3, places=5)
        from core.signal.yhat_geom import compound_pct

        tau_cc = compound_pct(1.0, 0.3)
        self.assertAlmostEqual(item["predicted_score_blend_tau_cc"], tau_cc, places=5)
        self.assertAlmostEqual(
            item["predicted_score_blend"], (2.0 + tau_cc) / 2.0, places=5
        )
        self.assertEqual(item["predicted_score_blend_vs"], "prev_close")
        self.assertEqual(item["predicted_score"], 2.0)
        self.assertTrue(item["dual_score_weights"].get("tau_available"))

    def test_trade_blend_vs_prev_close_fuses_eod_with_lifted_tau(self):
        from core.signal.dual_score import trade_blend_vs_prev_close
        from core.signal.yhat_geom import compound_pct

        tau_cc_exp = compound_pct(1.0, 0.3)
        cc, tau_cc, vs = trade_blend_vs_prev_close(2.0, 0.3, gap_pct=1.0)
        self.assertEqual(vs, "prev_close")
        self.assertAlmostEqual(tau_cc, tau_cc_exp, places=6)
        self.assertAlmostEqual(cc, (2.0 + tau_cc_exp) / 2.0, places=6)

    def test_buy_gate_legacy_f0_still_blocks(self):
        """f0 已废弃，归一为 f2 后 τ 闸仍生效。"""
        from core.signal.dual_score import buy_passes_tau_gate

        ok, reason = buy_passes_tau_gate(
            {"predicted_score_tau": -1.0},
            config={"dual_score": {"fusion_mode": "f0"}},
        )
        self.assertFalse(ok)
        self.assertIn("ŷ_τ", str(reason))

    def test_buy_gate_blocks_low_tau(self):
        from core.signal.dual_score import buy_passes_tau_gate

        ok, reason = buy_passes_tau_gate(
            {"predicted_score_tau": -0.2},
            config={
                "dual_score": {
                    "fusion_mode": "f2",
                    "min_predicted_score_tau": 0.0,
                }
            },
        )
        self.assertFalse(ok)
        self.assertIn("ŷ_τ", str(reason))

    def test_tau_freeze_breakglass_lowers_floor(self):
        from core.signal.dual_score import resolve_tau_buy_floor_for_pool

        pool = [
            {"predicted_score_tau": 0.05},
            {"predicted_score_tau": 0.08},
            {"predicted_score_tau": 0.04},
        ]
        floor, meta = resolve_tau_buy_floor_for_pool(
            pool,
            config={
                "dual_score": {
                    "min_predicted_score_tau": 0.3,
                    "tau_freeze_breakglass": True,
                    "min_predicted_score_tau_relax": 0.0,
                    "tau_freeze_breakglass_min_n": 3,
                }
            },
        )
        self.assertEqual(meta.get("mode"), "freeze_breakglass")
        self.assertAlmostEqual(floor, 0.0)
        self.assertEqual(meta.get("n_pass_base"), 0)

    def test_tau_freeze_breakglass_default_relax_is_half_base(self):
        from core.signal.dual_score import resolve_tau_buy_floor_for_pool

        pool = [
            {"predicted_score_tau": 0.05},
            {"predicted_score_tau": 0.08},
            {"predicted_score_tau": 0.02},
        ]
        floor, meta = resolve_tau_buy_floor_for_pool(
            pool,
            config={
                "dual_score": {
                    "min_predicted_score_tau": 0.3,
                    "tau_freeze_breakglass": True,
                    "tau_freeze_breakglass_min_n": 3,
                }
            },
        )
        self.assertEqual(meta.get("mode"), "freeze_breakglass")
        self.assertAlmostEqual(floor, 0.15)
        self.assertAlmostEqual(meta.get("relax_floor"), 0.15)

    def test_tau_freeze_breakglass_keeps_strict_when_someone_passes(self):
        from core.signal.dual_score import (
            buy_passes_tau_gate,
            resolve_tau_buy_floor_for_pool,
        )

        pool = [
            {"predicted_score_tau": 0.05},
            {"predicted_score_tau": 0.35},
            {"predicted_score_tau": 0.10},
        ]
        floor, meta = resolve_tau_buy_floor_for_pool(
            pool,
            config={
                "dual_score": {
                    "min_predicted_score_tau": 0.3,
                    "tau_freeze_breakglass": True,
                    "min_predicted_score_tau_relax": 0.0,
                    "tau_freeze_breakglass_min_n": 3,
                }
            },
        )
        self.assertEqual(meta.get("mode"), "strict")
        self.assertAlmostEqual(floor, 0.3)
        ok, _ = buy_passes_tau_gate(
            {"predicted_score_tau": 0.12},
            config={"dual_score": {"min_predicted_score_tau": 0.3}},
            floor=floor,
        )
        self.assertFalse(ok)

    def test_tau_freeze_breakglass_requires_min_n(self):
        from core.signal.dual_score import resolve_tau_buy_floor_for_pool

        pool = [
            {"predicted_score_tau": 0.05},
            {"predicted_score_tau": 0.08},
        ]
        floor, meta = resolve_tau_buy_floor_for_pool(
            pool,
            config={
                "dual_score": {
                    "min_predicted_score_tau": 0.3,
                    "tau_freeze_breakglass": True,
                    "tau_freeze_breakglass_min_n": 3,
                }
            },
        )
        self.assertEqual(meta.get("mode"), "strict")
        self.assertAlmostEqual(floor, 0.3)
        self.assertIn("min_n", str(meta.get("note") or ""))

    def test_eod_next_blend_never_falls_back_to_tau_cc(self):
        from core.signal.dual_score import compute_predicted_score_blend

        item = {
            "predicted_score_eod": 0.8,
            "predicted_score_tau": -1.2,
            "gap_pct": 1.0,
            "dual_score_window": "eod_next",
        }
        out = compute_predicted_score_blend(
            item, config={"dual_score": {"fusion_mode": "blend", "w_eod": 0.5, "w_tau": 0.5}}
        )
        self.assertAlmostEqual(float(out), 0.8, places=5)

    def test_heuristic_keeps_cluster_yhat_for_rank(self):
        from core.signal.dual_score import align_trade_score_fields, rank_key_for_item

        item = {
            "score_scale": "heuristic_0_100",
            "return_model_source": "oos_failed_heuristic",
            "heuristic_score": 62.0,
            "score_cluster": 0.85,
            "score_global": 0.40,
        }
        align_trade_score_fields(item, write_score=True)
        self.assertAlmostEqual(float(item["predicted_score_eod"]), 0.85, places=5)
        self.assertAlmostEqual(float(rank_key_for_item(item)), 0.85, places=5)
        self.assertEqual(item.get("score_scale"), "heuristic_0_100")
        self.assertAlmostEqual(float(item["heuristic_score"]), 62.0, places=5)

    def test_buy_gate_allows_missing_when_disabled(self):
        from core.signal.dual_score import buy_passes_tau_gate

        ok, _ = buy_passes_tau_gate(
            {"predicted_score": 1.0},
            config={
                "dual_score": {
                    "fusion_mode": "f2",
                    "block_buy_if_tau_missing": False,
                }
            },
        )
        self.assertTrue(ok)

    def test_buy_gate_default_blocks_missing_tau(self):
        from core.signal.dual_score import DEFAULT_DUAL_SCORE, buy_passes_tau_gate, get_dual_score_cfg

        self.assertTrue(DEFAULT_DUAL_SCORE.get("block_buy_if_tau_missing"))
        self.assertTrue(get_dual_score_cfg({}).get("block_buy_if_tau_missing"))
        ok, reason = buy_passes_tau_gate(
            {"predicted_score": 1.0},
            config={"dual_score": {}},
        )
        self.assertFalse(ok)
        self.assertIn("缺失", str(reason))

    def test_buy_gate_can_block_missing(self):
        from core.signal.dual_score import buy_passes_tau_gate

        ok, reason = buy_passes_tau_gate(
            {},
            config={
                "dual_score": {
                    "fusion_mode": "f2",
                    "block_buy_if_tau_missing": True,
                }
            },
        )
        self.assertFalse(ok)
        self.assertIn("缺失", str(reason))

    def test_residual_rank_key(self):
        from core.signal.dual_score import (
            apply_tau_score_fields,
            compute_predicted_score_blend,
            rank_key_for_item,
        )

        cfg = {
            "dual_score": {
                "fusion_mode": "blend",
            }
        }
        item = {"predicted_score": 1.0, "score": 1.0}
        apply_tau_score_fields(
            item, rem_yhat=0.2, gap_pct=0.0, feats={}, config=cfg
        )
        self.assertAlmostEqual(item["predicted_score_eod_rem"], 1.0, places=5)
        self.assertNotIn("predicted_score_tau", item)
        self.assertNotIn("y_tau", item)
        self.assertAlmostEqual(item["y_τc"], 0.2, places=5)
        self.assertAlmostEqual(item["predicted_score_blend"], 0.6, places=5)
        self.assertAlmostEqual(
            compute_predicted_score_blend(item, config=cfg), 0.6, places=5
        )
        # 排序键始终 raw ŷ_trade（与校准是否有 live 无关）
        self.assertAlmostEqual(rank_key_for_item(item, config=cfg), 0.6, places=5)
        self.assertEqual(item["dual_score_fusion"], "blend")

    def test_book_fields_skips_calibration_overlay(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "predicted_score_eod_rem": 1.0,
                "predicted_score_tau": 0.2,
                "predicted_score_blend": 0.6,
            }
        )
        self.assertNotIn("predicted_score_cal", out)
        self.assertNotIn("score_calibration_applied", out)

    def test_book_fields_passes_eod_feature_as_of(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "eod_feature_as_of": "2026-09-16",
                "trade_day": "2026-09-17",
                "dual_score_window": "intraday",
            }
        )
        self.assertEqual(out.get("eod_feature_as_of"), "2026-09-16")
        self.assertEqual(out.get("trade_day"), "2026-09-17")
        self.assertEqual(out.get("dual_score_window"), "intraday")

    def test_book_fields_passes_factor_anomaly(self):
        from core.signal.dual_score import dual_score_book_fields

        fa = {
            "ok": False,
            "fatal_tau": True,
            "fatal_eod": False,
            "issues": [{"kind": "pit", "code": "as_of_tau", "reason": "τ日错位"}],
        }
        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "y_oo": 1.0,
                "y_τc": 0.4,
                "score_rem": 0.4,
                "factor_anomaly": fa,
            }
        )
        self.assertEqual(out.get("factor_anomaly"), fa)
        self.assertEqual(out.get("y_oo"), 1.0)
        self.assertIsNone(out.get("y_τc"))
        self.assertIsNone(out.get("y_tau"))
        self.assertIsNone(out.get("score_rem"))
        self.assertIsNone(out.get("y_oo_fuse"))

    def test_book_fields_passes_y_τc(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "y_τc": 1.25,
                "y_r": 0.4,
            }
        )
        self.assertAlmostEqual(out.get("y_τc"), 1.25, places=6)
        self.assertAlmostEqual(out.get("predicted_score_τc"), 1.25, places=6)
        self.assertAlmostEqual(out.get("y_r"), 0.4, places=6)

        legacy = dual_score_book_fields({"y_r": 1.0, "predicted_score": 0.5})
        self.assertIsNone(legacy.get("y_τc"))
        self.assertIsNone(legacy.get("predicted_score_τc"))

    def test_book_fields_passes_formula_terms_r(self):
        from core.signal.dual_score import dual_score_book_fields

        terms = {
            "intercept": 0.1,
            "total": -0.4,
            "head": "r",
            "terms": [{"key": "gap_pct", "contrib": -0.5}],
        }
        out = dual_score_book_fields(
            {
                "predicted_score": 1.0,
                "y_τc": -0.4,
                "formula_terms_r": terms,
            }
        )
        self.assertEqual(out.get("formula_terms_r", {}).get("head"), "r")
        self.assertAlmostEqual(out.get("formula_terms_r", {}).get("total"), -0.4, places=6)
        self.assertEqual(len(out.get("formula_terms_r", {}).get("terms") or []), 1)

    def test_eod_gate_and_decision_stay_raw(self):
        """闸/决策分始终 raw；陈旧校准字段不参与。"""
        from core.signal.dual_score import (
            decision_score_for_item,
            oo_gate_score_for_item,
        )

        item = {
            "predicted_score": 2.0,
            "predicted_score_eod": 2.0,
            "predicted_score_eod_rem": 1.0,
            "y_τc": 0.5,
            "predicted_score_τc": 0.5,
            "predicted_score_blend": 0.75,
            "predicted_score_cal": 1.0,
            "score_calibration_applied": True,
        }
        self.assertAlmostEqual(oo_gate_score_for_item(item), 2.0, places=5)
        # decision = fuse 现算 = fuse(ŷ_oo=2.0, ŷ_τc=0.5) = 1.25（不受 calibration 影响）
        self.assertAlmostEqual(decision_score_for_item(item), 1.25, places=5)

    def test_legacy_rem_not_added_to_eod_rem(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 2.0, "score": 2.0}
        apply_tau_score_fields(
            item,
            rem_yhat=-1.0,
            gap_pct=1.0,
            feats={},
                        config={
                "dual_score": {
                    "w_eod": 0.5,
                    "w_tau": 0.5,
                    "w_mode": "fixed",
                }
            },
        )
        self.assertNotIn("predicted_score_tau", item)
        self.assertNotIn("y_tau", item)
        self.assertAlmostEqual(item["y_τc"], -1.0, places=5)
        self.assertNotIn("predicted_score_tau_delta", item)
        rem = ((1.02 / 1.01) - 1.0) * 100.0
        self.assertAlmostEqual(item["predicted_score_eod_rem"], rem, places=5)
        self.assertNotIn("predicted_score_tau", item)
        self.assertAlmostEqual(item["y_τc"], -1.0, places=5)
        from core.signal.yhat_geom import compound_pct

        tau_cc = compound_pct(1.0, -1.0)
        self.assertAlmostEqual(
            item["predicted_score_blend"], (2.0 + tau_cc) / 2.0, places=5
        )

    def test_flattened_cfg_passthrough_keeps_tau_fields(self):
        """回测 attach→apply 二次传入 flatten dual 不得丢 τ 闸。"""
        from core.signal.dual_score import (
            apply_tau_score_fields,
            attach_dual_score_pit,
            get_dual_score_cfg,
        )

        nested = {"dual_score": {"w_eod": 0.1, "w_tau": 0.9, "fusion_mode": "f2"}}
        flat = get_dual_score_cfg(nested)
        self.assertEqual(flat["fusion_mode"], "blend")
        self.assertAlmostEqual(flat["w_eod"], 0.1)
        self.assertAlmostEqual(flat["w_tau"], 0.9)
        again = get_dual_score_cfg(flat)
        self.assertAlmostEqual(again["w_eod"], 0.1)

        item = {"predicted_score": 2.0, "score": 2.0, "stock_code": "T"}
        apply_tau_score_fields(
            item,
            rem_yhat=-1.0,
            gap_pct=1.0,
            feats={},
            config=flat,
                    )
        rem = ((1.02 / 1.01) - 1.0) * 100.0
        self.assertAlmostEqual(item["predicted_score_eod_rem"], rem, places=5)
        self.assertNotIn("predicted_score_tau", item)
        self.assertNotIn("y_tau", item)
        self.assertAlmostEqual(item["y_τc"], -1.0, places=5)
        from core.signal.yhat_geom import compound_pct

        tau_cc = compound_pct(1.0, -1.0)
        self.assertAlmostEqual(
            item["predicted_score_blend"], 0.1 * 2.0 + 0.9 * tau_cc, places=5
        )
        self.assertEqual(item["dual_score_weights"]["w_eod"], 0.1)

        item2 = {
            "predicted_score": 2.0,
            "score": 2.0,
            "stock_code": "T2",
            "sub_scores": {},
        }
        attach_dual_score_pit(item2, quote=None, bars=None, config=nested)
        self.assertEqual(item2["dual_score_fusion"], "blend")
        self.assertEqual(item2["dual_score_weights"]["w_eod"], 0.1)

    def test_book_fields_override_stale_fusion(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "dual_score_fusion": "f1",
                "predicted_score_tau": 0.1,
            }
        )
        self.assertEqual(out["dual_score_fusion"], "blend")
        self.assertNotIn("predicted_score_nowcast", out)

    def test_book_fields_omits_nowcast(self):
        from core.signal.dual_score import dual_score_book_fields

        src = {
            "stock_code": "000001",
            "predicted_score": 1.2,
            "predicted_score_eod": 1.2,
            "predicted_score_tau": 0.8,
            "predicted_score_blend": 1.2,
            "predicted_score_nowcast": 1.2,
            "nowcast_as_of": "eod",
            "nowcast_K": None,
            "gap_pct": 2.0,
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5},
        }
        out = dual_score_book_fields(src)
        self.assertNotIn("predicted_score_nowcast", out)
        self.assertNotIn("nowcast_K", out)

    def test_book_fields_stamps_y_oo_fuse_from_rank_cfg(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {
                "predicted_score_eod": 2.30,
                "y_oo": 2.30,
                "y_τc": 5.69,
            },
            rank_cfg={"fusion_w_oo": 0.8, "fusion_w_oc": 0.2, "fusion_w_co": 0.0},
        )
        self.assertAlmostEqual(float(out.get("fusion_w_oo")), 0.8)
        self.assertAlmostEqual(float(out.get("fusion_w_oc")), 0.2)
        self.assertAlmostEqual(float(out.get("y_oo_fuse")), 0.8 * 2.30 + 0.2 * 5.69, places=4)


    def test_features_tau_includes_ret_open(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 0.4}
        apply_tau_score_fields(
            item,
            rem_yhat=0.1,
            gap_pct=1.0,
            feats={"gap_pct": 1.0, "ret_open_to_tau": 0.8},
            as_of_tau="2026-08-14T09:45:00+08:00",
            y_spec_override={"tau": "09:45", "formula": "close[T]/price[09:45]-1"},
                    )
        self.assertEqual(item["as_of_tau"], "2026-08-14T09:45:00+08:00")
        self.assertEqual(item["features_tau"].get("ret_open_to_tau"), 0.8)
        self.assertEqual(item["y_spec_tau"].get("tau"), "09:45")

    def test_features_tau_includes_gap_atr_and_rel(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 0.4}
        apply_tau_score_fields(
            item,
            rem_yhat=0.1,
            gap_pct=1.0,
            feats={
                "gap_pct": 1.0,
                "gap_atr": 0.8,
                "gap_vs_sector": 0.3,
                "momentum": 50.0,
            },
                    )
        self.assertEqual(item["features_tau"].get("gap_atr"), 0.8)
        self.assertEqual(item["features_tau"].get("gap_vs_sector"), 0.3)
        self.assertEqual(item["features_tau"].get("z_τc_gap_atr"), 0.8)
        self.assertEqual(item["features_tau"].get("z_τc_gap_pct"), 1.0)
        self.assertNotIn("momentum", item["features_tau"])
        self.assertIn("features_tau_fill", item)
        self.assertEqual(item["features_tau_fill"]["filled"], 3)

    def test_features_tau_snapshot_keeps_tau_lag(self):
        from core.signal.dual_score import apply_tau_score_fields

        item = {"predicted_score": 0.4}
        apply_tau_score_fields(
            item,
            rem_yhat=0.1,
            gap_pct=1.0,
            feats={
                "gap_pct": 1.0,
                "tau_lag1": 0.42,
                "tau_ma5": 0.11,
                "momentum": 50.0,
            },
                    )
        self.assertAlmostEqual(item["features_tau"].get("tau_lag1"), 0.42)
        self.assertAlmostEqual(item["features_tau"].get("tau_ma5"), 0.11)
        self.assertNotIn("momentum", item["features_tau"])
        self.assertNotIn("predicted_score_tau_cascade", item)

    def test_merge_tau_features_prefers_base_keeps_prior(self):
        from core.signal.dual_score import merge_tau_features

        m = merge_tau_features(
            {"gap_pct": 1.0, "gap_atr": None},
            {"gap_pct": 9.0, "gap_atr": 0.5, "sector_gap_breadth": 0.4},
        )
        self.assertEqual(m["gap_pct"], 1.0)
        self.assertEqual(m["gap_atr"], 0.5)
        self.assertEqual(m["sector_gap_breadth"], 0.4)

    def test_resolve_fusion_weights_theme_boost(self):
        from core.signal.dual_score import resolve_fusion_weights

        we, wt, note = resolve_fusion_weights(
            {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "theme_boost", "theme_w_tau_boost": 2.0},
            feats={"theme_day": 1.0},
        )
        self.assertAlmostEqual(we + wt, 1.0, places=5)
        self.assertGreater(wt, we)
        self.assertIn("theme_boost", note)

    def test_attach_preserves_book_features_tau(self):
        from core.signal.dual_score import attach_dual_score_pit

        item = {
            "predicted_score": 1.0,
            "features_tau": {
                "gap_pct": 1.2,
                "sector_gap_breadth": 0.55,
                "theme_day": 1.0,
                "gap_atr": 0.7,
                "gap_vs_sector": 0.2,
            },
        }
        attach_dual_score_pit(item, quote=None, bars=None)
        ft = item.get("features_tau") or {}
        self.assertEqual(ft.get("sector_gap_breadth"), 0.55)
        self.assertEqual(ft.get("gap_atr"), 0.7)
        self.assertEqual(ft.get("gap_vs_sector"), 0.2)

    def test_save_dual_score_fusion_f2(self):
        import json
        import os
        import tempfile

        from core.signal.dual_score.config import read_dual_score_public, save_dual_score

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "dual_score": {
                            "fusion_mode": "f1",
                            "minute_tau_hm": "10:30",
                            "minute_tau_grid": ["09:30", "10:30"],
                        },
                        "weights": {"x": 1},
                    },
                    f,
                )
            os.environ["QUANTLAB_SIGNAL_CONFIG"] = path
            try:
                import core.signal.config as cfg_mod

                cfg_mod._cached = None
                out = save_dual_score(
                    fusion_mode="f2",
                    min_predicted_score_tau=0.1,
                    w_eod=0.6,
                    w_tau=0.4,
                    note="test",
                )
                self.assertTrue(out["success"])
                self.assertEqual(out["dual_score"]["fusion_mode"], "blend")
                self.assertEqual(out["dual_score"]["min_predicted_score_tau"], 0.1)
                self.assertEqual(out["dual_score"]["w_eod"], 0.6)
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertEqual(raw["weights"], {"x": 1})
                self.assertEqual(raw["dual_score"]["fusion_mode"], "blend")
                self.assertNotIn("minute_tau_hm", raw["dual_score"])
                self.assertNotIn("minute_tau_grid", raw["dual_score"])
                pub = read_dual_score_public()
                self.assertEqual(pub["fusion_mode"], "blend")
                self.assertNotIn("minute_tau_hm", pub)
                self.assertNotIn("minute_tau_grid", pub)
            finally:
                os.environ.pop("QUANTLAB_SIGNAL_CONFIG", None)
                import core.signal.config as cfg_mod

                cfg_mod._cached = None

class TestPromoteOosCompareRetired(unittest.TestCase):
    def test_cluster_oos_labels_package_gone(self):
        """compare_oos_vs_active / compare_partition_vs_active 随 cluster 包删除。"""
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("core.signal.cluster"))
