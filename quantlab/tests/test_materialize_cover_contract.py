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

    def test_tau_l2_clocks_minute_only_no_open(self) -> None:
        from core.research.materialize.cover_policy import resolve_tau_l2_clocks

        clks = resolve_tau_l2_clocks(
            ["open"],
            suggested=["09:35", "10:30", "11:00"],
        )
        self.assertNotIn("open", clks)
        self.assertIn("09:35", clks)
        self.assertIn("11:00", clks)

        clks2 = resolve_tau_l2_clocks(
            ["09:35"],
            suggested=["09:35", "10:30"],
        )
        self.assertNotIn("open", clks2)
        self.assertEqual(clks2[0], "09:35")
        self.assertIn("10:30", clks2)

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

    def test_run_materialize_tau_l2_minute_only_no_open(self) -> None:
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
        self.assertNotIn("open", clks)
        self.assertIn("09:35", clks)
        self.assertIn("10:30", clks)

    def test_run_materialize_mixed_rebuilds_open_and_minute(self) -> None:
        """日线 L1 与分钟 L1 一起开时，L2 用日线窗补 open、用分钟窗补钟。"""
        from core.research.materialize import run_materialize

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.catalog.suggested_tau_clocks",
            return_value=["09:35"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1",
            return_value={"ok": True},
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1_tau_grid",
            return_value={"ok": True, "clocks_ok": 1},
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            return_value={"ok": True, "by_clock": {}, "elapsed_s": 1.0},
        ) as l2:
            out = run_materialize(
                do_l1=True,
                do_l1_tau_grid=True,
                do_l2=True,
                clocks=["open"],
                lookback=1000,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(l2.call_count, 2)
        open_kw = l2.call_args_list[0].kwargs
        minute_kw = l2.call_args_list[1].kwargs
        self.assertEqual(list(open_kw.get("clocks") or []), ["open"])
        self.assertFalse(open_kw.get("require_horizon_names"))
        self.assertGreater(int(open_kw.get("lookback_days") or 0), 60)
        minute_clks = list(minute_kw.get("clocks") or [])
        self.assertNotIn("open", minute_clks)
        self.assertIn("09:35", minute_clks)
        self.assertTrue(minute_kw.get("require_horizon_names"))
        self.assertLessEqual(int(minute_kw.get("lookback_days") or 0), 60)

    def test_l1_invalidation_batch_matches_the_track(self) -> None:
        from core.research.materialize.l1_jobs import (
            materialize_l1,
            materialize_l1_tau_grid,
        )

        with mock.patch(
            "core.research.day_cube_gc.l2_invalidation_batch"
        ) as batch, mock.patch(
            "core.research.materialize.l1_jobs._materialize_l1_body",
            return_value={"ok": True},
        ):
            batch.return_value.__enter__.return_value = {}
            batch.return_value.__exit__.return_value = False
            materialize_l1(["oo"], ["000001", "000002"])
        batch.assert_called_once_with(clocks=["open"])

        with mock.patch(
            "core.research.day_cube_gc.l2_invalidation_batch"
        ) as batch, mock.patch(
            "core.research.materialize.l1_jobs._materialize_l1_tau_grid_body",
            return_value={"ok": True},
        ):
            batch.return_value.__enter__.return_value = {}
            batch.return_value.__exit__.return_value = False
            materialize_l1_tau_grid(
                ["000001", "000002"],
                clocks=["open", "09:35", "10:00"],
            )
        batch.assert_called_once_with(clocks=["09:35", "10:00"])

    def test_t0_coverage_gap_ok_when_cubes_present(self) -> None:
        from core.research.materialize.consumer import materialized_consumer
        from quant.research.t0_backtest import _t0_coverage_gap

        days = [f"2026-10-{d:02d}" for d in range(10, 20)]
        # 用 contextvar，勿 patch consumer_read_only：coverage 若在 patch
        # 期间首次 import，会把 MagicMock 绑死进模块命名空间。
        with materialized_consumer(), mock.patch(
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
        self.assertNotIn("open", called_clocks)
        self.assertTrue(any(str(c).startswith("09:") for c in called_clocks))
        fams = list(assess.call_args.kwargs.get("families") or [])
        self.assertTrue(fams)
        self.assertTrue(all(str(f).startswith("tc_") for f in fams))
        self.assertNotIn("oo", fams)

    def test_t0_coverage_gap_ignores_missing_oo(self) -> None:
        """仅缺 oo 时做 T 闸不拦（τ 齐即可）。"""
        from core.research.materialize.consumer import materialized_consumer
        from quant.research.t0_backtest import _t0_coverage_gap

        days = [f"2026-10-{d:02d}" for d in range(10, 20)]

        def _assess(codes, day_list, *, families, clocks, cube_codes=None):
            self.assertNotIn("oo", list(families or []))
            self.assertNotIn("open", list(clocks or []))
            return {"ok": True, "success": True}

        with materialized_consumer(), mock.patch(
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
            side_effect=_assess,
        ):
            gap = _t0_coverage_gap(
                ["a", "b", "c", "d"],
                10,
                {"t0_last_leg1_hm": "11:00"},
            )
        self.assertIsNone(gap)

    def test_assess_coverage_minute_only_request(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.coverage import assess_coverage

        days = ["2099-01-04", "2099-01-05"]

        def _dates(fam, code):
            return set(days) if str(fam).startswith("tc_") else set()

        with patch(
            "core.research.day_cube.day_cube_status", return_value="miss"
        ), patch(
            "core.research.feature_lake.load_stock_dates", side_effect=_dates
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ):
            report = assess_coverage(
                ["a", "b", "c", "d"],
                days,
                families=["tc_0935"],
                clocks=["09:35"],
            )
        self.assertFalse(report.get("ok"))
        self.assertEqual(report.get("hint"), "分钟线物化")
        req = report.get("request") or {}
        self.assertFalse(req.get("do_l1"))
        self.assertTrue(req.get("do_l1_tau_grid"))
        self.assertEqual(list(req.get("families") or []), [])
        self.assertIn("09:35", list(req.get("clocks") or []))

    def test_assess_coverage_open_only_request(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.coverage import assess_coverage

        days = ["2099-01-04", "2099-01-05"]

        with patch(
            "core.research.day_cube.day_cube_status", return_value="miss"
        ), patch(
            "core.research.feature_lake.load_stock_dates", return_value=set()
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ):
            report = assess_coverage(
                ["a", "b", "c", "d"],
                days,
                families=["oo", "tc", "co"],
                clocks=["open"],
            )
        self.assertFalse(report.get("ok"))
        self.assertEqual(report.get("hint"), "日线物化")
        req = report.get("request") or {}
        self.assertTrue(req.get("do_l1"))
        self.assertFalse(req.get("do_l1_tau_grid"))
        self.assertIn("oo", list(req.get("families") or []))
        self.assertEqual(list(req.get("clocks") or []), ["open"])

    def test_assess_coverage_day_cube_status_error_counts_miss(self) -> None:
        """day_cube_status 抛错时记 miss，不整段崩掉。"""
        from unittest.mock import patch

        from core.research.materialize.coverage import assess_coverage

        days = ["2099-02-03"]
        with patch(
            "core.research.day_cube.day_cube_status",
            side_effect=RuntimeError("io"),
        ), patch(
            "core.research.feature_lake.load_stock_dates",
            return_value=set(days),
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ):
            report = assess_coverage(
                ["a", "b", "c", "d"],
                days,
                families=["tc_0935"],
                clocks=["09:35"],
            )
        self.assertFalse(report.get("ok"))
        self.assertEqual(report.get("hint"), "分钟线物化")
        self.assertTrue((report.get("request") or {}).get("do_l1_tau_grid"))

    def test_t0_decision_clocks_normalize_hhmm(self) -> None:
        """0935 与 09:35 同口径，且不超过 last_leg1。"""
        from quant.research.t0_backtest import _t0_minute_decision_clocks

        with mock.patch(
            "core.research.materialize.catalog.suggested_tau_clocks",
            return_value=["0935", "10:30", "11:30"],
        ):
            clks = _t0_minute_decision_clocks({"t0_last_leg1_hm": "11:00"})
        self.assertIn("09:35", clks)
        self.assertIn("10:30", clks)
        self.assertNotIn("11:30", clks)
        self.assertNotIn("0935", clks)

    def test_t0_coverage_gap_assess_exception_soft_fails(self) -> None:
        from core.research.materialize.consumer import materialized_consumer
        from quant.research.t0_backtest import _t0_coverage_gap

        days = [f"2026-10-{d:02d}" for d in range(10, 20)]
        with materialized_consumer(), mock.patch(
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
            side_effect=RuntimeError("boom"),
        ):
            gap = _t0_coverage_gap(
                ["a", "b", "c", "d"],
                10,
                {"t0_last_leg1_hm": "11:00"},
            )
        self.assertIsNotNone(gap)
        self.assertFalse(gap.get("success"))
        self.assertIn("覆盖检查失败", str(gap.get("error") or ""))
        self.assertEqual(gap.get("hint"), "分钟线物化")

    def test_cover_contract_does_not_pollute_consumer_flag(self) -> None:
        """回归：cover 单测不得把 coverage.consumer_read_only 留成 MagicMock。"""
        from core.research.materialize import coverage as cov
        from core.research.materialize.consumer import consumer_read_only

        self.assertFalse(consumer_read_only())
        self.assertTrue(callable(cov._mat_consumer.consumer_read_only))
        self.assertFalse(cov._mat_consumer.consumer_read_only())


if __name__ == "__main__":
    unittest.main()
