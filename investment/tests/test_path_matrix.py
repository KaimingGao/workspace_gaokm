"""早盘调仓决策矩阵：L1 path vs L0 linear。"""

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
            "mode": "path",
            "path_enter": 1.0,
            "path_half": 1.0,
            "path_full": 2.0,
            "y_on_allow": 0.01,
            "y_on_half": 0.0,
            "require_path_on_same_sign": True,
            "require_nowcast_for_open": False,
        }
        base.update(kwargs)
        return get_path_matrix_cfg({"path_matrix": base})

    def test_open_at_path_low(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_OPEN,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=2.5,
            y_nowcast=0.3,
            y_on=0.2,
            w=0.0,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_OPEN)
        self.assertTrue(d["execute"])
        self.assertAlmostEqual(d["lambda"], 1.0)
        self.assertAlmostEqual(d["delta_w"], 0.1)
        self.assertAlmostEqual(d["overnight_scale"], 1.0)

    def test_skip_open_when_path_high(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_SKIP,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=-2.0,
            y_nowcast=0.3,
            y_on=0.2,
            w=0.0,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_SKIP)
        self.assertFalse(d["execute"])

    def test_skip_open_when_y_on_disagree(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_SKIP,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=2.0,
            y_nowcast=0.3,
            y_on=-0.2,
            w=0.0,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_SKIP)

    def test_fuse_trade_nowcast(self):
        from core.paper.rebalance.path_matrix import fuse_trade_nowcast

        self.assertAlmostEqual(fuse_trade_nowcast(1.0, 3.0, w_trade=0.5, w_nowcast=0.5), 2.0)
        self.assertAlmostEqual(fuse_trade_nowcast(1.0, None), 1.0)
        self.assertIsNone(fuse_trade_nowcast(None, None))

    def test_open_despite_nowcast_disagree_when_fused_and_filters_ok(self):
        """nc 进融合；开仓过滤改看 path/on 同号，不再单独否决 nc。"""
        from core.paper.rebalance.path_matrix import (
            ACTION_OPEN,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.8,
            y_path=2.0,
            y_nowcast=-0.2,
            y_on=0.2,
            w=0.0,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_OPEN)
        self.assertAlmostEqual(d["scores"]["y_fuse"], 0.3)

    def test_flat_path_skips(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_SKIP,
            path_execution_lambda,
            resolve_rebalance_action,
        )

        lam, _ = path_execution_lambda(0.3, self._cfg())
        self.assertEqual(lam, 0.0)
        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=0.3,
            y_nowcast=0.3,
            y_on=0.2,
            w=0.0,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_SKIP)

    def test_exit_at_path_high(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_EXIT,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=-0.1,
            y_path=-2.5,
            y_nowcast=-0.2,
            y_on=-0.1,
            w=0.1,
            w_star_day=0.1,
            in_topk=True,
            cfg=self._cfg(),
            buy_floor=0.2,
            hold_floor=0.05,
        )
        self.assertEqual(d["action"], ACTION_EXIT)
        self.assertTrue(d["execute"])
        self.assertAlmostEqual(d["delta_w"], -0.1)

    def test_pending_exit_at_path_low(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_PENDING_EXIT,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=-0.1,
            y_path=2.0,
            y_nowcast=-0.2,
            y_on=-0.1,
            w=0.1,
            w_star_day=0.0,
            cfg=self._cfg(),
            buy_floor=0.2,
            hold_floor=0.05,
        )
        self.assertEqual(d["action"], ACTION_PENDING_EXIT)
        self.assertFalse(d["execute"])

    def test_reduce_when_weak_and_path_high(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_REDUCE,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.08,
            y_path=-2.0,
            y_nowcast=0.02,
            y_on=-0.1,
            w=0.10,
            w_star_day=0.05,
            cfg=self._cfg(),
            buy_floor=0.20,
            hold_floor=0.05,
        )
        self.assertEqual(d["action"], ACTION_REDUCE)
        self.assertTrue(d["execute"])
        self.assertLess(d["delta_w"], 0)

    def test_hold_when_aligned(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_HOLD,
            resolve_rebalance_action,
        )

        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=2.0,
            y_nowcast=0.4,
            y_on=0.2,
            w=0.1,
            w_star_day=0.1,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(d["action"], ACTION_HOLD)

    def test_overnight_half(self):
        from core.paper.rebalance.path_matrix import overnight_scale, resolve_rebalance_action

        s, _ = overnight_scale(0.005, self._cfg(y_on_allow=0.01, y_on_half=0.0))
        self.assertAlmostEqual(s, 0.5)
        d = resolve_rebalance_action(
            y_trade=0.5,
            y_path=2.0,
            y_nowcast=0.4,
            y_on=0.005,
            w=0.0,
            w_star_day=0.1,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertAlmostEqual(d["w_close"], 0.05)

    def test_linear_opens_despite_path_high(self):
        from core.paper.rebalance.path_matrix import (
            ACTION_OPEN,
            ACTION_SKIP,
            compare_linear_vs_path,
        )

        cmp_ = compare_linear_vs_path(
            y_trade=0.5,
            y_path=-2.0,
            y_nowcast=0.3,
            y_on=0.2,
            w=0.0,
            w_star_day=0.1,
            cfg=self._cfg(),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertTrue(cmp_["disagree"])
        self.assertEqual(cmp_["linear"]["action"], ACTION_OPEN)
        self.assertEqual(cmp_["path"]["action"], ACTION_SKIP)

    def test_buy_gate_disabled_passthrough(self):
        from core.paper.rebalance.path_matrix import buy_execution_gate

        g = buy_execution_gate(
            {"y_trade": 0.5, "y_path": -2.0, "y_nowcast": 0.3},
            w_star_day=0.1,
            cfg=self._cfg(enabled=False),
        )
        self.assertTrue(g["allow"])
        self.assertFalse(g["enabled"])

    def test_buy_gate_blocks_bad_path(self):
        from core.paper.rebalance.path_matrix import buy_execution_gate

        g = buy_execution_gate(
            {
                "y_trade": 0.5,
                "y_path": -2.0,
                "y_nowcast": 0.3,
                "y_on": 0.2,
            },
            w=0.0,
            w_star_day=0.1,
            cfg=self._cfg(enabled=True),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertFalse(g["allow"])

    def test_sell_gate_defers_pending_exit(self):
        from core.paper.rebalance.path_matrix import sell_execution_gate

        g = sell_execution_gate(
            {
                "y_trade": -0.2,
                "y_path": 2.0,
                "y_nowcast": -0.1,
                "y_on": -0.1,
            },
            w=1.0,
            w_star_day=0.0,
            cfg=self._cfg(enabled=True),
            hold_floor=0.05,
        )
        self.assertTrue(g["defer"])
        self.assertFalse(g["allow"])

    def test_plan_book_actions_counts(self):
        from core.paper.rebalance.path_matrix import ACTION_OPEN, plan_book_actions

        plan = plan_book_actions(
            [
                {
                    "stock_code": "000001",
                    "y_trade": 0.5,
                    "y_path": 2.0,
                    "y_nowcast": 0.4,
                    "y_on": 0.2,
                }
            ],
            weight_by_code={},
            w_star_by_code={"000001": 0.1},
            top_codes=["000001"],
            cfg=self._cfg(enabled=True),
            buy_floor=0.01,
            hold_floor=0.01,
        )
        self.assertEqual(plan["n"], 1)
        self.assertEqual(plan["rows"][0]["action"], ACTION_OPEN)

    def test_execution_default_has_path_matrix(self):
        from core.execution import DEFAULT_REBALANCE_TIMING

        pm = DEFAULT_REBALANCE_TIMING.get("path_matrix") or {}
        self.assertIn("enabled", pm)
        self.assertTrue(pm["enabled"])
        self.assertEqual(pm.get("mode"), "path")


if __name__ == "__main__":
    unittest.main()
