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

    def test_fuse_trade_nowcast(self):
        from core.paper.rebalance.path_matrix import fuse_trade_nowcast

        self.assertAlmostEqual(fuse_trade_nowcast(1.0, 3.0, w_trade=0.5, w_nowcast=0.5), 2.0)
        self.assertAlmostEqual(fuse_trade_nowcast(1.0, None), 1.0)
        self.assertIsNone(fuse_trade_nowcast(None, None))

    def test_reads_rank_lots_key_first(self):
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        cfg = get_path_matrix_cfg(
            {
                "rank_lots": {"rank_enter": 0.02, "y_on_alpha": 1.0},
                "path_matrix": {"rank_enter": 0.012, "y_on_alpha": 0.0},
            }
        )
        self.assertAlmostEqual(cfg["rank_enter"], 0.02)
        self.assertAlmostEqual(cfg["y_on_alpha"], 1.0)
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
        self.assertEqual(lots.get("y_on_alpha"), 0.0)
        self.assertAlmostEqual(float(lots.get("rank_enter")), 0.012)
        self.assertAlmostEqual(float(lots.get("rank_strong")), 0.012)
        self.assertAlmostEqual(float(lots.get("holdings_mv_cap")), 150000.0)
        # 旧键仍与 rank_lots 同结构，读盘兼容
        pm = DEFAULT_REBALANCE_TIMING.get("path_matrix") or {}
        self.assertEqual(pm.get("mode"), "rank_lots")
        self.assertAlmostEqual(float(pm.get("holdings_mv_cap")), 150000.0)

    def test_legacy_rank_thresholds_coerced_to_net(self):
        cfg = self._cfg(rank_enter=1.01, rank_strong=1.02)
        self.assertAlmostEqual(cfg["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg["rank_strong"], 0.02)
        cfg0 = self._cfg(rank_enter=0.01, rank_strong=0.20)
        self.assertAlmostEqual(cfg0["rank_enter"], 0.01)
        self.assertAlmostEqual(cfg0["rank_strong"], 0.02)

    def test_y_on_alpha_clamped(self):
        cfg = self._cfg(y_on_alpha=99)
        self.assertEqual(cfg["y_on_alpha"], 10.0)
        cfg0 = self._cfg(y_on_alpha=-1)
        self.assertEqual(cfg0["y_on_alpha"], 0.0)


if __name__ == "__main__":
    unittest.main()
