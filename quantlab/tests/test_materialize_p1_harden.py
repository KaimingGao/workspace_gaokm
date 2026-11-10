"""P1 加固：双卡 L2 不短窗盖 open；open τc 列硬齐。"""

from __future__ import annotations

import unittest
from unittest import mock


class MergeL2KeepFirstTests(unittest.TestCase):
    def test_merge_keeps_first_open_row(self) -> None:
        from core.research.materialize.orchestrate import _merge_l2

        long_open = {
            "ok": True,
            "elapsed_s": 1.0,
            "by_clock": {
                "open": {"ok": True, "cubes_hit": 1000, "cubes_built": 0, "days": 1000}
            },
        }
        short_open = {
            "ok": False,
            "error": "短窗失败",
            "elapsed_s": 0.5,
            "by_clock": {
                "open": {"ok": False, "cubes_hit": 10, "cubes_built": 0, "days": 60},
                "09:35": {"ok": True, "cubes_hit": 60, "cubes_built": 0, "days": 60},
            },
        }
        out = _merge_l2([long_open, short_open])
        self.assertFalse(out.get("ok"))
        self.assertEqual(out["by_clock"]["open"]["days"], 1000)
        self.assertEqual(out["by_clock"]["open"]["cubes_hit"], 1000)
        self.assertIn("09:35", out["by_clock"])


class DualCardMinuteFallbackTests(unittest.TestCase):
    def test_empty_minute_does_not_rerun_open_short(self) -> None:
        from core.research.materialize import run_materialize

        calls = []

        def _l2(*_a, **kwargs):
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "by_clock": {
                    str((kwargs.get("clocks") or ["?"])[0]): {
                        "ok": True,
                        "cubes_hit": 1,
                        "days": 1,
                    }
                },
                "elapsed_s": 0.1,
            }

        with mock.patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1",
            return_value={"ok": True},
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l1_tau_grid",
            return_value={"ok": True},
        ), mock.patch(
            "core.research.materialize.orchestrate.suggested_tau_clocks",
            return_value=[],
        ), mock.patch(
            "core.research.materialize.cover_policy.resolve_tau_l2_clocks",
            return_value=[],
        ), mock.patch(
            "core.research.materialize.orchestrate.materialize_l2",
            side_effect=_l2,
        ):
            out = run_materialize(
                do_l1=True,
                do_l1_tau_grid=True,
                do_l2=True,
                clocks=["open"],
                lookback=100,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(calls[0].get("clocks") or []), ["open"])
        self.assertGreater(int(calls[0].get("lookback_days") or 0), 60)


class OpenTcColumnGateTests(unittest.TestCase):
    def test_open_tc_required_hard_gate(self) -> None:
        from core.research.materialize.horizon_names import (
            names_cover_horizon,
            open_cube_required_names,
            open_tc_required_names,
        )

        tc = list(open_tc_required_names())
        self.assertEqual(len(tc), 8)
        oo_only = [n for n in open_cube_required_names() if n not in set(tc)]
        self.assertGreater(len(oo_only), 100)
        # oo 单边过半整体门槛，但 tc 子集 1.0 必须拒
        self.assertTrue(
            names_cover_horizon(oo_only, min_frac=0.5, need=open_cube_required_names())
        )
        self.assertFalse(names_cover_horizon(oo_only, min_frac=1.0, need=tc))

    def test_critical_missing_blocks_cs_z(self) -> None:
        from core.research.materialize.cube_z import _critical_open_tc_missing
        from core.research.materialize.horizon_names import open_tc_required_names

        tc = list(open_tc_required_names())
        miss = _critical_open_tc_missing(tc[:2], tc)
        self.assertEqual(miss, tc[:2])
        self.assertEqual(_critical_open_tc_missing(tc[:2], ["z_oo_mom"]), [])


class StemEmptyPanelTests(unittest.TestCase):
    def test_empty_panel_dates_skip_scored_through(self) -> None:
        from core.research.incremental_panel import stem_extra_meta

        bars = [
            {
                "date": "2024-01-02",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
        meta = stem_extra_meta(bars, panel_dates=[])
        self.assertNotIn("scored_through", meta)
        self.assertEqual(meta.get("panel_end"), "")


if __name__ == "__main__":
    unittest.main()
