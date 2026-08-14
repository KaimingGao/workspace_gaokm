"""双层 predicted_score：字段契约与 F1 买入闸。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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
                    "fusion_mode": "f2",
                    "tau": "open",
                    "min_predicted_score_tau": 0.0,
                }
            },
        )
        self.assertEqual(item["predicted_score"], 0.5)
        self.assertEqual(item["predicted_score_eod"], 0.5)
        self.assertEqual(item["predicted_score_tau"], 0.12)
        self.assertEqual(item["score_rem"], 0.12)
        self.assertEqual(item["as_of_tau"], "open")
        self.assertIn("formula", item["y_spec_tau"])
        self.assertEqual(item["features_tau"].get("gap_pct"), 2.5)
        self.assertNotIn("momentum", item["features_tau"])
        self.assertEqual(item["dual_score_fusion"], "f2")
        self.assertIn("formula_terms_tau", item)
        self.assertIn("score_formula_terms_tau", item)

    def test_ensure_formula_terms_tau_from_eod_terms(self):
        """旧簿无 τ 组成时，从 EOD z + 组模型反推后与 rem 对齐。"""
        from unittest.mock import MagicMock, patch

        from core.signal.dual_score import ensure_formula_terms_tau

        eod_rm = MagicMock()
        eod_rm.z_means = {"momentum": 50.0}
        eod_rm.z_stds = {"momentum": 10.0}
        eod_rm.standardized = True
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
            "core.signal.dual_score._eod_return_model_for_item", return_value=eod_rm
        ), patch(
            "quant.research.rem_ridge.load_rem_model", return_value=rem_doc
        ):
            expl = ensure_formula_terms_tau(item)
        self.assertIsNotNone(expl)
        self.assertTrue(expl.get("terms"))
        self.assertAlmostEqual(float(expl["total"]), 0.1 + 0.5 * 1.0, places=4)

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

    def test_buy_gate_allows_missing_by_default(self):
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

    def test_blend_rank_key(self):
        from core.signal.dual_score import (
            apply_tau_score_fields,
            compute_predicted_score_blend,
            rank_key_for_item,
        )

        cfg = {
            "dual_score": {
                "fusion_mode": "f2",
                "w_eod": 0.5,
                "w_tau": 0.5,
            }
        }
        item = {"predicted_score": 1.0, "score": 1.0}
        apply_tau_score_fields(
            item, rem_yhat=0.0, gap_pct=0.0, feats={}, config=cfg
        )
        self.assertAlmostEqual(item["predicted_score_blend"], 0.5, places=5)
        self.assertAlmostEqual(
            compute_predicted_score_blend(item, config=cfg), 0.5, places=5
        )
        self.assertAlmostEqual(
            rank_key_for_item(item, config=cfg), 0.5, places=5
        )
        self.assertEqual(item["dual_score_fusion"], "f2")

    def test_blend_weights_survive_flattened_cfg_passthrough(self):
        """回测 attach→apply 曾二次传入 flatten dual，权重不得回落 0.5/0.5。"""
        from core.signal.dual_score import (
            apply_tau_score_fields,
            attach_dual_score_pit,
            get_dual_score_cfg,
        )

        nested = {"dual_score": {"w_eod": 0.1, "w_tau": 0.9, "fusion_mode": "f2"}}
        flat = get_dual_score_cfg(nested)
        self.assertAlmostEqual(flat["w_eod"], 0.1)
        self.assertAlmostEqual(flat["w_tau"], 0.9)
        again = get_dual_score_cfg(flat)
        self.assertAlmostEqual(again["w_eod"], 0.1)
        self.assertAlmostEqual(again["w_tau"], 0.9)

        item = {"predicted_score": 2.0, "score": 2.0, "stock_code": "T"}
        apply_tau_score_fields(
            item, rem_yhat=-1.0, gap_pct=1.0, feats={}, config=flat
        )
        self.assertAlmostEqual(item["predicted_score_blend"], -0.7, places=5)
        self.assertEqual(item["dual_score_weights"]["w_eod"], 0.1)
        self.assertEqual(item["dual_score_weights"]["w_tau"], 0.9)

        item2 = {
            "predicted_score": 2.0,
            "score": 2.0,
            "stock_code": "T2",
            "sub_scores": {},
        }
        attach_dual_score_pit(item2, quote=None, bars=None, config=nested)
        self.assertEqual(item2["dual_score_weights"]["w_eod"], 0.1)
        self.assertEqual(item2["dual_score_weights"]["w_tau"], 0.9)
        yt = item2.get("predicted_score_tau")
        if yt is None:
            self.assertAlmostEqual(item2["predicted_score_blend"], 2.0, places=5)
        else:
            self.assertAlmostEqual(
                item2["predicted_score_blend"], 0.1 * 2.0 + 0.9 * float(yt), places=5
            )

    def test_book_fields_override_stale_fusion(self):
        from core.signal.dual_score import dual_score_book_fields

        out = dual_score_book_fields(
            {"dual_score_fusion": "f1", "predicted_score_tau": 0.1}
        )
        self.assertEqual(out["dual_score_fusion"], "f2")

    def test_tau_shadow_book_reranks(self):
        from core.signal.dual_score import build_tau_shadow_book, compare_book_overlap

        eligible = [
            {
                "stock_code": "A",
                "score": 2.0,
                "predicted_score": 2.0,
                "predicted_score_tau": 0.1,
            },
            {
                "stock_code": "B",
                "score": 1.5,
                "predicted_score": 1.5,
                "predicted_score_tau": 0.9,
            },
            {
                "stock_code": "C",
                "score": 1.2,
                "predicted_score": 1.2,
                "predicted_score_tau": 0.5,
            },
        ]
        eod = eligible[:2]  # A, B by EOD
        tau_book, meta = build_tau_shadow_book(
            eligible, max_names=2, eod_book=eod
        )
        self.assertEqual([r["stock_code"] for r in tau_book], ["B", "C"])
        self.assertEqual(tau_book[0]["rank_key"], "predicted_score_tau")
        self.assertEqual(meta["vs_eod_book"]["overlap"], 1)
        ov = compare_book_overlap(eod, tau_book)
        self.assertEqual(ov["only_eod"], ["A"])
        self.assertEqual(ov["only_tau"], ["C"])

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

    def test_save_dual_score_fusion_f2(self):
        import json
        import os
        import tempfile

        from core.signal.dual_score_config import read_dual_score_public, save_dual_score

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"dual_score": {"fusion_mode": "f1"}, "weights": {"x": 1}}, f)
            os.environ["INVESTMENT_SIGNAL_CONFIG"] = path
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
                self.assertEqual(out["dual_score"]["fusion_mode"], "f2")
                self.assertEqual(out["dual_score"]["min_predicted_score_tau"], 0.1)
                self.assertEqual(out["dual_score"]["w_eod"], 0.6)
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertEqual(raw["weights"], {"x": 1})
                self.assertEqual(raw["dual_score"]["fusion_mode"], "f2")
                pub = read_dual_score_public()
                self.assertEqual(pub["fusion_mode"], "f2")
            finally:
                os.environ.pop("INVESTMENT_SIGNAL_CONFIG", None)
                import core.signal.config as cfg_mod

                cfg_mod._cached = None


class TestPromoteOosCompare(unittest.TestCase):
    def test_compare_blocks_worse_than_active_when_strict(self):
        """显式关闭 allow_worse 时，相对 active 仍硬拦。"""
        from core.signal.cluster_oos_labels import compare_oos_vs_active

        draft = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": False}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        active = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": True}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        err, detail = compare_oos_vs_active(
            draft, active, max_oos_fail_rate=0.9, allow_worse_than_active=False
        )
        self.assertIsNotNone(err)
        self.assertIn("差于 active", str(err))
        self.assertEqual(detail["draft"]["fail_rate"], 0.5)

    def test_compare_worse_than_active_is_soft_by_default(self):
        from core.signal.cluster_oos_labels import compare_oos_vs_active

        draft = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": False}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        active = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": True}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        err, detail = compare_oos_vs_active(
            draft, active, max_oos_fail_rate=0.9, allow_worse_than_active=True
        )
        self.assertIsNone(err)
        self.assertTrue(any("差于 active" in str(w) for w in (detail.get("warnings") or [])))

    def test_compare_allows_when_configured(self):
        from core.signal.cluster_oos_labels import compare_oos_vs_active

        draft = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": False}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        active = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": True}},
                {"label": "G2", "oos_gate": {"passed": True}},
            ]
        }
        err, _ = compare_oos_vs_active(
            draft, active, max_oos_fail_rate=0.9, allow_worse_than_active=True
        )
        self.assertIsNone(err)

    def test_absolute_max_rate(self):
        from core.signal.cluster_oos_labels import compare_oos_vs_active

        draft = {
            "clusters": [
                {"label": "G1", "oos_gate": {"passed": False}},
                {"label": "G2", "oos_gate": {"passed": False}},
            ]
        }
        err, _ = compare_oos_vs_active(
            draft, None, max_oos_fail_rate=0.5, allow_worse_than_active=True
        )
        self.assertIsNotNone(err)
        self.assertIn("过高", str(err))

    def test_partition_preflight_promote_ready(self):
        from core.signal.cluster_oos_labels import compare_partition_vs_active

        draft = {
            "horizon_days": 1,
            "clusters": [
                {
                    "label": "G1",
                    "members": ["000938", "600000"],
                    "oos_gate": {"passed": True},
                    "ols": {"r2": 0.2},
                    "ic": 0.05,
                },
                {
                    "label": "G2",
                    "members": ["603019"],
                    "oos_gate": {"passed": True},
                    "ols": {"r2": 0.1},
                    "ic": 0.02,
                },
            ],
            "k_selection": {
                "greedy_refine": {
                    "ok": True,
                    "mode": "focused",
                    "n_swaps": 1,
                    "improved": True,
                }
            },
        }
        active = {
            "version": 3,
            "clusters": [
                {
                    "label": "G1",
                    "members": ["000938"],
                    "oos_gate": {"passed": True},
                    "ols": {"r2": 0.15},
                    "ic": 0.01,
                },
                {
                    "label": "G2",
                    "members": ["603019", "600000"],
                    "oos_gate": {"passed": False},
                    "ols": {"r2": 0.05},
                    "ic": -0.02,
                },
            ],
        }
        out = compare_partition_vs_active(
            draft, active, focus_codes=["000938", "603019"]
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["promote_ready"])
        self.assertEqual(out["focus_draft"]["000938"]["label"], "G1")
        self.assertEqual(out["draft"]["greedy_refine"]["mode"], "focused")
        self.assertIsNotNone(out["delta"]["oos_fail_rate"])
        self.assertLess(out["delta"]["oos_fail_rate"], 0)
