"""分池映射产物 + 纸面预演（不写账）。"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.cluster_pool_artifact import (
    assign_holdings_to_clusters,
    attach_cluster_pool_artifact,
    build_pool_artifact,
    intent_preview_vs_holdings,
    preview_paper_pool_rebalance,
)


class TestClusterPoolArtifact(unittest.TestCase):
    def _report(self):
        return {
            "success": True,
            "note": "base",
            "lookback": 80,
            "horizon_days": 3,
            "stock_count": 3,
            "preferred_cluster": {"label": "G1"},
            "clusters": [
                {
                    "cluster_id": 0,
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "singleton": False,
                    "oos_gate": {"ok": True, "passed": True},
                    "return_model": {
                        "coefficients": {"momentum": 0.3, "value": 0.2},
                        "intercept": 0.0,
                    },
                    "weight_suggest": {
                        "success": True,
                        "suggested_weights": {"momentum": 0.6, "value": 0.4},
                    },
                },
                {
                    "cluster_id": 1,
                    "label": "G2",
                    "members": ["601318"],
                    "return_model": {
                        "coefficients": {"momentum": 0.1, "value": 0.4},
                        "intercept": 0.0,
                    },
                    "weight_suggest": {
                        "success": True,
                        "suggested_weights": {"momentum": 0.3, "value": 0.7},
                    },
                    "oos_gate": {"ok": True, "passed": False},
                },
            ],
            "pool_merge": {
                "success": True,
                "book": {
                    "success": True,
                    "top_n_per_group": 1,
                    "name_count": 2,
                    "book": [
                        {
                            "stock_code": "600519",
                            "stock_name": "茅台",
                            "cluster_label": "G1",
                            "score": 80,
                            "weight_pct": 50,
                        },
                        {
                            "stock_code": "601318",
                            "stock_name": "人保",
                            "cluster_label": "G2",
                            "score": 75,
                            "weight_pct": 50,
                        },
                    ],
                    "vs_global_top": {"jaccard": 0.5},
                },
                "backtest": {
                    "success": True,
                    "metrics": {"total_return_pct": 1.2},
                    "compare": {"delta_oos_pp": 0.3},
                    "top_n_per_group": 1,
                    "top_k_global": 2,
                    "rebalance_count": 5,
                },
            },
        }

    def test_build_maps_code_to_weights(self):
        art = build_pool_artifact(self._report())
        self.assertTrue(art["success"])
        self.assertFalse(art["promote_ready"])
        self.assertEqual(art["schema_version"], 1)
        self.assertIn("600519", art["code_map"])
        self.assertEqual(art["code_map"]["600519"]["cluster_label"], "G1")
        # |β| 派生：0.3/(0.3+0.2)=0.6
        self.assertAlmostEqual(art["code_map"]["600519"]["weights"]["momentum"], 0.6)
        self.assertTrue(art["clusters"][0]["weights_derived_from_beta"])
        self.assertEqual(
            art["code_map"]["600519"]["return_model"]["coefficients"]["momentum"], 0.3
        )
        self.assertEqual(art["n_codes_with_coefs"], 3)
        self.assertEqual(len(art["pool_book"]), 2)
        self.assertIsNotNone(art["backtest_summary"])
        self.assertTrue(art["clusters"][0]["oos_passed"])
        self.assertTrue(art["clusters"][0]["oos_gate"]["passed"])
        self.assertFalse(art["clusters"][1]["oos_passed"])
        self.assertFalse(art["clusters"][1]["oos_gate"]["passed"])

    def test_intent_preview(self):
        intent = intent_preview_vs_holdings(
            [{"stock_code": "600519"}, {"stock_code": "601318"}],
            [{"stock_code": "600519"}, {"stock_code": "000001"}],
        )
        self.assertEqual(intent["would_keep"], ["600519"])
        self.assertEqual(intent["would_sell"], ["000001"])
        self.assertEqual(intent["would_buy"], ["601318"])

    def test_assign_holdings_to_clusters(self):
        code_map = {
            "600519": {
                "cluster_label": "G1",
                "weights": {"momentum": 0.6},
            },
            "601318": {
                "cluster_label": "G2",
                "weights": {"value": 0.5},
            },
        }
        out = assign_holdings_to_clusters(
            code_map,
            [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 100},
                {"stock_code": "000001", "stock_name": "平安", "shares": 200},
            ],
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["mapped_count"], 1)
        self.assertEqual(out["unmapped_count"], 1)
        g1 = next(g for g in out["groups"] if g["label"] == "G1")
        self.assertEqual(g1["holdings"][0]["stock_code"], "600519")
        self.assertEqual(out["unmapped"][0]["stock_code"], "000001")

    def test_assign_holdings_data_vs_universe(self):
        code_map = {
            "600519": {"cluster_label": "G1", "weights": {"momentum": 0.6}},
        }
        out = assign_holdings_to_clusters(
            code_map,
            [
                {"stock_code": "600519", "stock_name": "茅台"},
                {"stock_code": "000001", "stock_name": "平安"},
                {"stock_code": "999999", "stock_name": "外星"},
            ],
            universe_codes=["600519", "000001"],
            skipped=[{"code": "000001", "reason": "无日线"}],
        )
        self.assertEqual(out["mapped_count"], 1)
        by_code = {u["stock_code"]: u for u in out["unmapped"]}
        self.assertIn("数据不足", by_code["000001"]["reason"])
        self.assertIn("未纳入本次宇宙", by_code["999999"]["reason"])

    def test_assign_holdings_beta_outlier(self):
        out = assign_holdings_to_clusters(
            {"600519": {"cluster_label": "G1", "weights": {"momentum": 0.6}}},
            [
                {"stock_code": "600519", "stock_name": "茅台"},
                {"stock_code": "688981", "stock_name": "中芯"},
            ],
            universe_codes=["600519", "688981"],
            skipped=[{"code": "688981", "reason": "β离群未入簇"}],
        )
        by_code = {u["stock_code"]: u for u in out["unmapped"]}
        self.assertIn("β离群", by_code["688981"]["reason"])
        self.assertEqual(out.get("unmapped_beta_outlier_count"), 1)
        self.assertIn("β离群", out.get("note") or "")

    def test_assign_maps_without_weights(self):
        out = assign_holdings_to_clusters(
            {"600519": {"cluster_label": "G1", "weights": None}},
            [{"stock_code": "600519", "stock_name": "茅台"}],
        )
        self.assertEqual(out["mapped_count"], 1)
        self.assertFalse(out["groups"][0]["holdings"][0]["has_weights"])

    def test_attach_writes_artifact(self):
        report = self._report()
        with patch(
            "quant.research.cluster_pool_artifact.os.path.isfile",
            return_value=False,
        ):
            out = attach_cluster_pool_artifact(report)
        self.assertTrue(out["pool_artifact"]["success"])
        self.assertIn("分组映射", out["note"])
        self.assertTrue(out["pool_artifact"]["intent_preview"]["success"])

    def test_paper_preview_dry_run_no_write(self):
        book = [
            {"stock_code": "600519", "stock_name": "茅台", "score": 80},
            {"stock_code": "601318", "stock_name": "人保", "score": 75},
        ]
        paper = {
            "cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "000001",
                    "stock_name": "平安",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "rules": {"max_positions": 5, "min_score": 50, "position_pct": 0.2},
            "trades": [],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            import json

            with open(path, "w", encoding="utf-8") as wf:
                json.dump(paper, wf)

            def fake_sim(paper_in, ranking, **kwargs):
                # 模拟会改副本；验证外层不 save
                paper_in["holdings"] = [
                    {"stock_code": "600519", "stock_name": "茅台", "shares": 100}
                ]
                paper_in.setdefault("trades", []).append(
                    {"side": "sell", "stock_code": "000001"}
                )
                return {
                    "success": True,
                    "sell_trades": [{"side": "sell", "stock_code": "000001"}],
                    "buy_trades": [{"side": "buy", "stock_code": "600519"}],
                    "buys_blocked": False,
                    "risk_gate": {"ok": True},
                }

            with patch(
                "core.paper_rebalance.simulate_cross_section_rebalance",
                side_effect=fake_sim,
            ):
                out = preview_paper_pool_rebalance(book, paper_path=path, dry_run=True)

            self.assertTrue(out["success"])
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["sell_count"], 1)
            self.assertEqual(out["buy_count"], 1)
            # 原文件不应被改写
            with open(path, encoding="utf-8") as rf:
                saved = json.load(rf)
            self.assertEqual(saved["holdings"][0]["stock_code"], "000001")
            self.assertEqual(len(saved.get("trades") or []), 0)
        finally:
            os.unlink(path)

    def test_refuse_write_without_confirm(self):
        out = preview_paper_pool_rebalance(
            [{"stock_code": "600519", "score": 80}],
            dry_run=False,
            confirm=False,
        )
        self.assertFalse(out["success"])
        self.assertIn("禁止写账", out.get("error") or "")

    def test_confirm_writes_paper_not_signal_config(self):
        book = [
            {"stock_code": "600519", "stock_name": "茅台", "score": 80},
        ]
        paper = {
            "cash": 100000.0,
            "initial_cash": 100000.0,
            "holdings": [
                {
                    "stock_code": "000001",
                    "stock_name": "平安",
                    "shares": 100,
                    "cost": 10.0,
                }
            ],
            "rules": {"max_positions": 5, "min_score": 50, "position_pct": 0.2},
            "trades": [],
            "snapshots": [],
            "operation_log": [],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            import json

            with open(path, "w", encoding="utf-8") as wf:
                json.dump(paper, wf)

            def fake_sim(paper_in, ranking, **kwargs):
                paper_in["holdings"] = [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "shares": 100,
                        "cost": 50.0,
                    }
                ]
                paper_in["cash"] = 90000.0
                return {
                    "success": True,
                    "sell_trades": [{"side": "sell", "stock_code": "000001"}],
                    "buy_trades": [{"side": "buy", "stock_code": "600519"}],
                    "buys_blocked": False,
                    "risk_gate": {"ok": True},
                }

            with patch(
                "core.paper_rebalance.simulate_cross_section_rebalance",
                side_effect=fake_sim,
            ), patch(
                "core.paper.mark_to_market",
                return_value={"equity": 95000.0, "cash": 90000.0},
            ), patch(
                "core.paper.capture_mark_snapshot",
                return_value=None,
            ), patch(
                "core.paper.append_snapshot",
                return_value=None,
            ):
                out = preview_paper_pool_rebalance(
                    book,
                    paper_path=path,
                    confirm=True,
                    artifact={
                        "schema_version": 1,
                        "created_at": "2026-08-01T00:00:00Z",
                        "n_clusters": 2,
                    },
                )

            self.assertTrue(out["success"])
            self.assertTrue(out["confirmed"])
            self.assertFalse(out["dry_run"])
            with open(path, encoding="utf-8") as rf:
                saved = json.load(rf)
            self.assertEqual(saved["holdings"][0]["stock_code"], "600519")
            self.assertIn("last_cluster_pool", saved)
            self.assertFalse(saved["last_cluster_pool"]["signal_config_touched"])
            ops = saved.get("operation_log") or []
            self.assertTrue(
                any(o.get("type") == "cluster_pool_rebalance" for o in ops)
            )
            self.assertTrue(any(o.get("type") == "buy" for o in ops))
            summary = next(
                o for o in ops if o.get("type") == "cluster_pool_rebalance"
            )
            self.assertEqual((summary.get("meta") or {}).get("source"), "research_hub")
            self.assertEqual((summary.get("meta") or {}).get("buy_count"), 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
