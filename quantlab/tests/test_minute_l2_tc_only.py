"""分钟 L2：无 oo/co 时仍可按 tc 建钟立方。"""

from __future__ import annotations

import unittest
from unittest import mock


class TestMinuteL2TcOnly(unittest.TestCase):
    def test_build_day_cubes_accepts_empty_oo_co(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        codes = ["000001", "000002", "000003", "000004"]
        days = ["2099-06-02"]
        # 假 tc panel：每票一天一行
        names = ["z_τh_mom", "raw_ath_gap", "ret_open_to_tau"]
        row = __import__("numpy").zeros(len(names), dtype="float64")
        row[:] = 1.0
        tc_s = {c: (names, {days[0]: row.copy()}) for c in codes}

        ensured = []

        def _ensure(items, date=None, day=None, universe_codes=None, clock="open", **kwargs):
            d = date or day
            ensured.append((d, clock, len(items or [])))
            return object()  # non-DayCube → _keep_cube returns True

        with mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            side_effect=_ensure,
        ), mock.patch(
            "core.research.materialize.cube_batch._alias_plan",
            side_effect=RuntimeError("force item path"),
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ):
            stats = build_day_cubes_from_lake(
                codes,
                days,
                family_oo="oo",
                family_tc="tc_0935",
                family_co="co",
                clock="09:35",
                universe_codes=codes,
                min_items=2,
                require_tc_day=True,
                require_oo_item=False,
                shared_oo={},
                shared_co={},
                shared_tc=tc_s,
                swallow_ensure_errors=True,
                replace_existing=True,
            )
        self.assertGreaterEqual(int(stats.get("cubes_built") or 0), 1)
        self.assertTrue(ensured)
        self.assertEqual(ensured[0][1], "09:35")

    def test_build_skips_day_without_tc(self) -> None:
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        codes = ["000001", "000002", "000003", "000004"]
        days = ["2099-06-03"]
        with mock.patch(
            "core.research.materialize.cube_batch._alias_plan",
            side_effect=RuntimeError("force item path"),
        ):
            stats = build_day_cubes_from_lake(
                codes,
                days,
                family_oo="oo",
                family_tc="tc_0935",
                family_co="co",
                clock="09:35",
                universe_codes=codes,
                min_items=2,
                require_tc_day=True,
                require_oo_item=False,
                shared_oo={},
                shared_co={},
                shared_tc={},
                swallow_ensure_errors=True,
            )
        self.assertEqual(int(stats.get("cubes_built") or 0), 0)
        self.assertGreaterEqual(int(stats.get("days_skipped") or 0), 1)

    def test_build_tolerates_non_dict_shared_oo(self) -> None:
        """脏 shared_oo（非 dict）当空 panel，不炸。"""
        from core.research.materialize.cube_batch import build_day_cubes_from_lake

        codes = ["000001", "000002", "000003", "000004"]
        days = ["2099-06-04"]
        names = ["z_τh_mom"]
        row = __import__("numpy").zeros(len(names), dtype="float64")
        row[:] = 1.0
        tc_s = {c: (names, {days[0]: row.copy()}) for c in codes}

        with mock.patch(
            "core.research.batch_score.ensure_day_cube_for_items",
            return_value=object(),
        ), mock.patch(
            "core.research.materialize.cube_batch._alias_plan",
            side_effect=RuntimeError("force item path"),
        ), mock.patch(
            "core.research.materialize.horizon_names.names_cover_horizon",
            return_value=True,
        ):
            stats = build_day_cubes_from_lake(
                codes,
                days,
                family_oo="oo",
                family_tc="tc_0935",
                family_co="co",
                clock="09:35",
                universe_codes=codes,
                min_items=2,
                require_tc_day=True,
                require_oo_item=False,
                shared_oo="bad",  # type: ignore[arg-type]
                shared_co={},
                shared_tc=tc_s,
                swallow_ensure_errors=True,
                replace_existing=True,
            )
        self.assertGreaterEqual(int(stats.get("cubes_built") or 0), 1)


if __name__ == "__main__":
    unittest.main()
