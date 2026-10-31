"""形态硬闸：高波动 + 弱开盘。"""

from __future__ import annotations

import unittest


class MorphGateUnitTests(unittest.TestCase):
    def test_skip_when_high_vol_and_weak_open(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True, "morph_vol_pctl": 0.9, "morph_a1_max": 0.0}
        item = {"morph_vol_p": 0.95, "morph_a1": -0.02}
        skip = morph_gate_skip_reason(item, cfg)
        self.assertIsNotNone(skip)
        self.assertIn("形态闸", skip or "")

    def test_off_by_default(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        item = {"morph_vol_p": 0.99, "morph_a1": -0.05}
        self.assertIsNone(morph_gate_skip_reason(item, {}))
        self.assertIsNone(
            morph_gate_skip_reason(item, {"morph_gate_enabled": False})
        )

    def test_fail_open_when_missing_fields(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True}
        self.assertIsNone(morph_gate_skip_reason({"morph_a1": -0.02}, cfg))
        self.assertIsNone(morph_gate_skip_reason({"morph_vol_p": 0.99}, cfg))

    def test_no_skip_when_strong_open(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True, "morph_vol_pctl": 0.9, "morph_a1_max": 0.0}
        self.assertIsNone(
            morph_gate_skip_reason({"morph_vol_p": 0.99, "morph_a1": 0.01}, cfg)
        )

    def test_stamp_cs_percentile(self):
        from core.paper.rebalance.morph_gate import stamp_morph_cs_on_items

        items = [
            {"stock_code": "000001"},
            {"stock_code": "000002"},
            {"stock_code": "000003"},
            {"stock_code": "000004"},
            {"stock_code": "000005"},
            {"stock_code": "000006"},
            {"stock_code": "000007"},
            {"stock_code": "000008"},
        ]
        metrics = {
            c["stock_code"]: {"a1": -0.01, "vol20": float(i)}
            for i, c in enumerate(items)
        }
        stamp_morph_cs_on_items(items, metrics_by_code=metrics)
        self.assertAlmostEqual(items[0]["morph_vol_p"], 0.0, places=5)
        self.assertAlmostEqual(items[-1]["morph_vol_p"], 1.0, places=5)
        self.assertAlmostEqual(items[0]["morph_a1"], -0.01, places=5)

    def test_enter_skip_wired(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        item = {
            "predicted_score": 2.0,
            "y_τc": 1.0,
            "morph_vol_p": 0.95,
            "morph_a1": -0.02,
        }
        skip = rank_lot_enter_skip_reason(
            item,
            {
                "fuse_enter": 0.0,
                "morph_gate_enabled": True,
                "morph_vol_pctl": 0.9,
                "morph_a1_max": 0.0,
            },
            rs=0.02,
        )
        self.assertIsNotNone(skip)
        self.assertIn("形态闸", skip or "")

    def test_execution_normalize_keeps_morph_gate(self):
        from core.execution import validate_execution_patch
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        ok, norm, errs = validate_execution_patch(
            {
                "rebalance_timing": {
                    "rank_lots": {"morph_gate_enabled": True},
                }
            }
        )
        self.assertTrue(ok, errs)
        lots = (norm.get("rebalance_timing") or {}).get("rank_lots") or {}
        self.assertTrue(lots.get("morph_gate_enabled"))

        paper = {
            "rules": {
                "execution": {
                    "rebalance_timing": {
                        "rank_lots": dict(lots),
                        "path_matrix": dict(lots),
                    }
                }
            }
        }
        cfg = get_rank_lot_cfg(paper)
        self.assertTrue(cfg.get("morph_gate_enabled"))


if __name__ == "__main__":
    unittest.main()
