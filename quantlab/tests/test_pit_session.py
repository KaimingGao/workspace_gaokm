"""开盘 PIT provenance / ensure / warmup 重建闸。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.research.pit_session import (
    BUILDER_LAKE_OPEN,
    BUILDER_SESSION_OPEN_QUOTE,
    OPEN_SOURCE_DAILY,
    OPEN_SOURCE_QUOTE,
    PHASE_FINAL,
    PHASE_PROVISIONAL,
    force_open_rebuild_after_status_ok,
    membership_drift,
    meta_is_final_open,
    meta_needs_lake_rebuild,
    provenance_dict,
    should_force_open_lake_rebuild,
    stamp_cube_provenance,
)


class TestPitSessionProvenance(unittest.TestCase):
    def test_meta_final_vs_provisional(self) -> None:
        final = provenance_dict(
            phase=PHASE_FINAL,
            open_source=OPEN_SOURCE_DAILY,
            builder=BUILDER_LAKE_OPEN,
            eod_as_of="2026-11-09",
        )
        prov = provenance_dict(
            phase=PHASE_PROVISIONAL,
            open_source=OPEN_SOURCE_QUOTE,
            builder=BUILDER_SESSION_OPEN_QUOTE,
        )
        self.assertTrue(meta_is_final_open(final))
        self.assertFalse(meta_needs_lake_rebuild(final))
        self.assertFalse(meta_is_final_open(prov))
        self.assertTrue(meta_needs_lake_rebuild(prov))
        self.assertTrue(meta_needs_lake_rebuild({}))  # legacy
        self.assertTrue(meta_needs_lake_rebuild(None))

    def test_stamp_cube_slim(self) -> None:
        cube = MagicMock()
        cube.slim = {}
        stamp_cube_provenance(
            cube,
            phase=PHASE_PROVISIONAL,
            open_source=OPEN_SOURCE_QUOTE,
            builder=BUILDER_SESSION_OPEN_QUOTE,
            eod_as_of="2026-11-06",
            missing_open_n=3,
        )
        slim = dict(cube.slim)
        self.assertEqual(slim.get("phase"), PHASE_PROVISIONAL)
        self.assertEqual(slim.get("builder"), BUILDER_SESSION_OPEN_QUOTE)
        self.assertEqual(slim.get("eod_as_of"), "2026-11-06")
        self.assertEqual(slim.get("missing_open_n"), 3)

    def test_force_rebuild_when_daily_ready(self) -> None:
        meta = provenance_dict(
            phase=PHASE_PROVISIONAL,
            open_source=OPEN_SOURCE_QUOTE,
            builder=BUILDER_SESSION_OPEN_QUOTE,
        )
        with patch(
            "core.research.pit_session.daily_bars_ready_for_day", return_value=True
        ):
            self.assertTrue(
                should_force_open_lake_rebuild("2026-11-09", ["a", "b"], meta)
            )
        with patch(
            "core.research.pit_session.daily_bars_ready_for_day", return_value=False
        ):
            self.assertFalse(
                should_force_open_lake_rebuild("2026-11-09", ["a", "b"], meta)
            )
        final = provenance_dict(
            phase=PHASE_FINAL,
            open_source=OPEN_SOURCE_DAILY,
            builder=BUILDER_LAKE_OPEN,
        )
        with patch(
            "core.research.pit_session.daily_bars_ready_for_day", return_value=True
        ):
            self.assertFalse(
                should_force_open_lake_rebuild("2026-11-09", ["a", "b"], final)
            )

    def test_force_rebuild_after_status_ok_fail_closed(self) -> None:
        """meta 读失败 + 日线已齐 → 必须重建，不得当 hit。"""
        with (
            patch(
                "core.research.day_cube.read_day_cube_meta",
                side_effect=RuntimeError("meta boom"),
            ),
            patch(
                "core.research.pit_session.daily_bars_ready_for_day",
                return_value=True,
            ),
        ):
            self.assertTrue(
                force_open_rebuild_after_status_ok(
                    "2026-11-09", ["a", "b"], clock="open"
                )
            )
        with (
            patch(
                "core.research.day_cube.read_day_cube_meta",
                side_effect=RuntimeError("meta boom"),
            ),
            patch(
                "core.research.pit_session.daily_bars_ready_for_day",
                side_effect=RuntimeError("as_of boom"),
            ),
        ):
            self.assertTrue(
                force_open_rebuild_after_status_ok(
                    "2026-11-09", ["a", "b"], clock="open"
                )
            )

    def test_ensure_session_open_marks_needs_final_on_auto_fallback(self) -> None:
        from core.research.pit_session import ensure_session_open_l2

        with (
            patch(
                "core.research.materialize.orchestrate.resolve_materialize_codes",
                return_value=["000001", "000002"],
            ),
            patch(
                "core.research.pit_session.daily_bars_ready_for_day",
                return_value=True,
            ),
            patch(
                "core.research.pit_session._build_final_open_from_lake",
                return_value={"ok": False, "error": "lake boom"},
            ),
            patch(
                "core.research.materialize.session_open.materialize_session_open_l2",
                return_value={"ok": True, "elapsed_s": 0.1},
            ),
        ):
            out = ensure_session_open_l2(
                cycle_day="2026-11-09", codes=["000001", "000002"], prefer="auto"
            )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("needs_final"))
        self.assertEqual(out.get("phase"), PHASE_PROVISIONAL)
        self.assertEqual(out.get("lake_error"), "lake boom")

    def test_save_day_cube_lifts_provenance(self) -> None:
        import numpy as np

        from core.research.day_cube import DayCube, save_day_cube
        from core.research.feature_registry import universe_hash

        codes = ["000001", "000002"]
        uh = universe_hash(codes)
        cube = DayCube(
            date="2026-11-09",
            codes=codes,
            names=["raw_aoo_GAP"],
            X_raw=np.array([[0.1], [0.2]], dtype=np.float64),
            z_mean=np.array([0.15], dtype=np.float64),
            z_std=np.array([0.05], dtype=np.float64),
            universe_hash=uh,
            clock="open",
            slim={},
        )
        stamp_cube_provenance(
            cube,
            phase=PHASE_PROVISIONAL,
            open_source=OPEN_SOURCE_QUOTE,
            builder=BUILDER_SESSION_OPEN_QUOTE,
            eod_as_of="2026-11-06",
        )
        with patch("core.research.day_cube.enabled", return_value=True), patch(
            "core.research.day_cube._atomic_write_bytes"
        ), patch("core.research.day_cube._atomic_write_json") as write_json, patch(
            "core.research.day_cube.os.makedirs"
        ):
            self.assertTrue(save_day_cube(cube))
        self.assertTrue(write_json.called)
        meta = write_json.call_args[0][1]
        self.assertEqual(meta.get("phase"), PHASE_PROVISIONAL)
        self.assertEqual(meta.get("builder"), BUILDER_SESSION_OPEN_QUOTE)
        self.assertEqual(meta.get("open_source"), OPEN_SOURCE_QUOTE)

    def test_membership_drift(self) -> None:
        self.assertIsNone(membership_drift(100, 101, frac_thresh=0.02))
        d = membership_drift(100, 110, frac_thresh=0.02)
        self.assertIsNotNone(d)
        self.assertEqual(d["prev_n_codes"], 100)
        self.assertEqual(d["n_codes"], 110)
        self.assertIn("重标定", d["note"])

    def test_ensure_auto_falls_back_to_quote(self) -> None:
        from core.research.pit_session import ensure_session_open_l2

        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-11-09"
        ), patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["a", "b"],
        ), patch(
            "core.research.pit_session.daily_bars_ready_for_day", return_value=False
        ), patch(
            "core.research.materialize.session_open.materialize_session_open_l2",
            return_value={
                "ok": True,
                "success": True,
                "persisted": True,
                "cycle_day": "2026-11-09",
                "phase": PHASE_PROVISIONAL,
                "open_source": OPEN_SOURCE_QUOTE,
                "builder": BUILDER_SESSION_OPEN_QUOTE,
            },
        ) as quote_fn:
            out = ensure_session_open_l2(cycle_day="2026-11-09", prefer="auto")
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("phase"), PHASE_PROVISIONAL)
        quote_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
