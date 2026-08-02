"""code_map 多权打分：仅组内序。"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.cluster_multi_score import (
    attach_cluster_multi_score,
    score_by_code_map,
)


@contextmanager
def _fake_overlay(_patch):
    yield


class TestClusterMultiScore(unittest.TestCase):
    def test_within_group_only_no_cross_rank(self):
        code_map = {
            "600519": {
                "cluster_label": "G1",
                "cluster_id": 0,
                "weights": {"momentum": 0.7, "value": 0.3},
            },
            "000001": {
                "cluster_label": "G1",
                "cluster_id": 0,
                "weights": {"momentum": 0.7, "value": 0.3},
            },
            "601318": {
                "cluster_label": "G2",
                "cluster_id": 1,
                "weights": {"momentum": 0.2, "value": 0.8},
            },
        }
        bars = {
            "600519": [
                {"close": 10 + i, "date": f"2024-01-{i + 1:02d}"} for i in range(20)
            ],
            "000001": [
                {"close": 8 + i * 0.5, "date": f"2024-01-{i + 1:02d}"}
                for i in range(20)
            ],
            "601318": [
                {"close": 12 + i * 0.2, "date": f"2024-01-{i + 1:02d}"}
                for i in range(20)
            ],
        }

        def fake_score(code, bars_in, **kwargs):
            last = float((bars_in or [{}])[-1].get("close") or 0)
            return {
                "success": True,
                "stock_code": code,
                "stock_name": code,
                "score": last,
                "hard_reject": False,
            }

        with patch(
            "quant.research.cluster_multi_score._score_one",
            side_effect=fake_score,
        ), patch(
            "core.signal.config.load_signal_config",
            return_value={"weights": {"momentum": 0.5, "value": 0.5}},
        ), patch(
            "core.signal.config.signal_config_overlay",
            _fake_overlay,
        ):
            out = score_by_code_map(code_map, bars, horizon_days=3)

        self.assertTrue(out["success"])
        self.assertFalse(out["cross_group_rank"])
        g1 = next(g for g in out["groups"] if g["label"] == "G1")
        self.assertEqual(g1["ranking"][0]["stock_code"], "600519")
        self.assertEqual(g1["ranking"][0]["rank_in_group"], 1)
        for r in out["flat"]:
            self.assertIn("rank_in_group", r)
            self.assertNotIn("global_rank", r)
            self.assertIn("delta_vs_global", r)

    def test_attach_and_persist(self):
        report = {
            "success": True,
            "note": "base",
            "pool_artifact": {
                "success": True,
                "schema_version": 1,
                "created_at": "2026-08-01T00:00:00Z",
                "n_clusters": 1,
                "code_map": {
                    "600519": {
                        "cluster_label": "G1",
                        "weights": {"momentum": 0.6, "value": 0.4},
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "core.paths.QUANT_REPORTS_DIR", tmp
            ), patch(
                "quant.research.cluster_multi_score.score_by_code_map",
                return_value={
                    "success": True,
                    "scored_count": 1,
                    "groups": [],
                    "flat": [],
                    "cross_group_rank": False,
                },
            ):
                out = attach_cluster_multi_score(
                    report,
                    {"600519": [{"date": "2024-01-01", "close": 10}]},
                )
            self.assertTrue(out["multi_score"]["success"])
            self.assertIn("多权打分", out["note"])
            path = os.path.join(tmp, "last_cluster_pool_artifact.json")
            self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
