"""FH1：指针原子晋升 + force 审计。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@contextmanager
def _live_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, "live")
        hist = os.path.join(live, "cluster_weights_history")
        os.makedirs(hist, exist_ok=True)
        active = os.path.join(live, "cluster_weights_active.json")
        draft = os.path.join(live, "cluster_weights_draft.json")
        book = os.path.join(live, "cluster_book_active.json")
        pointer = os.path.join(live, "cluster_pointer.json")
        audit = os.path.join(live, "promote_audit.jsonl")
        cfg_path = os.path.join(tmp, "signal_config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"cluster_scoring": {"enabled": True, "mode": "off"}}, f)
        with patch("core.paths.LIVE_DIR", live), patch(
            "core.paths.CLUSTER_WEIGHTS_ACTIVE_PATH", active
        ), patch(
            "core.paths.CLUSTER_WEIGHTS_HISTORY_DIR", hist
        ), patch(
            "core.paths.CLUSTER_WEIGHTS_DRAFT_PATH", draft
        ), patch(
            "core.paths.CLUSTER_BOOK_ACTIVE_PATH", book
        ), patch(
            "core.paths.CLUSTER_POINTER_PATH", pointer
        ), patch(
            "core.paths.PROMOTE_AUDIT_PATH", audit
        ), patch(
            "core.paths.SIGNAL_CONFIG_PATH", cfg_path
        ), patch.dict(
            os.environ, {"INVESTMENT_SIGNAL_CONFIG": cfg_path}
        ):
            import core.signal.config as cfg_mod

            cfg_mod._cached = None
            yield {
                "live": live,
                "active": active,
                "pointer": pointer,
                "audit": audit,
                "book": book,
                "cfg": cfg_path,
            }


def _artifact():
    rm = {
        "intercept": 0.1,
        "coefficients": {"momentum": 0.5, "value": -0.2},
        "z_means": {},
        "z_stds": {},
        "standardized": False,
    }
    return {
        "created_at": "2026-08-01T00:00:00Z",
        "n_clusters": 1,
        "clusters": [
            {
                "cluster_id": 0,
                "label": "G1",
                "members": ["600519", "000001"],
                "return_model": rm,
            }
        ],
        "code_map": {
            "600519": {
                "cluster_id": 0,
                "cluster_label": "G1",
                "return_model": rm,
            },
            "000001": {
                "cluster_id": 0,
                "cluster_label": "G1",
                "return_model": rm,
            },
        },
    }


class TestClusterPointerFh1(unittest.TestCase):
    def test_promote_writes_pointer_and_versioned(self):
        from core.signal.cluster.live import (
            load_active_cluster_weights,
            promote_cluster_artifact,
        )
        from core.signal.cluster.pointer import load_cluster_pointer

        with _live_tmp() as ctx:
            out = promote_cluster_artifact(_artifact())
            self.assertTrue(out["success"])
            ptr = load_cluster_pointer()
            self.assertIsNotNone(ptr)
            self.assertEqual(int(ptr["version"]), 1)
            ver_path = os.path.join(ctx["live"], "cluster_weights_v1.json")
            self.assertTrue(os.path.isfile(ver_path))
            self.assertTrue(os.path.isfile(ctx["pointer"]))
            act = load_active_cluster_weights()
            self.assertEqual(act["version"], 1)
            self.assertEqual(act["n_mapped_codes"], 2)

    def test_validation_fail_leaves_pointer_untouched(self):
        from core.signal.cluster.live import promote_cluster_artifact
        from core.signal.cluster.pointer import load_cluster_pointer

        with _live_tmp() as ctx:
            bad = promote_cluster_artifact({"code_map": {}})
            self.assertFalse(bad["success"])
            self.assertIsNone(load_cluster_pointer())
            self.assertFalse(os.path.isfile(ctx["pointer"]))

            ok = promote_cluster_artifact(_artifact())
            self.assertTrue(ok["success"])
            ptr1 = load_cluster_pointer()
            # 再推一个无效产物（force=False）
            bad2 = promote_cluster_artifact({"code_map": {"x": {}}})
            self.assertFalse(bad2["success"])
            ptr2 = load_cluster_pointer()
            self.assertEqual(ptr1["version"], ptr2["version"])

    def test_force_active_writes_audit(self):
        from core.signal.cluster.live import (
            promote_cluster_artifact,
            set_cluster_scoring_mode,
        )

        with _live_tmp() as ctx:
            promote_cluster_artifact(_artifact())
            # 无簿 → 应拦截
            blocked = set_cluster_scoring_mode("active")
            self.assertFalse(blocked["success"])
            # force 豁免并写审计
            with patch(
                "core.signal.cluster.live.assess_cluster_live_health",
                return_value={"allow_active": True, "alerts": []},
            ), patch(
                "core.signal.cluster.live.build_cluster_enable_evidence",
                return_value={"gate": {"ok": True, "blockers": []}},
            ):
                forced = set_cluster_scoring_mode("active", force=True)
            self.assertTrue(forced["success"])
            self.assertTrue(os.path.isfile(ctx["audit"]))
            with open(ctx["audit"], encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            self.assertGreaterEqual(len(lines), 1)
            row = json.loads(lines[-1])
            self.assertEqual(row["action"], "cluster_set_mode_force_active")


if __name__ == "__main__":
    unittest.main()
