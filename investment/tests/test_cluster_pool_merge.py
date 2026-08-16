"""分池合成：各组 Top-N → 候选簿；对照回测挂载。"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.cluster_pool_merge import (
    attach_cluster_pool_merge,
    build_merged_book,
)


class TestClusterPoolMerge(unittest.TestCase):
    def test_build_merged_book_takes_top_n_per_group(self):
        group_scores = {
            "success": True,
            "groups": [
                {
                    "cluster_id": 0,
                    "label": "G1",
                    "skipped": False,
                    "ranking": [
                        {
                            "stock_code": "600519",
                            "stock_name": "茅台",
                            "rank_in_group": 1,
                            "score": 80,
                            "global_rank": 2,
                        },
                        {
                            "stock_code": "000001",
                            "stock_name": "平安",
                            "rank_in_group": 2,
                            "score": 70,
                            "global_rank": 3,
                        },
                    ],
                },
                {
                    "cluster_id": 1,
                    "label": "G2",
                    "skipped": False,
                    "ranking": [
                        {
                            "stock_code": "601318",
                            "stock_name": "人保",
                            "rank_in_group": 1,
                            "score": 75,
                            "global_rank": 1,
                        },
                    ],
                },
            ],
            "global_ranking": [
                {"stock_code": "601318", "global_rank": 1},
                {"stock_code": "600519", "global_rank": 2},
                {"stock_code": "000001", "global_rank": 3},
            ],
        }
        out = build_merged_book(group_scores, top_n_per_group=1, max_names=10)
        self.assertTrue(out["success"])
        self.assertEqual(out["name_count"], 2)
        codes = [p["stock_code"] for p in out["book"]]
        self.assertEqual(codes, ["600519", "601318"])
        self.assertAlmostEqual(out["book"][0]["weight_pct"], 50.0)
        self.assertEqual(out["vs_global_top"]["overlap_count"], 2)

    def test_attach_writes_pool_merge(self):
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
                        "suggested_weights": {"momentum": 0.6, "value": 0.4},
                    },
                },
            ],
            "group_scores": {
                "success": True,
                "groups": [
                    {
                        "cluster_id": 0,
                        "label": "G1",
                        "skipped": False,
                        "ranking": [
                            {
                                "stock_code": "600519",
                                "rank_in_group": 1,
                                "score": 80,
                            }
                        ],
                    }
                ],
                "global_ranking": [{"stock_code": "600519", "global_rank": 1}],
            },
        }
        with patch(
            "quant.research.cluster_pool_merge.backtest_cluster_pools",
            return_value={
                "success": True,
                "metrics": {"total_return_pct": 1.0},
                "compare": {"delta_oos_pp": 0.5, "pool_better_oos": True},
                "global_baseline": {"success": True, "top_k": 1},
            },
        ):
            out = attach_cluster_pool_merge(
                report,
                {"600519": [{"date": "2024-01-01", "close": 10}]},
                run_backtest=True,
            )
        self.assertTrue(out["pool_merge"]["success"])
        self.assertEqual(out["pool_merge"]["book"]["name_count"], 1)
        self.assertTrue(out["pool_merge"]["backtest"]["success"])
        self.assertIn("分池合成", out["note"])

    def test_global_baseline_uses_heuristic_not_predicted(self):
        """全局对照须 heuristic；默认 predicted+live 会把分池合成拖成「假卡死」。"""
        from quant.research.cluster_pool_merge import backtest_cluster_pools

        clusters = [
            {
                "cluster_id": 0,
                "label": "G1",
                "members": ["a", "b"],
                "weight_suggest": {
                    "success": True,
                    "suggested_weights": {"momentum": 1.0},
                },
            }
        ]
        bars = {
            "a": [
                {
                    "date": f"2024-01-{i:02d}",
                    "close": 10 + i * 0.1,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "volume": 1e6,
                }
                for i in range(1, 40)
            ],
            "b": [
                {
                    "date": f"2024-01-{i:02d}",
                    "close": 20 + i * 0.1,
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "volume": 1e6,
                }
                for i in range(1, 40)
            ],
        }
        captured = {}

        def fake_bt(stock_bars, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "metrics": {"total_return_pct": 1.0},
                "equity_curve": [
                    {"date": "d1", "equity": 100},
                    {"date": "d2", "equity": 101},
                    {"date": "d3", "equity": 102},
                    {"date": "d4", "equity": 103},
                ],
                "trades": [],
            }

        with patch(
            "core.backtest.topk_backtest.backtest_topk_equal_weight",
            side_effect=fake_bt,
        ):
            out = backtest_cluster_pools(
                clusters, bars, horizon_days=1, top_n_per_group=1, max_names=10
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(captured.get("rank_mode"), "heuristic_score")
        self.assertTrue(captured.get("allow_heuristic_baseline"))
        self.assertFalse(captured.get("use_live_cluster_models"))
        self.assertFalse(captured.get("apply_tau_buy_gate"))


if __name__ == "__main__":
    unittest.main()
