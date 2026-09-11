"""分组拟合三档 A/B/C。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.cluster.fit_tier import (
    attach_cluster_fit_tiers,
    classify_cluster_fit_tier,
)


def _passed_gate(*, yhat=1.2):
    return {
        "ok": True,
        "passed": True,
        "skipped": False,
        "reason": "oos_not_worse",
        "research": {"oos": {"oos_return_pct": yhat}},
    }


def _ic_panel(*, ic=0.04, icir=0.3):
    return {
        "score_ic": {
            "spearman": {"ic": ic, "icir": icir},
        }
    }


class ClusterFitTierTests(unittest.TestCase):
    def test_a_requires_pass_ic_icir_yhat(self):
        cl = {
            "members": ["600519", "000001"],
            "return_model": {"coefficients": {"momentum": 0.5}},
            "oos_gate": _passed_gate(yhat=1.2),
            "factor_ic_panel": _ic_panel(ic=0.05, icir=0.4),
        }
        info = classify_cluster_fit_tier(cl)
        self.assertEqual(info["fit_tier"], "A")
        self.assertEqual(info["fit_tier_label"], "强")

    def test_b_when_passed_but_yhat_nonpositive(self):
        cl = {
            "members": ["600519", "000001"],
            "return_model": {"coefficients": {"momentum": 0.5}},
            "oos_gate": _passed_gate(yhat=-0.2),
            "factor_ic_panel": _ic_panel(),
        }
        info = classify_cluster_fit_tier(cl)
        self.assertEqual(info["fit_tier"], "B")
        self.assertIn("yhat_oos_nonpositive", info["fit_tier_reason"])

    def test_c_singleton_and_failed(self):
        single = classify_cluster_fit_tier(
            {"label": "G2", "members": ["601318"], "singleton": True}
        )
        self.assertEqual(single["fit_tier"], "C")
        failed = classify_cluster_fit_tier(
            {
                "members": ["600519", "000001"],
                "return_model": {"coefficients": {"momentum": 0.5}},
                "oos_gate": {
                    "ok": True,
                    "passed": False,
                    "skipped": False,
                    "reason": "oos_worse_-2.1pp",
                },
            }
        )
        self.assertEqual(failed["fit_tier"], "C")
        self.assertEqual(failed["fit_tier_reason"], "oos_worse_-2.1pp")

    def test_c_skipped_and_no_model(self):
        skipped = classify_cluster_fit_tier(
            {
                "members": ["600519", "000001"],
                "return_model": {"coefficients": {"momentum": 0.5}},
                "oos_gate": {"ok": False, "passed": False, "skipped": True, "reason": "gate_disabled"},
            }
        )
        self.assertEqual(skipped["fit_tier"], "C")
        self.assertEqual(skipped["fit_tier_reason"], "gate_disabled")
        no_model = classify_cluster_fit_tier(
            {"members": ["600519", "000001"], "oos_gate": _passed_gate()}
        )
        self.assertEqual(no_model["fit_tier"], "C")
        self.assertEqual(no_model["fit_tier_reason"], "no_return_model")

    def test_attach_summary(self):
        report = {
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "return_model": {"coefficients": {"momentum": 0.5}},
                    "oos_gate": _passed_gate(yhat=0.8),
                    "factor_ic_panel": _ic_panel(ic=0.03, icir=0.2),
                },
                {
                    "label": "G2",
                    "members": ["601318"],
                    "singleton": True,
                },
            ]
        }
        out = attach_cluster_fit_tiers(report)
        self.assertEqual(out["clusters"][0]["fit_tier"], "A")
        self.assertEqual(out["clusters"][1]["fit_tier"], "C")
        self.assertEqual(out["fit_tier_summary"]["A"], 1)
        self.assertEqual(out["fit_tier_summary"]["B"], 0)
        self.assertEqual(out["fit_tier_summary"]["C"], 1)
        self.assertEqual(out["fit_tier_summary"]["n"], 2)

    def test_normalize_universe_fit_tiers(self):
        from core.signal.cluster.fit_tier import (
            normalize_universe_fit_tiers,
            universe_fit_tiers_unrestricted,
        )

        self.assertEqual(normalize_universe_fit_tiers(None), ["A", "B", "C"])
        self.assertEqual(normalize_universe_fit_tiers([]), ["A", "B", "C"])
        self.assertEqual(normalize_universe_fit_tiers("A"), ["A"])
        self.assertEqual(normalize_universe_fit_tiers(["B", "A", "A"]), ["A", "B"])
        self.assertTrue(universe_fit_tiers_unrestricted(["A", "B", "C"]))
        self.assertFalse(universe_fit_tiers_unrestricted(["A"]))

    def test_filter_codes_by_fit_tiers(self):
        from core.signal.cluster.fit_tier import filter_codes_by_fit_tiers

        mapping = {"600519": "A", "000001": "B", "601318": "C"}
        kept, meta = filter_codes_by_fit_tiers(
            ["600519", "000001", "601318", "000002"],
            tiers=["A"],
            keep=["601318"],
            code_tiers=mapping,
        )
        self.assertEqual(kept, ["600519", "601318"])
        self.assertEqual(meta["n_dropped"], 2)
        self.assertEqual(meta["n_kept_held"], 1)
        self.assertEqual(meta["n_unmapped"], 1)
        all_kept, all_meta = filter_codes_by_fit_tiers(
            ["600519", "000001"],
            tiers=["A", "B", "C"],
            code_tiers=mapping,
        )
        self.assertEqual(all_kept, ["600519", "000001"])
        self.assertTrue(all_meta["unrestricted"])
        self.assertEqual(meta["n_by_tier"], {"A": 1, "B": 1, "C": 1})

    def test_last_report_a_not_overwritten_by_thin_live_b(self):
        from unittest.mock import patch
        from core.signal.cluster.fit_tier import (
            filter_codes_by_fit_tiers,
            load_code_fit_tier_map,
        )

        last = {
            "success": True,
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "fit_tier": "A",
                    "return_model": {"coefficients": {"momentum": 0.5}},
                    "oos_gate": _passed_gate(yhat=1.2),
                    "factor_ic_panel": _ic_panel(ic=0.05, icir=0.4),
                }
            ],
            "code_map": {
                "600519": {"fit_tier": "A", "cluster_label": "G1"},
                "000001": {"fit_tier": "A", "cluster_label": "G1"},
            },
        }
        thin = {
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "fit_tier": "B",
                }
            ],
            "code_map": {
                "600519": {"fit_tier": "B"},
                "000001": {"fit_tier": "B"},
                "601318": {"fit_tier": "C"},
            },
        }
        with patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value=thin,
        ), patch(
            "core.signal.cluster.live.load_research_cluster_weights",
            return_value=thin,
        ), patch(
            "core.signal.cluster.job_hydrate.load_latest_cluster_report",
            return_value=last,
        ):
            mapping = load_code_fit_tier_map(prefer_research=True)
        self.assertEqual(mapping["600519"], "A")
        self.assertEqual(mapping["000001"], "A")
        self.assertEqual(mapping["601318"], "C")
        kept, meta = filter_codes_by_fit_tiers(
            ["600519", "000001", "601318"],
            tiers=["A"],
            code_tiers=mapping,
        )
        self.assertEqual(kept, ["600519", "000001"])
        self.assertEqual(meta["n_by_tier"]["A"], 2)

    def test_attach_force_false_keeps_stored_a(self):
        report = {
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "fit_tier": "A",
                    "fit_tier_label": "强",
                    "fit_tier_reason": "stored",
                    "return_model": {"coefficients": {"momentum": 0.5}},
                    "oos_gate": _passed_gate(yhat=-1.0),
                }
            ]
        }
        out = attach_cluster_fit_tiers(report, force=False)
        self.assertEqual(out["clusters"][0]["fit_tier"], "A")
        forced = attach_cluster_fit_tiers(report, force=True)
        self.assertEqual(forced["clusters"][0]["fit_tier"], "B")

    def test_cluster_live_fit_tiers_payload_keeps_report_a(self):
        from unittest.mock import patch
        from core.signal.cluster.fit_tier import load_code_fit_tier_map
        from quant.services.quant_service_factors import QuantFactorMixin

        last = {
            "success": True,
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519"],
                    "fit_tier": "A",
                    "return_model": {"coefficients": {"momentum": 0.5}},
                    "oos_gate": _passed_gate(yhat=1.2),
                    "factor_ic_panel": _ic_panel(ic=0.05, icir=0.4),
                }
            ],
            "code_map": {"600519": {"fit_tier": "A", "cluster_label": "G1"}},
        }
        thin = {
            "clusters": [
                {"label": "G1", "members": ["600519"], "fit_tier": "B"}
            ],
            "code_map": {"600519": {"fit_tier": "B"}, "000002": {"fit_tier": "C"}},
        }
        with patch(
            "core.signal.cluster.live.load_active_cluster_weights",
            return_value=thin,
        ), patch(
            "core.signal.cluster.live.load_research_cluster_weights",
            return_value=thin,
        ), patch(
            "core.signal.cluster.job_hydrate.load_latest_cluster_report",
            return_value=last,
        ):
            mapping = load_code_fit_tier_map(prefer_research=True)
            out = QuantFactorMixin().cluster_live_fit_tiers()
        self.assertEqual(mapping["600519"], "A")
        self.assertTrue(out["success"])
        self.assertEqual(out["code_fit_tiers"]["600519"], "A")
        self.assertEqual(out["code_fit_tiers"]["000002"], "C")
        self.assertGreaterEqual(out["fit_tier_counts"]["A"], 1)


if __name__ == "__main__":
    unittest.main()
