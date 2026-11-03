"""09:30 冷启动批量开盘分：整池 z，名单内才写 ŷ。"""

from __future__ import annotations

import inspect
import math
import unittest
from unittest.mock import patch

from core.backtest.replay_batch_open import (
    _RankModel,
    _overlay_open_tau,
    _rank_map,
    clear_open_slice,
    cross_section_ridge,
    prepare_replay_features,
    score_open_day_from_slice,
)
from core.backtest.replay_scoring import _score_open_day
from core.research.materialize.consumer import MaterializeRequired


class ReplayBatchOpenTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_open_slice()

    def test_ridge_uses_full_pool_z(self) -> None:
        rows = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
        sd = math.sqrt(2.0 / 3.0)
        got = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.0)
        self.assertEqual(got, [round(-1.0 / sd, 6), 0.0, round(1.0 / sd, 6)])
        # 少一个同伴，z 变了。分档不能拿来缩小截面。
        shrunk = cross_section_ridge(rows[:2], ["x"], {"x": 1.0}, 0.0)
        self.assertNotEqual(shrunk[0], got[0])

    def test_all_missing_row_follows_head_rule(self) -> None:
        rows = [{"x": 1.0}, {}]
        imputed = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.5, impute_all_missing=True)
        skipped = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.5, impute_all_missing=False)
        self.assertIsNotNone(imputed[1])
        self.assertIsNone(skipped[1])

    def test_score_without_slice_is_unset(self) -> None:
        clear_open_slice()
        self.assertIsNone(
            score_open_day_from_slice(
                "2024-01-03",
                dates=["2024-01-02", "2024-01-03"],
                day_i=1,
            )
        )

    def test_open_overlay_rounds_gap_and_writes_sector(self) -> None:
        row = {"z_τc_gap_pct": 0.14245014245015675}
        _overlay_open_tau(
            row,
            quote={"open": 10.01425, "prev_close": 10.0, "date": "2026-10-29"},
            pool={
                "sector_gap_breadth": 0.015,
                "pool_gaps": [0.1425],
                "ref_by_code": {"600000": -0.0429},
            },
            code="600000",
            day="2026-10-29",
            trigger=2.0,
        )
        self.assertEqual(row["gap_pct"], 0.1425)
        self.assertEqual(row["sector_gap_breadth"], 0.015)
        self.assertEqual(row["gap_vs_sector"], 0.1854)
        self.assertEqual(row["theme_day"], 0.0)

    def test_cancel_does_not_prefetch(self) -> None:
        with patch("core.research.lake_replay.prefetch_l1_coverage") as prefetch:
            prepare_replay_features(
                ["600000"],
                ["2026-10-29"],
                clock="09:30",
                cancel_cb=lambda: True,
            )
        prefetch.assert_not_called()
        self.assertIsNone(score_open_day_from_slice("2026-10-29", dates=["2026-10-29"]))

    def test_broken_rank_is_not_silently_empty(self) -> None:
        self.assertEqual(
            _rank_map("2026-10-29", ["a"], [{}], None, None, getter=lambda _r, _k: None),
            {},
        )
        broken = _RankModel({}, [], None, broken=True)
        self.assertIsNone(
            _rank_map("2026-10-29", ["a"], [{}], broken, None, getter=lambda _r, _k: None)
        )

    def test_foreign_slice_is_not_used(self) -> None:
        from core.backtest import replay_batch_open as mod

        mod._HOLDER = mod._OpenSlice(["600000"], ["2026-10-29"], {}, {})
        self.assertIsNone(
            score_open_day_from_slice(
                "2026-10-29",
                dates=["2026-10-28", "2026-10-29"],
                date_maps={"600000": {}},
            )
        )
        self.assertIsNone(
            score_open_day_from_slice(
                "2026-10-29",
                dates=["2026-10-29"],
                date_maps={"000001": {}},
            )
        )

    def test_materialize_required_is_not_swallowed(self) -> None:
        def _boom(*_a: object, **_k: object) -> None:
            raise MaterializeRequired({"error": "need l2"})

        with patch("core.backtest.replay_batch_open.score_open_day_from_slice", _boom):
            with self.assertRaises(MaterializeRequired):
                _score_open_day(
                    stock_bars={},
                    dates=["2024-01-02", "2024-01-03"],
                    date_maps={},
                    day_i=1,
                    max_window=30,
                    horizon_days=1,
                    cfg=None,
                )

    def test_replay_releases_slice_and_keeps_signature(self) -> None:
        from core.backtest import replay_batch_open as mod
        from core.backtest.paper_replay import backtest_paper_replay

        names = inspect.signature(backtest_paper_replay).parameters
        self.assertIn("stock_bars", names)
        self.assertIn("tradable_codes", names)
        mod._HOLDER = mod._OpenSlice(["600000"], ["2026-10-29"], {}, {})
        out = backtest_paper_replay({})
        self.assertFalse(out.get("success"))
        self.assertIsNone(mod._HOLDER)


if __name__ == "__main__":
    unittest.main()
