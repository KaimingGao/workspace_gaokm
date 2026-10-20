"""物化覆盖契约回归：批挂 5m / 探针 / L2 ok / 窗头列 / 做 T gap。"""

from __future__ import annotations

import unittest
from unittest import mock


class CoverPolicyUnitTests(unittest.TestCase):
    def test_cover_need_and_reaches(self) -> None:
        from core.research.materialize.cover_policy import (
            LAKE_COVER_FRAC,
            cover_need,
            cover_reaches,
        )

        self.assertEqual(LAKE_COVER_FRAC, 0.5)
        self.assertEqual(cover_need(50), 25)
        self.assertEqual(cover_need(800, minimum=2), 400)
        self.assertFalse(cover_reaches(0, 50))
        self.assertTrue(cover_reaches(25, 50))

    def test_scale_eligible_not_span(self) -> None:
        from core.research.materialize.cover_policy import (
            clamp_span_days,
            scale_eligible_stocks,
        )

        self.assertEqual(
            scale_eligible_stocks(24, probe_n=24, pool_n=800),
            800,
        )
        # 仅当探针跨度 > 2×写出日才钳（禁止假放大）
        self.assertEqual(clamp_span_days(192, 0), 192)
        self.assertEqual(clamp_span_days(6400, 192), 192)
        self.assertEqual(clamp_span_days(400, 192), 192)
        self.assertEqual(clamp_span_days(200, 192), 200)

    def test_finalize_l2_rejects_zero_cubes(self) -> None:
        from core.research.materialize.cover_policy import finalize_l2_report

        report = {
            "ok": True,
            "by_clock": {
                "09:35": {
                    "ok": True,
                    "cubes_hit": 0,
                    "cubes_built": 0,
                    "days": 50,
                    "slim_reject": 50,
                }
            },
        }
        out = finalize_l2_report(report, ["09:35"])
        self.assertFalse(out.get("ok"))
        self.assertIn("need≥", str(out.get("error") or ""))
        self.assertIn("slim_reject=50", str(out["by_clock"]["09:35"].get("error")))

    def test_orchestrate_error_includes_nested_detail(self) -> None:
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.catalog.suggested_tau_clocks",
            return_value=["09:35"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1_tau_grid",
            return_value={"ok": True},
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={
                "ok": False,
                "error": "立方 09:35 覆盖不足 hit+built=0/50 need≥25",
            },
        ):
            out = run_materialize(
                do_l1=False,
                do_l1_tau_grid=True,
                do_l2=True,
                clocks=["open"],
            )
        self.assertFalse(out.get("ok"))
        err = str(out.get("error") or "")
        self.assertIn("l2", err)
        self.assertIn("hit+built=0/50", err)

    def test_format_cube_cover_error_includes_need_and_slim(self) -> None:
        from core.research.materialize.cover_policy import format_cube_cover_error

        text = format_cube_cover_error(
            "09:35",
            hit=0,
            built=0,
            n_days=50,
            slim_reject=50,
        )
        self.assertIn("立方 09:35", text)
        self.assertIn("hit+built=0/50", text)
        self.assertIn("need≥25", text)
        self.assertIn("slim_reject=50", text)

    def test_probe_must_not_scale_calendar_span(self) -> None:
        """大池小探针：192 日不得因 ×(池/探针) 被判覆盖不足。"""
        from core.research.materialize.l1_jobs import _clock_reaches_lake

        row = {"n_stocks": 798, "n_dates": 192}
        self.assertFalse(_clock_reaches_lake(row, n_eligible=800, n_days=6400))
        self.assertTrue(_clock_reaches_lake(row, n_eligible=800, n_days=192))

    def test_tau_l2_clocks_keep_open(self) -> None:
        from core.research.materialize.cover_policy import resolve_tau_l2_clocks

        clks = resolve_tau_l2_clocks(
            ["open"],
            do_l1=False,
            suggested=["09:35", "10:30", "11:00"],
        )
        self.assertIn("open", clks)
        self.assertIn("09:35", clks)
        self.assertIn("11:00", clks)

        clks2 = resolve_tau_l2_clocks(
            ["09:35"],
            do_l1=False,
            suggested=["09:35", "10:30"],
        )
        self.assertEqual(clks2[0], "open")
        self.assertIn("09:35", clks2)

    def test_l2_empty_build_fails_cover(self) -> None:
        from core.research.materialize.cover_policy import LAKE_COVER_FRAC
        from core.research.materialize.l2_jobs import _clock_reaches_days

        self.assertFalse(
            _clock_reaches_days(
                {"cubes_hit": 0, "cubes_built": 0},
                n_days=50,
                lake_frac=LAKE_COVER_FRAC,
            )
        )
        self.assertTrue(
            _clock_reaches_days(
                {"cubes_hit": 10, "cubes_built": 20},
                n_days=50,
                lake_frac=LAKE_COVER_FRAC,
            )
        )

    def test_horizon_cover_frac_not_full_column_set(self) -> None:
        """窗头验收用半数而非 1.0（早盘 ath 常缺，全齐列会 slim_reject 全滅）。"""
        from core.research.materialize.cover_policy import (
            HORIZON_COVER_FRAC,
            LAKE_COVER_FRAC,
        )
        from core.research.materialize.horizon_names import (
            minute_horizon_required_names,
            names_cover_horizon,
        )

        self.assertEqual(HORIZON_COVER_FRAC, LAKE_COVER_FRAC)
        self.assertLess(HORIZON_COVER_FRAC, 1.0)
        # 契约：required 里大量 ath；真实早盘立方往往缺一部分仍应可过半数门槛
        req = list(minute_horizon_required_names())
        ath_n = sum(1 for n in req if "ath" in n)
        self.assertGreater(ath_n, len(req) // 2)
        # 合成：恰好半数命中 → 0.5 过、1.0 拒
        need = [f"col_{i}" for i in range(20)]
        kept = need[:10] + ["pad_a", "pad_b", "pad_c"]
        self.assertTrue(
            names_cover_horizon(kept, min_frac=HORIZON_COVER_FRAC, need=need)
        )
        self.assertFalse(names_cover_horizon(kept, min_frac=1.0, need=need))

    def test_run_materialize_tau_wires_open_into_l2(self) -> None:
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.catalog.suggested_tau_clocks",
            return_value=["09:35", "10:30"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1_tau_grid",
            return_value={"ok": True, "clocks_ok": 2},
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={"ok": True},
        ) as l2:
            out = run_materialize(
                do_l1=False,
                do_l1_tau_grid=True,
                do_l2=True,
                clocks=["open"],
            )
        self.assertTrue(out.get("ok"), out)
        clks = list(l2.call_args.kwargs.get("clocks") or [])
        self.assertIn("open", clks)
        self.assertIn("09:35", clks)

    def test_t0_coverage_gap_ok_when_cubes_present(self) -> None:
        from quant.research.t0_backtest import _t0_coverage_gap

        days = [f"2026-10-{d:02d}" for d in range(10, 20)]
        with mock.patch(
            "core.research.materialize.consumer.consumer_read_only",
            return_value=True,
        ), mock.patch(
            "core.research.materialize.coverage.recent_trading_days",
            return_value=days,
        ), mock.patch(
            "core.store.list_minute_covered_dates",
            return_value=days,
        ), mock.patch(
            "core.research.materialize.coverage.read_cube_codes",
            return_value=["a", "b", "c", "d"],
        ), mock.patch(
            "core.research.materialize.coverage.assess_coverage",
            return_value={"ok": True, "success": True},
        ) as assess:
            gap = _t0_coverage_gap(
                ["a", "b", "c", "d"],
                10,
                {"t0_last_leg1_hm": "10:30"},
            )
        self.assertIsNone(gap)
        called_clocks = list(assess.call_args.kwargs.get("clocks") or [])
        self.assertIn("open", called_clocks)
        self.assertTrue(any(str(c).startswith("09:") for c in called_clocks))


if __name__ == "__main__":
    unittest.main()
