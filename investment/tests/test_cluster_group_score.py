"""分组 score：因子系数 → 收益分组内排序。"""

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


def _rm(mom=0.2, val=0.1):
    return {
        "coefficients": {"momentum": mom, "value": val},
        "intercept": 0.0,
        "z_means": {"momentum": 50.0, "value": 50.0},
        "z_stds": {"momentum": 1.0, "value": 1.0},
    }


class TestClusterGroupScore(unittest.TestCase):
    def test_within_group_rank_uses_predicted_score(self):
        clusters = [
            {
                "cluster_id": 0,
                "label": "G1",
                "members": ["600519", "000001"],
                "return_model": _rm(0.6, 0.4),
            },
            {
                "cluster_id": 1,
                "label": "G2",
                "members": ["601318"],
                "return_model": _rm(0.3, 0.7),
            },
        ]
        bars = {
            "600519": [{"close": 10 + i, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
            "000001": [{"close": 8 + i * 0.5, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
            "601318": [{"close": 12 + i * 0.2, "date": f"2024-01-{i+1:02d}"} for i in range(20)],
        }

        def fake_score_one(code, bars_in, **kw):
            last = float((bars_in or [{}])[-1].get("close") or 0)
            return {
                "success": True,
                "stock_code": code,
                "stock_name": code,
                "score": last,
                "hard_reject": False,
                "sub_scores": {"momentum": last, "value": 50.0},
            }

        with patch(
            "quant.research.cluster_group_score._score_one",
            side_effect=fake_score_one,
        ):
            out = compute_cluster_group_scores(clusters, bars, horizon_days=3)

        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("mode"), "group_factor_coefs_within")
        g1 = next(g for g in out["groups"] if g["label"] == "G1")
        self.assertFalse(g1.get("skipped"))
        self.assertTrue(g1.get("has_return_model"))
        self.assertEqual(len(g1["predicted_ranking"]), 2)
        self.assertEqual(g1["predicted_ranking"][0]["rank_in_group"], 1)
        # 600519 sub momentum 更高 → ŷ 更高
        self.assertEqual(g1["predicted_ranking"][0]["stock_code"], "600519")
        self.assertEqual(g1["predicted_ranking"][0]["score_mode"], "group_predicted_score")
        self.assertTrue(out.get("predicted_global_ranking"))

    def test_skips_without_return_model(self):
        clusters = [
            {
                "cluster_id": 0,
                "label": "G1",
                "members": ["600519"],
                "weight_suggest": {
                    "success": True,
                    "suggested_weights": {"momentum": 1.0},
                },
            }
        ]
        out = compute_cluster_group_scores(clusters, {"600519": []}, horizon_days=3)
        self.assertTrue(out["success"])
        self.assertTrue(out["groups"][0].get("skipped"))
        self.assertEqual(out["groups"][0].get("reason"), "no_return_model")

    def test_attach_writes_group_ranking(self):
        report = {
            "success": True,
            "note": "base",
            "clusters": [
                {
                    "cluster_id": 0,
                    "label": "G1",
                    "members": ["600519"],
                    "return_model": _rm(),
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
                        "predicted_ranking": [
                            {
                                "stock_code": "600519",
                                "rank_in_group": 1,
                                "score": 1.2,
                                "predicted_score": 1.2,
                            }
                        ],
                    }
                ],
            },
        ):
            attach_cluster_group_scores(report, {}, horizon_days=3)
        self.assertEqual(report["clusters"][0]["group_ranking"][0]["predicted_score"], 1.2)
        self.assertEqual(report["clusters"][0]["predicted_ranking"][0]["score"], 1.2)


if __name__ == "__main__":
    unittest.main()
