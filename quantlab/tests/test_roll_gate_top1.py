"""滚动验证：落盘 / 进度 job_id 与参数夹紧。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock


class TestRollGateTop1Hardening(unittest.TestCase):
    def test_persist_last_stamps_and_json_safe(self):
        from core.research import roll_gate_top1 as rg

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
        from core.research.roll_gate_top1 import attach_saved_at

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

    def test_start_job_clears_partial(self):
        from core.research import roll_gate_top1 as rg

        class _Slot:
            def reclaim_if_stale(self):
                return False

            def try_start(self, **kwargs):
                return "jid99", {"id": "jid99", "status": "running"}

            def get(self):
                return {"id": "jid99", "status": "running"}

        with mock.patch("core.job_progress.roll_gate_top1_job", _Slot()), mock.patch.object(
            rg, "_set_progress"
        ) as set_prog, mock.patch("threading.Thread") as th:
            # 不跑 worker，只断言开槽瞬间清掉旧 partial
            th.return_value.start = lambda: None
            out = rg.start_roll_gate_top1_job()
            self.assertTrue(out.get("success"))
            self.assertFalse(out.get("reused"))
            cleared = [
                c
                for c in set_prog.call_args_list
                if c.kwargs.get("partial") is None and c.kwargs.get("job_id") == "jid99"
            ]
            self.assertTrue(cleared, set_prog.call_args_list)

    def test_arm_stats_skips_nonfinite_labels(self):
        from core.research.roll_gate_top1 import _arm_stats

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
        from core.research.roll_gate_top1 import _sum_arm

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
        from core.research import roll_gate_top1 as rg

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
        from core.research import roll_gate_top1 as rg

        with mock.patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["600000"]},
        ), mock.patch(
            "core.research.predictability_tiers.load_predictability_tiers_last",
            return_value=None,
        ):
            self.assertEqual(rg._tier_a_codes(), [])


if __name__ == "__main__":
    unittest.main()
