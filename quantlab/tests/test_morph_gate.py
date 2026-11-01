"""形态硬闸：高波动 + 弱开盘。"""

from __future__ import annotations

import unittest


class MorphGateUnitTests(unittest.TestCase):
    def test_skip_when_high_vol_and_weak_open(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True}
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

        cfg = {"morph_gate_enabled": True}
        self.assertIsNone(
            morph_gate_skip_reason({"morph_vol_p": 0.99, "morph_a1": 0.01}, cfg)
        )

    def test_gap_leg_alone_without_vol_p(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True}
        # 异常低开写死 8%：a1≤−8% 不需 vol_p
        skip = morph_gate_skip_reason({"morph_a1": -0.08}, cfg)
        self.assertIsNotNone(skip)
        self.assertIn("异常低开", skip or "")
        # 未过 gap 且无 vol_p → 高波腿 fail-open
        self.assertIsNone(morph_gate_skip_reason({"morph_a1": -0.05}, cfg))
        self.assertIsNone(morph_gate_skip_reason({"morph_a1": -0.01}, cfg))

    def test_cfg_thresholds_ignored_hardcoded(self):
        """旧 cfg 门槛字段被忽略；产品写死 vol_p≥0.9∧a1<0 / a1≤−8%。"""
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {
            "morph_gate_enabled": True,
            "morph_gap_max": 0.0,  # 旧「关」也不再生效
            "morph_vol_pctl": 0.99,
            "morph_a1_max": 0.05,
            "morph_vol20_min": 0.5,
        }
        # −8% 仍走异常低开（无 vol_p）
        self.assertIn(
            "异常低开",
            morph_gate_skip_reason({"morph_a1": -0.08}, cfg) or "",
        )
        # 高波弱开：绝对 vol20_min 不再参与；cfg 抬高分位也不拦 vol_p=0.95
        skip = morph_gate_skip_reason(
            {"morph_a1": -0.02, "morph_vol_p": 0.95, "morph_vol20": 0.2},
            cfg,
        )
        self.assertIsNotNone(skip)
        self.assertIn("高波弱开", skip or "")

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
            },
            rs=0.02,
        )
        self.assertIsNotNone(skip)
        self.assertIn("形态闸", skip or "")

    def test_held_clear_skips_morph_gate(self):
        """已持仓清仓判定不因形态闸触发（与 UI「不清仓」一致）。"""
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        item = {
            "predicted_score": 2.0,
            "y_τc": 1.0,
            "morph_vol_p": 0.95,
            "morph_a1": -0.02,
        }
        cfg = {
            "fuse_enter": 0.0,
            "morph_gate_enabled": True,
        }
        self.assertIsNotNone(
            rank_lot_enter_skip_reason(item, cfg, rs=0.02, apply_morph_gate=True)
        )
        self.assertIsNone(
            rank_lot_enter_skip_reason(item, cfg, rs=0.02, apply_morph_gate=False)
        )

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
        self.assertNotIn("morph_vol_pctl", cfg)
        self.assertNotIn("morph_gap_max", cfg)

    def test_path_matrix_strips_legacy_threshold_keys(self):
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg

        cfg = get_path_matrix_cfg(
            {
                "morph_gate_enabled": True,
                "morph_vol_pctl": 0.99,
                "morph_a1_max": 0.05,
                "morph_gap_max": 0.03,
                "morph_vol20_min": 0.5,
            }
        )
        self.assertTrue(cfg.get("morph_gate_enabled"))
        for k in (
            "morph_vol_pctl",
            "morph_a1_max",
            "morph_gap_max",
            "morph_vol20_min",
        ):
            self.assertNotIn(k, cfg)

    def test_string_false_does_not_enable(self):
        from core.paper.rebalance.morph_gate import morph_gate_enabled

        self.assertFalse(morph_gate_enabled({"morph_gate_enabled": "false"}))
        self.assertFalse(morph_gate_enabled({"morph_gate_enabled": "0"}))
        self.assertTrue(morph_gate_enabled({"morph_gate_enabled": "true"}))

    def test_stamp_clears_stale_vol_p_when_pool_small(self):
        from core.paper.rebalance.morph_gate import stamp_morph_cs_on_items

        items = [
            {"stock_code": "000001", "morph_vol_p": 0.99},
            {"stock_code": "000002", "morph_vol_p": 0.95},
        ]
        stamp_morph_cs_on_items(
            items,
            metrics_by_code={
                "000001": {"a1": -0.01, "vol20": 0.2},
                "000002": {"a1": -0.02, "vol20": 0.3},
            },
        )
        self.assertNotIn("morph_vol_p", items[0])
        self.assertNotIn("morph_vol_p", items[1])
        self.assertIn("morph_a1", items[0])

    def test_stamp_dedupes_codes_for_percentile(self):
        from core.paper.rebalance.morph_gate import stamp_morph_cs_on_items

        items = [{"stock_code": f"{i:06d}"} for i in range(1, 9)]
        items.append({"stock_code": "000001"})  # duplicate
        metrics = {
            f"{i:06d}": {"a1": -0.01, "vol20": float(i)} for i in range(1, 9)
        }
        stamp_morph_cs_on_items(items, metrics_by_code=metrics)
        self.assertEqual(items[0]["morph_vol_p"], items[-1]["morph_vol_p"])

    def test_skip_ignores_out_of_range_vol_p(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True}
        self.assertIsNone(
            morph_gate_skip_reason({"morph_vol_p": 1.5, "morph_a1": -0.02}, cfg)
        )

    def test_try_stamp_tolerates_bad_factors(self):
        from core.paper.rebalance.morph_gate import try_stamp_morph_from_opens

        items = [
            {"stock_code": "000001", "open": 10.0, "factors": ["not-a-dict"]},
            {"stock_code": "000002", "open": 11.0, "prev_close": 10.0},
        ]
        n_a1, n_vp = try_stamp_morph_from_opens(items, opens={})
        self.assertGreaterEqual(n_a1, 1)
        self.assertEqual(n_vp, 0)

    def test_metrics_from_bars_respects_as_of(self):
        from core.paper.rebalance.morph_gate import metrics_from_bars

        bars = [
            {"date": "2026-10-20", "close": 10.0, "open": 9.9},
            {"date": "2026-10-21", "close": 10.5, "open": 10.1},
            {"date": "2026-10-22", "close": 12.0, "open": 11.0},  # 当日不应进昨收
        ]
        m = metrics_from_bars(bars, open_t=10.8, as_of="2026-10-22")
        self.assertAlmostEqual(m["prev_close"], 10.5, places=5)
        self.assertIsNotNone(m["a1"])
        self.assertAlmostEqual(m["a1"], 10.8 / 10.5 - 1.0, places=5)

    def test_metrics_no_open_t_does_not_use_prev_open(self):
        from core.paper.rebalance.morph_gate import metrics_from_bars

        bars = []
        px = 10.0
        for i in range(1, 25):
            day = f"2024-05-{i:02d}"
            bars.append({"date": day, "open": px, "close": px * 1.01})
            px *= 1.01
        m = metrics_from_bars(bars, open_t=None, as_of="2024-05-22")
        self.assertIsNotNone(m["prev_close"])
        self.assertIsNone(m["a1"])
        self.assertIsNotNone(m["vol20"])

    def test_deep_history_as_of_with_explicit_bars(self):
        """深历史 as_of + 显式 bars：vol/a1 可算，形态闸可拦（不依赖 cache 尾）。"""
        from core.paper.rebalance.morph_gate import (
            metrics_from_bars,
            morph_gate_skip_reason,
            stamp_morph_cs_on_items,
        )

        def _series(base: float, amp: float):
            bars = []
            px = base
            for i in range(30):
                day = f"2024-04-{i + 1:02d}"
                o = px
                c = px * (1.0 + amp * (0.5 if i % 2 == 0 else -0.5))
                bars.append({"date": day, "open": o, "close": c})
                px = c
            return bars

        # 高波动票 + 低波动同伴，凑满截面 ≥8
        bars_by = {}
        items = []
        for i in range(1, 9):
            code = f"{i:06d}"
            amp = 0.08 if i == 1 else 0.005 * i
            bars_by[code] = _series(10.0 + i, amp)
            items.append({"stock_code": code})

        as_of = "2024-04-25"
        metrics = {}
        opens = {}
        for it in items:
            code = it["stock_code"]
            prev = None
            for b in bars_by[code]:
                if b["date"] < as_of:
                    prev = b["close"]
            # 000001 弱开；其余平开
            open_t = (prev * 0.97) if code == "000001" else (prev * 1.01)
            opens[code] = open_t
            metrics[code] = metrics_from_bars(
                bars_by[code], open_t=open_t, as_of=as_of
            )
            self.assertIsNotNone(metrics[code]["vol20"], code)
            self.assertIsNotNone(metrics[code]["a1"], code)

        stamp_morph_cs_on_items(items, metrics_by_code=metrics)
        self.assertIn("morph_vol_p", items[0])
        cfg = {"morph_gate_enabled": True}
        skip = morph_gate_skip_reason(items[0], cfg)
        self.assertIsNotNone(skip)
        self.assertIn("形态闸", skip or "")

    def test_plan_rank_lot_day_uses_bars_by_code(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        as_of = "2024-04-25"
        bars_by = {}
        scored = []
        opens = {}
        prices = {}
        for i in range(1, 9):
            code = f"{i:06d}"
            bars = []
            px = 10.0 + i
            amp = 0.08 if i == 1 else 0.004 * i
            for d in range(1, 31):
                day = f"2024-04-{d:02d}"
                o = px
                c = px * (1.0 + amp * (0.5 if d % 2 == 0 else -0.5))
                bars.append({"date": day, "open": o, "close": c})
                px = c
            bars_by[code] = bars
            prev = [b["close"] for b in bars if b["date"] < as_of][-1]
            open_t = prev * 0.96 if i == 1 else prev * 1.01
            opens[code] = open_t
            prices[code] = open_t
            scored.append(
                {
                    "stock_code": code,
                    "predicted_score": 2.0,
                    "y_τc": 1.0,
                    "y_oo_rank": i,
                    "y_τc_rank": i,
                }
            )

        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=200_000.0,
            prices=prices,
            opens=opens,
            cfg={
                "fuse_enter": 0.0,
                "fuse_strong": 0.0,
                "morph_gate_enabled": True,
                "lot_base_amount": 10_000,
                "lot_strong_amount": 10_000,
                "oo_rank_max": 100,
                "top_k": 15,
            },
            as_of=as_of,
            bars_by_code=bars_by,
        )
        stamp = out.get("morph_stamp") or {}
        self.assertEqual(stamp.get("bars_source"), "caller")
        self.assertGreaterEqual(int(stamp.get("n_vp") or 0), 8)
        morph_skips = [
            s
            for s in (out.get("skips") or [])
            if "形态闸" in str(s.get("reason") or "")
        ]
        self.assertTrue(morph_skips, msg=f"skips={out.get('skips')}")

    def test_metrics_unsorted_bars_and_yyyymmdd_as_of(self):
        from core.paper.rebalance.morph_gate import metrics_from_bars

        bars = [
            {"date": "2026-10-22", "close": 12.0},
            {"date": "2026-10-20", "close": 10.0},
            {"date": "2026-10-21", "close": 10.5},
        ]
        m = metrics_from_bars(bars, open_t=10.8, as_of="20261022")
        self.assertAlmostEqual(m["prev_close"], 10.5, places=5)
        self.assertAlmostEqual(m["a1"], 10.8 / 10.5 - 1.0, places=5)

    def test_dirty_a1_fail_open(self):
        from core.paper.rebalance.morph_gate import morph_gate_skip_reason

        cfg = {"morph_gate_enabled": True}
        # |a1|>50% 视为脏数，两腿都不拦
        self.assertIsNone(
            morph_gate_skip_reason({"morph_a1": -0.9, "morph_vol_p": 0.99}, cfg)
        )
        self.assertIsNone(
            morph_gate_skip_reason({"morph_a1": 0.9, "morph_vol_p": 0.99}, cfg)
        )

    def test_stamp_opens_int_keys_and_none_items(self):
        from core.paper.rebalance.morph_gate import try_stamp_morph_from_opens

        self.assertEqual(try_stamp_morph_from_opens(None, opens={1: 10.0}), (0, 0))
        items = [{"stock_code": "000001", "prev_close": 10.0}]
        n_a1, n_vp = try_stamp_morph_from_opens(items, opens={1: 9.5})
        self.assertEqual(n_a1, 1)
        self.assertEqual(n_vp, 0)
        self.assertAlmostEqual(items[0]["morph_a1"], -0.05, places=5)

    def test_bars_filtered_empty_falls_back_to_row_prev(self):
        from core.paper.rebalance.morph_gate import try_stamp_morph_from_opens

        # bars 全是 as_of 当日及以后 → 滤空，回退行内昨收
        items = [{"stock_code": "000001", "prev_close": 10.0, "open": 9.0}]
        bars = {"000001": [{"date": "2026-10-22", "close": 12.0}]}
        n_a1, _ = try_stamp_morph_from_opens(
            items, opens={"000001": 9.0}, bars_by_code=bars, as_of="2026-10-22"
        )
        self.assertEqual(n_a1, 1)
        self.assertAlmostEqual(items[0]["morph_a1"], -0.1, places=5)

    def test_enabled_bad_cfg_fail_closed_switch(self):
        from core.paper.rebalance.morph_gate import morph_gate_enabled

        self.assertFalse(morph_gate_enabled(None))
        self.assertFalse(morph_gate_enabled("not-a-mapping"))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
