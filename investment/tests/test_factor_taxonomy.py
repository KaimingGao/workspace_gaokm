"""因子经济族 / 来源分类（分组表徽章）。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.factors.meta.panel import build_factor_panel
from core.signal.factors.meta.registry import list_factors, registered_factor_names
from core.signal.factors.meta.taxonomy import (
    REMOVED_RAW_BASIS_NAMES,
    classify_factor,
    classify_factors,
    strip_removed_factors_from_cluster_report,
)


class TestFactorTaxonomy(unittest.TestCase):
    def test_core_families(self):
        self.assertEqual(classify_factor("momentum")["family"], "trend")
        self.assertEqual(classify_factor("value")["family"], "value_quality")
        self.assertEqual(classify_factor("amihud")["family"], "liquidity_flow")
        self.assertEqual(classify_factor("gap_risk")["family"], "risk")
        self.assertEqual(classify_factor("size")["family"], "residual")
        self.assertEqual(classify_factor("reversal")["family"], "reversal")
        self.assertEqual(classify_factor("alt_sentiment")["family"], "sentiment")

    def test_source_badges(self):
        mf = classify_factor("money_flow")
        self.assertEqual(mf["family"], "liquidity_flow")
        self.assertEqual(mf["source"], "proxy")
        self.assertEqual(mf["source_label"], "代理")

        alt = classify_factor("alt_sentiment")
        self.assertEqual(alt["source"], "prior_only")
        self.assertEqual(alt["source_label"], "旁路")

    def test_panel_exposes_taxonomy(self):
        panel = build_factor_panel()
        self.assertTrue(panel["success"])
        by = {r["factor"]: r for r in panel["rows"]}
        self.assertEqual(by["momentum"]["family"], "trend")
        self.assertEqual(by["money_flow"]["source"], "proxy")
        fac = {f["name"]: f for f in panel["factors"]}
        self.assertEqual(fac["quality"]["family_label"], "价值质量")

    def test_registered_names_coverable(self):
        names = [f["name"] for f in list_factors()]
        got = classify_factors(names)
        self.assertEqual(len(got), len(names))
        for tax in got.values():
            self.assertIn(
                tax["family"],
                {
                    "trend",
                    "value_quality",
                    "liquidity_flow",
                    "risk",
                    "residual",
                    "reversal",
                    "sentiment",
                    "other",
                },
            )

    def test_raw_basis_factors_unregistered(self):
        registered = set(registered_factor_names())
        for name in REMOVED_RAW_BASIS_NAMES:
            self.assertNotIn(name, registered)
            tax = classify_factor(name)
            self.assertEqual(tax["family"], "other")

    def test_strip_removed_from_cluster_report(self):
        report = {
            "success": True,
            "feature_names": ["momentum", "mom3_pct", "value"],
            "clusters": [
                {
                    "ols": {
                        "coefficients": {"momentum": 0.1, "mom3_pct": 0.02},
                        "exclusion_reasons": {"pe_raw": "未算"},
                    },
                    "factor_ic_panel": {
                        "rows": [
                            {"factor": "momentum", "ic": 0.05},
                            {"factor": "atr_pct_raw", "ic": None},
                        ],
                        "exclusion_reasons": {"vol_elevated": "未算"},
                    },
                }
            ],
        }
        out = strip_removed_factors_from_cluster_report(report)
        self.assertEqual(out["feature_names"], ["momentum", "value"])
        cl = out["clusters"][0]
        self.assertEqual(set(cl["ols"]["coefficients"].keys()), {"momentum"})
        self.assertEqual(cl["ols"]["exclusion_reasons"], {})
        self.assertEqual(len(cl["factor_ic_panel"]["rows"]), 1)
        self.assertEqual(cl["factor_ic_panel"]["exclusion_reasons"], {})

    def test_strip_keeps_empty_ic_rows_list(self):
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_cluster_report

        report = {
            "clusters": [
                {
                    "factor_ic_panel": {
                        "rows": [],
                        "exclusion_reasons": {"pe_raw": "未算"},
                    }
                }
            ],
        }
        out = strip_removed_factors_from_cluster_report(report)
        self.assertEqual(out["clusters"][0]["factor_ic_panel"]["rows"], [])
        self.assertEqual(out["clusters"][0]["factor_ic_panel"]["exclusion_reasons"], {})

    def test_strip_removed_from_pool_artifact(self):
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_pool_artifact

        art = {
            "code_map": {
                "000001": {
                    "weights": {"momentum": 0.5, "mom3_pct": 0.1},
                    "return_model": {
                        "coefficients": {"momentum": 0.2},
                        "sample_fingerprint": {
                            "dropped": {"sparse": ["mom3_pct", "pe_raw", "value"]}
                        },
                    },
                }
            },
            "clusters": [
                {
                    "return_model": {
                        "sample_fingerprint": {
                            "dropped": {"sparse": ["atr_pct_raw", "quality"]}
                        }
                    }
                }
            ],
        }
        out = strip_removed_factors_from_pool_artifact(art)
        entry = out["code_map"]["000001"]
        self.assertEqual(set(entry["weights"].keys()), {"momentum"})
        self.assertEqual(
            entry["return_model"]["sample_fingerprint"]["dropped"]["sparse"],
            ["value"],
        )
        self.assertEqual(
            out["clusters"][0]["return_model"]["sample_fingerprint"]["dropped"][
                "sparse"
            ],
            ["quality"],
        )


if __name__ == "__main__":
    unittest.main()
