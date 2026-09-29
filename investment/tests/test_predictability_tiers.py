"""滚动可预测性票档：命中率为主、IC 为辅。"""

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
    def test_assign_tier_thresholds(self):
        from core.research.predictability_tiers import assign_tier, effective_min_n

        self.assertEqual(assign_tier(0.60, 25), "A")
        self.assertEqual(assign_tier(0.55, 20), "A")
        self.assertEqual(assign_tier(0.52, 25), "B")
        self.assertEqual(assign_tier(0.50, 25), "B")
        self.assertEqual(assign_tier(0.49, 25), "C")
        self.assertEqual(assign_tier(0.90, 10), "B")  # n 不足但命中尚可
        self.assertEqual(assign_tier(0.40, 10), "C")  # n 不足且命中很差
        self.assertEqual(assign_tier(0.0, 2), "C")
        self.assertEqual(assign_tier(0.50, 5), "B")  # 边界：不足窗但 ≥B 门槛
        self.assertEqual(assign_tier(None, 0), "C")
        self.assertEqual(assign_tier(0.8, 0), "C")
        self.assertEqual(assign_tier(None, 8), "C")  # 有日数但无有效命中
        self.assertEqual(effective_min_n(20, 40), 20)
        self.assertEqual(effective_min_n(20, 10), 9)
        self.assertEqual(effective_min_n(20, 5), 5)
        self.assertEqual(effective_min_n(20, 3), 3)

    def test_split_holdout_and_ledger_window(self):
        from core.research.predictability_tiers import (
            build_predictability_tiers,
            split_holdout_windows,
            tier_code_set,
        )

        dates = [f"2025-08-{d:02d}" for d in range(1, 11)]  # 10 days ascending
        newest_first = list(reversed(dates))
        with patch(
            "core.score_ledger.io.list_ledger_dates", return_value=newest_first
        ):
            split = split_holdout_windows(10)
        self.assertTrue(split.get("ok"), split)
        self.assertEqual(split.get("tier_n"), 5)
        self.assertEqual(split["tier_dates"][-1], "2025-08-05")
        self.assertNotIn("bt_dates", split)
        self.assertNotIn("bt_lookback", split)

        def _ledger(d):
            return {
                "success": True,
                "empty": False,
                "rows": [
                    {"code": "000001", "name": "A", "yhat": 1.0},
                    {"code": "000002", "name": "B", "yhat": 1.0},
                ],
            }

        def _outcomes(d):
            # only 000001 hits on early dates
            return {
                "success": True,
                "by_code": {
                    "000001": {"realized_h": 1.0, "sign_hit": True},
                    "000002": {"realized_h": -1.0, "sign_hit": False},
                },
            }

        with patch(
            "core.score_ledger.io.load_ledger", side_effect=_ledger
        ), patch(
            "core.score_ledger.io.load_outcomes", side_effect=_outcomes
        ):
            rep = build_predictability_tiers(
                codes=["000001", "000002"],
                ledger_dates=split["tier_dates"],
                min_n=3,
                head="oo",
                persist=False,
            )
        self.assertTrue(rep.get("success"), rep)
        self.assertEqual(rep.get("n_ledger_dates"), 5)
        keep = tier_code_set(rep, ["A", "B"])
        self.assertIn("000001", keep)
        # 000002 hit=0 → C when n enough under new rule
        by = {r["code"]: r for r in rep.get("rows") or []}
        self.assertEqual(by["000002"]["tier"], "C")
        self.assertNotIn("000002", keep)

    def test_build_holdout_half_tiers_meta(self):
        from core.research.predictability_tiers import build_holdout_half_tiers

        dates = [f"2025-08-{d:02d}" for d in range(1, 11)]
        newest_first = list(reversed(dates))

        def _ledger(d):
            return {
                "success": True,
                "empty": False,
                "rows": [{"code": "000001", "name": "A", "yhat": 1.0}],
            }

        def _outcomes(d):
            return {
                "success": True,
                "by_code": {"000001": {"realized_h": 1.0, "sign_hit": True}},
            }

        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            with patch(
                "core.score_ledger.io.list_ledger_dates", return_value=newest_first
            ), patch(
                "core.score_ledger.io.load_ledger", side_effect=_ledger
            ), patch(
                "core.score_ledger.io.load_outcomes", side_effect=_outcomes
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ):
                rep = build_holdout_half_tiers(
                    10,
                    pool="watching",
                    min_n=3,
                    head="oo",
                    persist=True,
                    watching_codes=["000001"],
                )
                self.assertTrue(rep.get("success"), rep)
                self.assertEqual(rep.get("protocol"), "holdout_half")
                self.assertEqual(rep.get("holdout_n"), 10)
                self.assertEqual(rep.get("tier_n"), 5)
                self.assertNotIn("bt_lookback", rep)
                self.assertNotIn("bt_dates", rep)
                self.assertIn("holdout_split", rep)
                from core.research.predictability_tiers import (
                    load_predictability_tiers_last,
                    save_predictability_tiers_last,
                )

                save_predictability_tiers_last(rep)
                self.assertTrue(os.path.isfile(last_p), last_p)
                loaded = load_predictability_tiers_last()
                self.assertTrue(loaded and loaded.get("protocol") == "holdout_half")

    def test_build_tiers_defaults_to_watching(self):
        from core.research.predictability_tiers import (
            build_predictability_tiers,
            load_predictability_tiers_last,
        )

        dates = [f"2025-06-{d:02d}" for d in range(1, 26)]

        def _ledger(d):
            return {
                "success": True,
                "empty": False,
                "rows": [
                    {"code": "000001", "name": "平安", "yhat": 1.2},
                    {"code": "000002", "name": "万科", "yhat": 0.8},
                    {"code": "000003", "name": "薄样", "yhat": 0.6},
                    {"code": "000099", "name": "仅宇宙", "yhat": 0.5},
                ],
            }

        def _outcomes(d):
            day_i = dates.index(d) if d in dates else 0
            hit2 = day_i % 2 == 0
            return {
                "success": True,
                "by_code": {
                    "000001": {"realized_h": 1.0, "sign_hit": True},
                    "000002": {
                        "realized_h": 1.0 if hit2 else -1.0,
                        "sign_hit": hit2,
                    },
                    "000003": {"realized_h": -1.0, "sign_hit": False},
                    "000099": {"realized_h": 1.0, "sign_hit": True},
                },
            }

        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "live")
            os.makedirs(live, exist_ok=True)
            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=os.path.join(live, "predictability_tiers_last.json"),
            ), patch(
                "core.score_ledger.io.list_ledger_dates",
                return_value=list(reversed(dates)),
            ), patch(
                "core.score_ledger.io.load_ledger", side_effect=_ledger
            ), patch(
                "core.score_ledger.io.load_outcomes", side_effect=_outcomes
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
                    lookback_dates=25,
                    min_n=20,
                    head="oo",
                    persist=True,
                )
                self.assertTrue(rep.get("success"), rep)
                self.assertEqual(rep.get("pool"), "watching")
                self.assertEqual(rep.get("pool_source"), "watching")
                self.assertEqual(rep.get("pool_count"), 4)
                by = {r["code"]: r for r in rep.get("rows") or []}
                self.assertIn("000001", by)
                self.assertIn("600000", by)  # 观察池无账本 → C
                self.assertEqual(by["600000"]["tier"], "C")
                self.assertNotIn("000099", by)  # 仅研究宇宙、不在观察池
                self.assertEqual(by["000001"]["tier"], "A")
                self.assertEqual(by["000002"]["tier"], "B")
                self.assertEqual(by["000003"]["tier"], "C")
                counts = rep.get("counts") or {}
                self.assertEqual(counts.get("A"), 1)
                self.assertEqual(counts.get("B"), 1)
                self.assertEqual(counts.get("C"), 2)

                last = load_predictability_tiers_last()
                self.assertIsNotNone(last)
                self.assertEqual(last.get("pool"), "watching")

                ru = build_predictability_tiers(
                    pool="research_universe",
                    lookback_dates=25,
                    min_n=20,
                    head="oo",
                    persist=False,
                )
                ru_codes = {r["code"] for r in ru.get("rows") or []}
                self.assertIn("000099", ru_codes)
                self.assertEqual(ru.get("pool"), "research_universe")

                led = build_predictability_tiers(
                    pool="ledger",
                    lookback_dates=25,
                    min_n=20,
                    head="oo",
                    persist=False,
                )
                led_codes = {r["code"] for r in led.get("rows") or []}
                self.assertIn("000099", led_codes)
                self.assertNotIn("600000", led_codes)  # 无样本不进
                self.assertEqual(led.get("pool"), "ledger")

                # 账本短：min_n 20 → 生效 9，命中高且 n=9 可进 A
                short_dates = [f"2025-07-{d:02d}" for d in range(1, 11)]

                def _ledger_short(_d):
                    return {
                        "success": True,
                        "empty": False,
                        "rows": [{"code": "000001", "name": "平安", "yhat": 1.2}],
                    }

                def _outcomes_short(_d):
                    return {
                        "success": True,
                        "by_code": {"000001": {"realized_h": 1.0, "sign_hit": True}},
                    }

                with patch(
                    "core.score_ledger.io.list_ledger_dates",
                    return_value=list(reversed(short_dates)),
                ), patch(
                    "core.score_ledger.io.load_ledger", side_effect=_ledger_short
                ), patch(
                    "core.score_ledger.io.load_outcomes", side_effect=_outcomes_short
                ):
                    short = build_predictability_tiers(
                        pool="watching",
                        lookback_dates=40,
                        min_n=20,
                        head="oo",
                        persist=False,
                        watching_codes=["000001"],
                    )
                    self.assertEqual(short.get("min_n_effective"), 9)
                    self.assertTrue((short.get("thresholds") or {}).get("min_n_adapted"))
                    by_s = {r["code"]: r for r in short.get("rows") or []}
                    self.assertEqual(by_s["000001"]["tier"], "A")
                    self.assertEqual(by_s["000001"]["n_valid"], 10)

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
                self.assertIn("000003", kept)  # held C kept
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
