"""盘中补齐会话日 open L2。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestSessionOpenMaterialize(unittest.TestCase):
    def test_request_payload(self) -> None:
        from core.research.materialize.session_open import (
            session_open_materialize_request,
        )

        req = session_open_materialize_request("2026-11-09")
        self.assertTrue(req.get("do_session_open_l2"))
        self.assertEqual(req.get("clocks"), ["open"])
        self.assertEqual(req.get("cycle_day"), "2026-11-09")
        self.assertFalse(req.get("do_l1"))

    def test_materialize_persists_cube(self) -> None:
        from core.research.materialize.session_open import materialize_session_open_l2

        fake_cube = MagicMock()
        fake_cube.codes = ["a", "b"]
        fake_cube.names = ["x", "y"]
        fake_cube.universe_hash = "uh"
        fake_cube.slim = {}
        items = [
            {"stock_code": "a", "sub_scores": {"raw_aoo_GAP": 0.1}},
            {"stock_code": "b", "sub_scores": {"raw_aoo_GAP": 0.2}},
        ]
        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-11-09"
        ), patch(
            "core.research.materialize.orchestrate.resolve_materialize_codes",
            return_value=["a", "b"],
        ), patch(
            "core.paper.rebalance.open_pit_score.build_open_pit_feature_items",
            return_value=(
                items,
                [],
                {
                    "ok": True,
                    "pool_items": items,
                    "n_open": 2,
                    "n_items": 2,
                    "n_universe": 2,
                },
            ),
        ), patch(
            "core.research.batch_score.features_by_code_from_items",
            return_value={"a": {"raw_aoo_GAP": 0.1}, "b": {"raw_aoo_GAP": 0.2}},
        ), patch(
            "core.research.day_cube.build_day_cube", return_value=fake_cube
        ) as build, patch(
            "core.research.day_cube.save_day_cube", return_value=True
        ) as save, patch(
            "core.research.day_cube.remember"
        ) as rem, patch(
            "core.research.pit_session.prev_eod_as_of", return_value="2026-11-06"
        ):
            out = materialize_session_open_l2(cycle_day="2026-11-09")
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("persisted"))
        self.assertEqual(out.get("phase"), "provisional")
        self.assertEqual(out.get("open_source"), "quote")
        self.assertEqual(getattr(fake_cube, "slim", {}).get("phase"), "provisional")
        self.assertEqual(getattr(fake_cube, "slim", {}).get("builder"), "session_open_quote")
        build.assert_called_once()
        save.assert_called_once_with(fake_cube)
        rem.assert_called_once_with(fake_cube)

    def test_orchestrate_session_flag(self) -> None:
        from core.research.materialize.orchestrate import run_materialize

        with patch(
            "core.research.pit_session.ensure_session_open_l2",
            return_value={
                "ok": True,
                "success": True,
                "cycle_day": "2026-11-09",
                "n_universe": 2,
                "elapsed_s": 0.1,
                "phase": "provisional",
                "open_source": "quote",
            },
        ) as fn:
            out = run_materialize(do_session_open_l2=True, cycle_day="2026-11-09")
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("do_session_open_l2"))
        fn.assert_called_once()

    def test_score_pool_request_includes_session_open(self) -> None:
        from core.paper.rebalance.watching_matrix import _score_pool_from_lake
        from core.research.materialize.consumer import MaterializeRequired

        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-11-09"
        ), patch(
            "core.research.day_cube.load_day_cube", return_value=None
        ), patch(
            "core.research.materialize.coverage.read_cube_codes",
            return_value=["a", "b"],
        ):
            with self.assertRaises(MaterializeRequired) as ctx:
                _score_pool_from_lake(
                    ["a", "b"],
                    use_minute_tau=False,
                )
        payload = ctx.exception.to_dict()
        self.assertEqual(payload.get("error_code"), "materialize_required")
        self.assertTrue((payload.get("request") or {}).get("do_session_open_l2"))


if __name__ == "__main__":
    unittest.main()
