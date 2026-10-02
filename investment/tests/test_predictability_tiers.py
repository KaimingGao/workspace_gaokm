"""滚动可预测性票档：ŷ_oo Holdout OOS 前半。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestPredictabilityTiers(unittest.TestCase):
    def test_normalize_head_is_tau_or_oo(self):
        from core.research.predictability_tiers import HEAD_OO, HEAD_TAU, _normalize_head

        self.assertEqual(_normalize_head("tau"), HEAD_TAU)
        self.assertEqual(_normalize_head("y_τc"), HEAD_TAU)
        self.assertEqual(_normalize_head("ŷ_τc"), HEAD_TAU)
        self.assertEqual(_normalize_head("y_oc"), HEAD_OO)
        self.assertEqual(_normalize_head("y_tau"), HEAD_OO)
        self.assertEqual(_normalize_head(None), HEAD_OO)

    def test_slim_tier_rows_drops_series(self):
        from core.research.predictability_tiers import slim_tier_rows

        rows = slim_tier_rows(
            [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "tier": "A",
                    "hit_rate": 0.7,
                    "n_valid": 9,
                    "n_days": 10,
                    "ic": 0.12,
                    "yhats": [1, 2],
                    "realized": [1],
                    "dates": ["2026-01-01"],
                },
                "skip",
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], "A")
        self.assertNotIn("yhats", rows[0])
        self.assertNotIn("realized", rows[0])

    def test_assign_tier_thresholds(self):
        from core.research.predictability_tiers import assign_tier, effective_min_n

        self.assertEqual(assign_tier(0.60, 25), "A")
        self.assertEqual(assign_tier(0.55, 20), "B")  # <60% 不够 A
        self.assertEqual(assign_tier(0.52, 25), "B")
        self.assertEqual(assign_tier(0.50, 25), "B")
        self.assertEqual(assign_tier(0.49, 25), "C")
        self.assertEqual(assign_tier(0.90, 10), "B")
        self.assertEqual(assign_tier(0.40, 10), "C")
        self.assertEqual(assign_tier(0.0, 2), "C")
        self.assertEqual(assign_tier(0.50, 5), "B")
        self.assertEqual(assign_tier(None, 0), "C")
        self.assertEqual(assign_tier(0.8, 0), "C")
        self.assertEqual(assign_tier(None, 8), "C")
        # min_n = 分档窗 × 90%（向上取整）
        self.assertEqual(effective_min_n(20, 40), 36)
        self.assertEqual(effective_min_n(20, 10), 9)
        self.assertEqual(effective_min_n(20, 5), 5)
        self.assertEqual(effective_min_n(20, 3), 3)
        self.assertEqual(effective_min_n(20, 20), 18)
        self.assertEqual(effective_min_n(20, 0), 20)

    def test_split_holdout_half_dates(self):
        from core.research.predictability_tiers import (
            split_holdout_half_dates,
            split_holdout_windows,
            tier_code_set,
        )

        dates = [f"2025-08-{d:02d}" for d in range(1, 11)]
        split = split_holdout_half_dates(dates, holdout_n=10)
        self.assertTrue(split.get("ok"), split)
        self.assertEqual(split.get("tier_n"), 5)
        self.assertEqual(split["tier_dates"][-1], "2025-08-05")
        self.assertNotIn("bt_dates", split)

        bare = split_holdout_windows(10)
        self.assertFalse(bare.get("ok"))

        rep = {
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "C"},
            ]
        }
        keep = tier_code_set(rep, ["A", "B"])
        self.assertIn("000001", keep)
        self.assertNotIn("000002", keep)

    def test_build_holdout_half_tiers_oos(self):
        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            build_holdout_half_tiers,
            load_predictability_tiers_last,
            save_predictability_tiers_last,
            tier_code_set,
        )

        tier_dates = [f"2025-08-{d:02d}" for d in range(1, 6)]

        def _fake_accumulate(codes, **kwargs):
            acc = {
                "000001": {
                    "hits": 5,
                    "n_valid": 5,
                    "n_days": 5,
                    "yhats": [1.0] * 5,
                    "realized": [1.0] * 5,
                    "dates": list(tier_dates),
                },
                "000002": {
                    "hits": 0,
                    "n_valid": 5,
                    "n_days": 5,
                    "yhats": [1.0] * 5,
                    "realized": [-1.0] * 5,
                    "dates": list(tier_dates),
                },
            }
            return {
                "ok": True,
                "acc": acc,
                "tier_dates": list(tier_dates),
                "tier_n": 5,
                "holdout_split": {
                    "ok": True,
                    "holdout_n": 10,
                    "n_test": 10,
                    "n_ledger": 10,
                    "tier_dates": list(tier_dates),
                    "tier_n": 5,
                    "as_of_tier_last": tier_dates[-1],
                },
                "split_meta": {"eval_start": "2025-08-01", "test_days": tier_dates * 2},
                "n_train": 40,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_holdout_oos",
            }

        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            with patch(
                "core.research.predictability_tiers.accumulate_holdout_oos_by_code",
                side_effect=_fake_accumulate,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ), patch(
                "core.research.predictability_tiers._watching_name_map",
                return_value={"000001": "平安银行", "000002": "万科A", "600000": "浦发银行"},
            ):
                rep = build_holdout_half_tiers(
                    10,
                    pool="watching",
                    min_n=3,
                    head="oo",
                    persist=True,
                    watching_codes=["000001", "000002", "600000"],
                )
                self.assertTrue(rep.get("success"), rep)
                self.assertEqual(rep.get("protocol"), PROTOCOL_HOLDOUT_OOS)
                self.assertEqual(rep.get("source"), "oo_holdout_oos")
                self.assertEqual(rep.get("holdout_n"), 10)
                self.assertEqual(rep.get("tier_n"), 5)
                self.assertNotIn("bt_lookback", rep)
                by = {r["code"]: r for r in rep.get("rows") or []}
                self.assertEqual(by["000001"]["tier"], "A")
                self.assertEqual(by["000002"]["tier"], "C")
                self.assertEqual(by["600000"]["tier"], "C")  # pad missing
                self.assertEqual(by["000001"].get("name"), "平安银行")
                self.assertEqual(by["000002"].get("name"), "万科A")
                keep = tier_code_set(rep, ["A", "B"])
                self.assertIn("000001", keep)
                self.assertNotIn("000002", keep)

                save_predictability_tiers_last(rep)
                loaded = load_predictability_tiers_last()
                self.assertTrue(
                    loaded and loaded.get("protocol") == PROTOCOL_HOLDOUT_OOS
                )

                tau = build_holdout_half_tiers(10, head="tau", persist=False)
                self.assertFalse(tau.get("success"))

    def test_build_tiers_defaults_to_watching(self):
        from core.research.predictability_tiers import build_predictability_tiers

        tier_dates = [f"2025-06-{d:02d}" for d in range(1, 11)]

        def _fake_accumulate(codes, **kwargs):
            code_set = set(codes or [])
            acc = {}
            for code, hits, n in (
                ("000001", 10, 10),
                ("000002", 5, 10),
                ("000003", 0, 10),
                ("000099", 10, 10),
            ):
                if code not in code_set:
                    continue
                acc[code] = {
                    "hits": hits,
                    "n_valid": n,
                    "n_days": n,
                    "yhats": [1.0] * n,
                    "realized": [1.0 if hits else -1.0] * n,
                    "dates": list(tier_dates),
                }
            return {
                "ok": True,
                "acc": acc,
                "tier_dates": list(tier_dates),
                "tier_n": len(tier_dates),
                "holdout_split": {
                    "ok": True,
                    "holdout_n": 20,
                    "n_test": 20,
                    "tier_dates": list(tier_dates),
                    "tier_n": len(tier_dates),
                    "as_of_tier_last": tier_dates[-1],
                },
                "split_meta": {},
                "n_train": 50,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_holdout_oos",
            }

        with patch(
            "core.research.predictability_tiers.accumulate_holdout_oos_by_code",
            side_effect=_fake_accumulate,
        ), patch(
            "core.research_universe.resolve_research_codes",
            return_value={
                "codes": ["000001", "000002", "000003", "000099"],
                "count": 4,
                "source": "research_universe",
            },
        ), patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["000001", "000002", "000003", "600000"]},
        ):
            rep = build_predictability_tiers(
                lookback_dates=20,
                min_n=5,
                head="oo",
                persist=False,
            )
            self.assertTrue(rep.get("success"), rep)
            self.assertEqual(rep.get("pool"), "watching")
            by = {r["code"]: r for r in rep.get("rows") or []}
            self.assertIn("000001", by)
            self.assertIn("600000", by)
            self.assertNotIn("000099", by)
            self.assertEqual(by["000001"]["tier"], "A")
            self.assertEqual(by["000003"]["tier"], "C")

            ru = build_predictability_tiers(
                pool="research_universe",
                lookback_dates=20,
                min_n=5,
                head="oo",
                persist=False,
            )
            ru_codes = {r["code"] for r in ru.get("rows") or []}
            self.assertIn("000099", ru_codes)

            # ledger 池已废弃 → watching
            led = build_predictability_tiers(
                pool="ledger",
                lookback_dates=20,
                min_n=5,
                head="oo",
                persist=False,
            )
            self.assertEqual(led.get("pool"), "watching")

    def test_promote_and_live_filter(self):
        from core.research.predictability_tiers import (
            clear_predictability_tiers_live,
            filter_codes_by_predictability_live,
            live_tier_status,
            promote_predictability_tiers_to_live,
            save_predictability_tiers_last,
        )

        report = {
            "success": True,
            "counts": {"A": 1, "B": 1, "C": 1},
            "head": "oo",
            "head_label": "ŷ_oo",
            "n_ledger_dates": 20,
            "ledger_dates": ["2025-01-01", "2025-01-20"],
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "B"},
                {"code": "000003", "tier": "C"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            active_p = os.path.join(td, "predictability_tiers_active.json")
            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_active_path",
                return_value=active_p,
            ), patch(
                "core.live_config_manifest.write_live_config_manifest",
                return_value={},
            ):
                save_predictability_tiers_last(report)
                ok = promote_predictability_tiers_to_live()
                self.assertTrue(ok.get("ok"))
                st = live_tier_status()
                self.assertTrue(st.get("enabled"))
                self.assertEqual(st.get("allowed_tiers"), ["A", "B"])

                kept, meta = filter_codes_by_predictability_live(
                    ["000001", "000002", "000003", "000099"],
                    keep=["000003"],
                )
                self.assertTrue(meta.get("predictability_live"))
                self.assertIn("000001", kept)
                self.assertIn("000002", kept)
                self.assertIn("000003", kept)
                self.assertNotIn("000099", kept)

                clear_predictability_tiers_live()
                st2 = live_tier_status()
                self.assertFalse(st2.get("enabled"))
                kept2, meta2 = filter_codes_by_predictability_live(
                    ["000001", "000003"], keep=[]
                )
                self.assertTrue(meta2.get("unrestricted"))
                self.assertEqual(kept2, ["000001", "000003"])

        with tempfile.TemporaryDirectory() as td2:
            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=os.path.join(td2, "missing_last.json"),
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_active_path",
                return_value=os.path.join(td2, "active.json"),
            ):
                fail = promote_predictability_tiers_to_live()
                self.assertFalse(fail.get("ok"))


if __name__ == "__main__":
    unittest.main()
