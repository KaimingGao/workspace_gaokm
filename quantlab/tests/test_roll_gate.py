"""滚动验证：落盘 / 进度 job_id 与参数夹紧。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock


class TestRollGateHardening(unittest.TestCase):
    def test_persist_last_stamps_and_json_safe(self):
        from core.research import roll_gate as rg

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "last.json")
            with mock.patch.object(rg, "last_path", return_value=path):
                out = rg.persist_last(
                    {
                        "success": True,
                        "job_id": "abc123",
                        "hit_pct": float("nan"),
                        "nested": {"x": float("inf")},
                    }
                )
                self.assertEqual(out, path)
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                self.assertEqual(doc.get("job_id"), "abc123")
                # NaN/Inf 不得让 json.load 失败；json_safe 后应为可序列化值
                self.assertTrue(doc.get("success"))

    def test_attach_saved_at_keeps_finished_and_fills_mtime(self):
        from core.research.roll_gate import attach_saved_at

        stamped = attach_saved_at(
            {"finished_at": "2026-11-03T04:14:00+00:00"}, "/no/such.json"
        )
        self.assertEqual(stamped.get("finished_at"), "2026-11-03T04:14:00+00:00")
        self.assertNotIn("saved_at", stamped)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "last.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"success": True}, f)
            out = attach_saved_at({"success": True}, path)
            self.assertTrue(str(out.get("saved_at") or "").strip())
            kept = attach_saved_at(
                {"success": True, "updated_at": "2026-11-03T01:00:00+00:00"},
                path,
            )
            self.assertNotIn("saved_at", kept)
            self.assertEqual(kept.get("updated_at"), "2026-11-03T01:00:00+00:00")

    def test_load_last_falls_back_to_legacy_filename(self):
        from core.research import roll_gate as rg

        with tempfile.TemporaryDirectory() as td:
            canon = os.path.join(td, "roll_gate_last.json")
            legacy = os.path.join(td, "roll_gate_top1_last.json")
            with open(legacy, "w", encoding="utf-8") as f:
                json.dump({"success": True, "from": "legacy"}, f)
            with mock.patch.object(rg, "last_path", return_value=canon), mock.patch(
                "core.paths.ROLL_GATE_LAST_LEGACY_PATH", legacy
            ):
                doc = rg.load_last()
                self.assertEqual(doc.get("from"), "legacy")
                self.assertEqual(rg.resolved_last_path(), legacy)

    def test_start_job_clears_partial(self):
        from core.research import roll_gate as rg

        class _Slot:
            def reclaim_if_stale(self):
                return False

            def try_start(self, **kwargs):
                return "jid99", {"id": "jid99", "status": "running"}

            def get(self):
                return {"id": "jid99", "status": "running"}

        with mock.patch("core.job_progress.roll_gate_job", _Slot()), mock.patch.object(
            rg, "_set_progress"
        ) as set_prog, mock.patch("threading.Thread") as th:
            # 不跑 worker，只断言开槽瞬间清掉旧 partial
            th.return_value.start = lambda: None
            out = rg.start_roll_gate_job()
            self.assertTrue(out.get("success"))
            self.assertFalse(out.get("reused"))
            cleared = [
                c
                for c in set_prog.call_args_list
                if c.kwargs.get("partial") is None and c.kwargs.get("job_id") == "jid99"
            ]
            self.assertTrue(cleared, set_prog.call_args_list)

    def test_arm_stats_skips_nonfinite_labels(self):
        from core.research.roll_gate import _arm_stats

        out = _arm_stats(
            [
                {"empty": False, "hit": 1, "label": 1.0},
                {"empty": False, "hit": 0, "label": float("nan")},
                {"empty": False, "hit": 1, "label": float("inf")},
            ]
        )
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["hits"], 2)
        self.assertEqual(out["mean_ret"], 1.0)

    def test_sum_arm_skips_nan_mean(self):
        from core.research.roll_gate import _sum_arm

        out = _sum_arm(
            [
                {"oo": {"hits": 1, "n": 2, "empty": 0, "mean_ret": 1.0}},
                {"oo": {"hits": 0, "n": 1, "empty": 0, "mean_ret": float("nan")}},
            ],
            "oo",
        )
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["mean_ret"], 1.0)

    def test_tier_a_codes_intersects_watching(self):
        from core.research import roll_gate as rg

        with mock.patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["600000", "600001", "600002"]},
        ), mock.patch(
            "core.research.predictability_tiers.load_predictability_tiers_last",
            return_value={
                "success": True,
                "rows": [
                    {"code": "600000", "tier": "A"},
                    {"code": "600001", "tier": "B"},
                    {"code": "999999", "tier": "A"},
                ],
            },
        ):
            self.assertEqual(rg._tier_a_codes(), ["600000"])

    def test_tier_a_codes_empty_without_report(self):
        from core.research import roll_gate as rg

        with mock.patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["600000"]},
        ), mock.patch(
            "core.research.predictability_tiers.load_predictability_tiers_last",
            return_value=None,
        ):
            self.assertEqual(rg._tier_a_codes(), [])

    def test_pick_gated_requires_rank_score_above_min(self):
        from core.research.roll_gate import _pick_gated_top_n

        day = {
            "codes": ["A", "B"],
            "ys": [1.0, -0.5],
        }
        # 相对分最高者 A=0.25 ≤ 0.3 → 空闸
        out = _pick_gated_top_n(
            day,
            y_preds=[1.0, 0.5],
            rank_preds=[0.25, 0.10],
            y_min=0.2,
            rank_score_min=0.3,
        )
        self.assertTrue(out["empty"])
        self.assertEqual(out["reason"], "rank_score")
        self.assertEqual(out["code"], "A")

        # 相对分最高者 B=0.45 > 0.3 且 ŷ 过线 → 有仓
        hit = _pick_gated_top_n(
            day,
            y_preds=[0.1, 0.8],
            rank_preds=[0.20, 0.45],
            y_min=0.2,
            rank_score_min=0.3,
        )
        self.assertFalse(hit["empty"])
        self.assertEqual(hit["code"], "B")
        self.assertEqual(hit["hit"], 0)  # label=-0.5
        self.assertEqual(hit["n_picks"], 1)

    def test_pick_gated_top_n_by_rank_score(self):
        from core.research.roll_gate import _pick_gated_top_n

        day = {
            "codes": ["A", "B", "C", "D"],
            "ys": [1.0, 2.0, -1.0, 0.5],
        }
        # A/B/C 过闸；D 分不够。top_n=2 → 按分取 A,B；等权 label=(1+2)/2
        out = _pick_gated_top_n(
            day,
            y_preds=[1.0, 0.8, 0.5, 1.0],
            rank_preds=[0.9, 0.8, 0.7, 0.2],
            y_min=0.2,
            rank_score_min=0.3,
            top_n=2,
        )
        self.assertFalse(out["empty"])
        self.assertEqual(out["codes"], ["A", "B"])
        self.assertEqual(out["n_picks"], 2)
        self.assertEqual(out["code"], "A")
        self.assertAlmostEqual(out["label"], 1.5)
        self.assertEqual(out["hit"], 1)


if __name__ == "__main__":
    unittest.main()
