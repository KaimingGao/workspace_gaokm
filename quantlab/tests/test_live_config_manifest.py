"""H3：live_config_manifest 一致性指纹。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestLiveConfigManifest(unittest.TestCase):
    def test_weights_without_mode_alerts(self):
        from core.live_config_manifest import build_live_config_manifest

        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "live")
            os.makedirs(live)
            signal = os.path.join(td, "signal_config.json")
            weights = os.path.join(live, "cluster_weights_active.json")
            with open(signal, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "scoring": {"rank_mode": "predicted_score", "min_predicted_score": 1.0},
                        "cluster_scoring": {"mode": "off", "enabled": False},
                    },
                    f,
                )
            with open(weights, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "n_mapped_codes": 3, "promoted_at": "2026-08-05T00:00:00"}, f)

            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": signal}), patch(
                "core.paths.CLUSTER_WEIGHTS_ACTIVE_PATH", weights
            ), patch(
                "core.paths.CLUSTER_BOOK_ACTIVE_PATH", os.path.join(live, "book.json")
            ), patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", os.path.join(live, "rm.json")
            ), patch(
                "core.paths.SIGNAL_CONFIG_PATH", signal
            ), patch(
                "core.paths.LIVE_DIR", live
            ), patch(
                "core.paths.PAPER_PATH", os.path.join(td, "paper.json")
            ):
                m = build_live_config_manifest(note="test")
                self.assertTrue(m["success"])
                self.assertFalse(m["consistent"])
                self.assertTrue(m.get("cluster_retired"))
                self.assertTrue(any("cluster_retired" in a for a in m["alerts"]))

    def test_write_manifest(self):
        from core.live_config_manifest import write_live_config_manifest

        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "live")
            os.makedirs(live)
            signal = os.path.join(td, "signal_config.json")
            with open(signal, "w", encoding="utf-8") as f:
                json.dump({"cluster_scoring": {"mode": "off"}, "scoring": {}}, f)
            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": signal}), patch(
                "core.paths.LIVE_DIR", live
            ), patch(
                "core.paths.SIGNAL_CONFIG_PATH", signal
            ), patch(
                "core.paths.CLUSTER_WEIGHTS_ACTIVE_PATH", os.path.join(live, "w.json")
            ), patch(
                "core.paths.CLUSTER_BOOK_ACTIVE_PATH", os.path.join(live, "b.json")
            ), patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", os.path.join(live, "r.json")
            ), patch(
                "core.paths.PAPER_PATH", os.path.join(td, "paper.json")
            ):
                out = write_live_config_manifest(note="unit")
                self.assertTrue(os.path.isfile(out["path"]))
                with open(out["path"], encoding="utf-8") as rf:
                    raw = json.load(rf)
                self.assertEqual(raw.get("schema_version"), 1)


if __name__ == "__main__":
    unittest.main()
