"""分组 score：组权打分 + 组内排序。"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.cluster_group_score import (
    attach_cluster_group_scores,
    compute_cluster_group_scores,
)


class TestClusterGroupScore(unittest.TestCase):
    def test_within_group_rank_uses_overlay_weights(self):
        clusters = [
            {
                "cluster_id": 0,
                "label": "G1",
                "members": ["600519", "000001"],
                "weight_suggest": {
                    "success": True,
                    "suggested_weights": {"momentum": 0.6, "value": 0.4},
                },
            },
            {
                "cluster_id": 1,
                "label": "G2",
                "members": ["601318"],
                "weight_suggest": {
                    "success": True,
                    "suggested_weights": {"momentum": 0.3, "value": 0.7},
                },
            },
        ]
        bars = {
            "600519": [{"close": 10 + i, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
            "000001": [{"close": 8 + i * 0.5, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
            "601318": [{"close": 12 + i * 0.2, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
        }

        def fake_score(bars_in, **kwargs):
            # 用最后收盘价当伪 score，便于排序
            last = float((bars_in or [{}])[-1].get("close") or 0)
            return {"score": last, "hard_reject": False, "sub_scores": {}}

        with patch(
            "quant.research.cluster_group_score._score_one",
            side_effect=lambda code, bars, **kw: {
                "success": True,
                "stock_code": code,
                "stock_name": code,
                "score": float((bars or [{}])[-1].get("close") or 0),
                "hard_reject": False,
            },
        ):
            out = compute_cluster_group_scores(clusters, bars, horizon_days=3)

        self.assertTrue(out.get("success"))
        g1 = next(g for g in out["groups"] if g["label"] == "G1")
        self.assertFalse(g1.get("skipped"))
        self.assertEqual(len(g1["ranking"]), 2)
        self.assertEqual(g1["ranking"][0]["rank_in_group"], 1)
        self.assertEqual(g1["ranking"][1]["rank_in_group"], 2)
        # 600519 last close 29 > 000001 last 17.5
        self.assertEqual(g1["ranking"][0]["stock_code"], "600519")
        self.assertTrue(out.get("global_ranking"))

    def test_attach_writes_group_ranking(self):
        report = {
            "success": True,
            "note": "base",
            "clusters": [
                {
                    "cluster_id": 0,
                    "label": "G1",
                    "members": ["600519"],
                    "weight_suggest": {
                        "success": True,
                        "suggested_weights": {"momentum": 1.0},
                    },
                }
            ],
        }
        with patch(
            "quant.research.cluster_group_score.compute_cluster_group_scores",
            return_value={
                "success": True,
                "groups": [
                    {
                        "label": "G1",
                        "ranking": [
                            {
                                "stock_code": "600519",
                                "rank_in_group": 1,
                                "score": 55.0,
                            }
                        ],
                    }
                ],
                "global_ranking": [],
                "flat": [],
            },
        ):
            out = attach_cluster_group_scores(
                report, {"600519": []}, run_group_score=True
            )
        self.assertTrue(out["group_scores"]["success"])
        self.assertEqual(out["clusters"][0]["group_ranking"][0]["rank_in_group"], 1)
        self.assertIn("分组 score", out.get("note") or "")


if __name__ == "__main__":
    unittest.main()
