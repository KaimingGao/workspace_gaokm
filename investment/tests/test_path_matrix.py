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
        self.assertEqual(lots.get("y_on_alpha"), 1.0)
        self.assertAlmostEqual(float(lots.get("rank_enter")), 0.001)
        self.assertAlmostEqual(float(lots.get("rank_strong")), 0.001)
        self.assertAlmostEqual(float(lots.get("holdings_mv_cap")), 150000.0)
        self.assertAlmostEqual(float(lots.get("y_oo_enter")), 0.1)
        self.assertAlmostEqual(float(lots.get("y_hl_enter")), 0.1)
        self.assertTrue(lots.get("y_hl_enabled"))
        self.assertEqual(lots.get("fill_clock"), "09:30")
        # 旧键仍与 rank_lots 同结构，读盘兼容
        pm = DEFAULT_REBALANCE_TIMING.get("path_matrix") or {}
        self.assertEqual(pm.get("mode"), "rank_lots")
        self.assertAlmostEqual(float(pm.get("holdings_mv_cap")), 150000.0)

    def test_fill_clock_passthrough(self):
        cfg = self._cfg(fill_clock="09:40")
        self.assertEqual(cfg["fill_clock"], "09:40")
        cfg_bad = self._cfg(fill_clock="11:00")
        self.assertEqual(cfg_bad["fill_clock"], "09:30")

    def test_y_on_alpha_maps_to_fusion_w_co(self):
        cfg = self._cfg(y_on_alpha=0.4)
        self.assertAlmostEqual(cfg["fusion_w_co"], 0.4)
        self.assertAlmostEqual(cfg["y_on_alpha"], 0.4)
        cfg0 = self._cfg()
        self.assertAlmostEqual(cfg0["fusion_w_co"], 1.0)

    def test_legacy_rank_thresholds_coerced_to_net(self):
        cfg = self._cfg(rank_enter=1.01, rank_strong=1.02)
        self.assertAlmostEqual(cfg["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg["rank_strong"], 0.02)
        cfg0 = self._cfg(rank_enter=0.01, rank_strong=0.20)
        self.assertAlmostEqual(cfg0["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg0["rank_strong"], 0.02)

    def test_legacy_trade_weights_map_to_oo_oc(self):
        cfg = self._cfg(fusion_w_trade=0.7, fusion_w_nowcast=0.3)
        self.assertAlmostEqual(cfg["fusion_w_oo"], 0.7)
        self.assertAlmostEqual(cfg["fusion_w_oc"], 0.3)


if __name__ == "__main__":
    unittest.main()
