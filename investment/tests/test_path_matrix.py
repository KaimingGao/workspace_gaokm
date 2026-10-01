"""rank_lots 调仓配置：门槛、融合权重、市值上限。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestPathMatrix(unittest.TestCase):
    def _cfg(self, **kwargs):
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        base = {
            "enabled": True,
            "mode": "rank_lots",
        }
        base.update(kwargs)
        return get_path_matrix_cfg({"path_matrix": base})

    def test_fuse_pct(self):
        from core.signal.yhat_windows import fuse_pct

        self.assertAlmostEqual(fuse_pct(1.0, 3.0, w_left=0.5, w_right=0.5), 2.0)
        self.assertAlmostEqual(fuse_pct(1.0, None), 1.0)
        self.assertIsNone(fuse_pct(None, None))

    def test_ranking_fuses_oo_oc(self):
        from core.signal.yhat_windows import ranking_pct, residual_pct, invert_price_over_close

        item = {"predicted_score": 2.0, "y_tau": 1.0}
        self.assertAlmostEqual(ranking_pct(item), 1.5)
        self.assertAlmostEqual(invert_price_over_close(1.0), round((1.0 / 1.01 - 1.0) * 100.0, 10))
        item_r = {"y_tau": 2.0, "y_r": 1.0, "ret_open_to_tau": 0.0}
        pc = invert_price_over_close(1.0)
        rem = 2.0
        self.assertAlmostEqual(residual_pct(item_r), 0.5 * pc + 0.5 * rem)
        from core.signal.yhat_windows import t0_residual_pct

        self.assertIsNone(t0_residual_pct({"y_tau": 2.0}))
        self.assertAlmostEqual(t0_residual_pct(item_r), 0.5 * pc + 0.5 * rem)

    def test_reads_rank_lots_key_first(self):
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        cfg = get_path_matrix_cfg(
            {
                "rank_lots": {"rank_enter": 0.02, "fusion_w_oo": 0.6, "fusion_w_oc": 0.4},
                "path_matrix": {"rank_enter": 0.012, "fusion_w_oo": 0.4},
            }
        )
        self.assertAlmostEqual(cfg["rank_enter"], 0.02)
        self.assertAlmostEqual(cfg["fusion_w_oo"], 0.6)
        self.assertEqual(cfg["mode"], "rank_lots")

    def test_execution_default_has_rank_lots(self):
        from core.execution import DEFAULT_REBALANCE_TIMING

        lots = DEFAULT_REBALANCE_TIMING.get("rank_lots") or {}
        self.assertIn("enabled", lots)
        self.assertTrue(lots["enabled"])
        self.assertEqual(lots.get("mode"), "rank_lots")
        self.assertIn("rank_enter", lots)
        self.assertIn("cash_floor", lots)
        self.assertAlmostEqual(float(lots.get("cash_floor")), 0.0)
        self.assertEqual(lots.get("fusion_w_co"), 1.0)
        self.assertNotIn("y_on_alpha", lots)
        self.assertNotIn("fusion_w_trade", lots)
        self.assertNotIn("fusion_w_nowcast", lots)
        self.assertAlmostEqual(float(lots.get("rank_enter")), 0.001)
        self.assertAlmostEqual(float(lots.get("rank_strong")), 0.001)
        self.assertNotIn("rank_exit", lots)
        self.assertNotIn("lot_reduce", lots)
        self.assertAlmostEqual(float(lots.get("holdings_mv_cap")), 150000.0)
        self.assertNotIn("y_oo_enter", lots)
        self.assertNotIn("y_oc_enter", lots)
        self.assertNotIn("y_hl_enter", lots)
        self.assertNotIn("y_hl_gt0", lots)
        self.assertFalse(lots.get("y_oo_gt0"))
        self.assertFalse(lots.get("y_τc_gt0"))
        self.assertEqual(lots.get("fill_clock"), "09:30")
        self.assertEqual(int(lots.get("lot_base_amount") or 0), 10000)
        self.assertEqual(int(lots.get("lot_strong_amount") or 0), 20000)
        # 旧键仍与 rank_lots 同结构，读盘兼容
        pm = DEFAULT_REBALANCE_TIMING.get("path_matrix") or {}
        self.assertEqual(pm.get("mode"), "rank_lots")
        self.assertAlmostEqual(float(pm.get("holdings_mv_cap")), 150000.0)
        self.assertEqual(int(pm.get("lot_base_amount") or 0), 10000)
        self.assertEqual(int(pm.get("lot_strong_amount") or 0), 20000)

    def test_fill_clock_passthrough(self):
        cfg = self._cfg(fill_clock="09:40")
        self.assertEqual(cfg["fill_clock"], "09:40")
        cfg_bad = self._cfg(fill_clock="11:00")
        self.assertEqual(cfg_bad["fill_clock"], "09:30")

    def test_lot_amounts_passthrough(self):
        cfg = self._cfg(lot_base_amount=30000, lot_strong_amount=50000)
        self.assertEqual(cfg["lot_base_amount"], 30000)
        self.assertEqual(cfg["lot_strong_amount"], 50000)
        cfg_clip = self._cfg(lot_base_amount=350, lot_strong_amount=80)
        self.assertEqual(cfg_clip["lot_base_amount"], 1000)
        self.assertEqual(cfg_clip["lot_strong_amount"], 1000)
        cfg0 = self._cfg()
        self.assertEqual(cfg0["lot_base_amount"], 10000)
        self.assertEqual(cfg0["lot_strong_amount"], 20000)

    def test_old_y_on_alpha_does_not_fill_fusion_w_co(self):
        cfg = self._cfg(y_on_alpha=0.4)
        self.assertAlmostEqual(cfg["fusion_w_co"], 1.0)
        self.assertNotIn("y_on_alpha", cfg)
        cfg0 = self._cfg(fusion_w_co=0.4)
        self.assertAlmostEqual(cfg0["fusion_w_co"], 0.4)

    def test_legacy_rank_thresholds_coerced_to_net(self):
        cfg = self._cfg(rank_enter=1.01, rank_strong=1.02)
        self.assertAlmostEqual(cfg["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg["rank_strong"], 0.02)
        cfg0 = self._cfg(rank_enter=0.01, rank_strong=0.20)
        self.assertAlmostEqual(cfg0["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg0["rank_strong"], 0.02)

    def test_old_trade_weights_do_not_fill_oo_oc(self):
        cfg = self._cfg(fusion_w_trade=0.7, fusion_w_nowcast=0.3)
        self.assertAlmostEqual(cfg["fusion_w_oo"], 0.6)
        self.assertAlmostEqual(cfg["fusion_w_oc"], 0.4)
        self.assertNotIn("fusion_w_trade", cfg)
        self.assertNotIn("fusion_w_nowcast", cfg)

    def test_stale_y_oo_oc_enter_stripped(self):
        from core.execution import apply_execution_patch_to_paper
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        gone = (
            "y_oo_enter",
            "y_oc_enter",
            "y_oo_enter_alt",
            "y_oc_enter_alt",
            "y_hl_enter",
            "y_oo_oc_enabled",
            "y_oo_oc_enter",
            "y_hl_gt0",
        )
        cfg = get_path_matrix_cfg(
            {
                "rank_lots": {
                    "y_oo_enter": 0.5,
                    "y_oc_enter": 0.5,
                    "y_oo_enter_alt": 0.4,
                    "y_oc_enter_alt": 0.4,
                    "y_hl_enter": 0.2,
                }
            }
        )
        for k in gone:
            self.assertNotIn(k, cfg)
        self.assertNotIn("y_hl_gt0", cfg)
        self.assertFalse(cfg.get("y_oo_gt0"))

        cfg_on = get_path_matrix_cfg(
            {"rank_lots": {"y_oo_oc_enabled": True, "y_oo_oc_enter": 0.3}}
        )
        self.assertFalse(cfg_on["y_oo_gt0"])
        self.assertFalse(cfg_on["y_τc_gt0"])
        self.assertNotIn("y_oo_oc_enabled", cfg_on)
        self.assertNotIn("y_oo_oc_enter", cfg_on)

        paper = {
            "rules": {
                "execution": {
                    "rebalance_timing": {
                        "rank_lots": {"y_oo_enter": 0.5, "y_oc_enter": 0.5, "y_hl_enter": 0.2},
                    }
                }
            }
        }
        applied = apply_execution_patch_to_paper(
            paper,
            {"rebalance_timing": {"rank_lots": {"y_oo_gt0": True}}},
        )
        self.assertTrue(applied.get("ok"), applied)
        lots = paper["rules"]["execution"]["rebalance_timing"]["rank_lots"]
        for k in gone:
            self.assertNotIn(k, lots)
        self.assertNotIn("y_hl_gt0", lots)
        self.assertTrue(lots.get("y_oo_gt0"))

    def test_save_rules_persists_lot_amounts(self):
        from core.execution import apply_execution_patch_to_paper, validate_execution_patch

        ok, norm, errs = validate_execution_patch(
            {
                "rebalance_timing": {
                    "rank_lots": {"lot_base_amount": 30000, "lot_strong_amount": 50000}
                }
            }
        )
        self.assertTrue(ok, errs)
        saved = (norm.get("rebalance_timing") or {}).get("rank_lots") or {}
        self.assertEqual(int(saved.get("lot_base_amount") or 0), 30000)
        self.assertEqual(int(saved.get("lot_strong_amount") or 0), 50000)

        paper = {"rules": {}}
        applied = apply_execution_patch_to_paper(
            paper,
            {"rebalance_timing": {"rank_lots": {"lot_base_amount": 30000, "lot_strong_amount": 50000}}},
        )
        self.assertTrue(applied.get("ok"), applied)
        lots = paper["rules"]["execution"]["rebalance_timing"]["rank_lots"]
        self.assertEqual(int(lots.get("lot_base_amount") or 0), 30000)
        self.assertEqual(int(lots.get("lot_strong_amount") or 0), 50000)


if __name__ == "__main__":
    unittest.main()
