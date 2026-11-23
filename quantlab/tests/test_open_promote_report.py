"""临时 open 立方晋升终态后的对照摘要。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from core.research.open_promote_report import (
    apply_score_delta,
    compare_open_matrices,
    finish_open_promote,
    format_open_promote_line,
    hold_provisional_opens,
    prune_old_open_promotes,
    score_delta,
)


def _cube(codes, names, rows):
    return (
        list(codes),
        list(names),
        np.asarray(rows, dtype=np.float64),
    )


class TestOpenPromoteReport(unittest.TestCase):
    def test_format_small_and_membership(self) -> None:
        line = format_open_promote_line(
            {
                "open_median_bp": 0.4,
                "open_p95_bp": 0.5,
                "hist_median": 0.0,
                "only_morning": 0,
                "only_final": 0,
                "y_mae_pct": 0.01,
                "rank_corr": 0.994,
                "threshold_cross": 2,
            }
        )
        self.assertIn("今开列中位差 0.4bp", line)
        self.assertNotIn("95%", line)
        self.assertIn("历史列中位差 0", line)
        self.assertIn("股票集合相同", line)
        self.assertIn("ŷ 平均差 0.01%", line)
        self.assertIn("名次相关 0.99", line)
        self.assertIn("跨门槛 2", line)

    def test_format_open_tail_and_membership_drift(self) -> None:
        line = format_open_promote_line(
            {
                "open_median_bp": 3,
                "open_p95_bp": 12,
                "hist_median": 0.0,
                "only_morning": 4,
                "only_final": 1,
            }
        )
        self.assertIn("今开列中位差 3bp（95% 12bp）", line)
        self.assertIn("只在早盘 4", line)
        self.assertIn("只在终态 1", line)
        self.assertNotIn("ŷ", line)

    def test_compare_gap_bp_and_hist(self) -> None:
        names = ["gap_pct", "raw_aoo_YRANGE_PCT", "z_oo_size"]
        before = _cube(
            ["A", "B", "C"],
            names,
            [[1.0, 0.2, 1.0], [1.0, 0.2, 0.0], [1.0, 0.2, -1.0]],
        )
        after = _cube(
            ["A", "B"],
            names,
            [[1.004, 0.2, 9.0], [1.0, 0.2, 8.0]],
        )
        stats = compare_open_matrices(*before, *after)
        self.assertEqual(stats["only_morning"], 1)
        self.assertEqual(stats["only_final"], 0)
        # gap_pct 差 0.004 百分点 = 0.4bp，中位取两只里的 0 和 0.4
        self.assertAlmostEqual(stats["open_median_bp"], 0.2, places=3)
        self.assertAlmostEqual(stats["hist_median"], 0.0, places=8)

    def test_score_delta_fraction_fuse(self) -> None:
        morning = {
            "A": {"y_oo_fuse": 0.002, "y_oo_rank": 1},
            "B": {"y_oo_fuse": -0.001, "y_oo_rank": 2},
            "C": {"y_oo_fuse": 0.003, "y_oo_rank": 3},
        }
        final = [
            {"stock_code": "A", "y_oo_fuse": 0.0022, "y_oo_rank": 1},
            {"stock_code": "B", "y_oo_fuse": 0.002, "y_oo_rank": 3},
            {"stock_code": "C", "y_oo_fuse": 0.003, "y_oo_rank": 2},
        ]
        delta = score_delta(morning, final, fuse_enter=0.0)
        self.assertAlmostEqual(delta["y_mae_pct"], 0.11, places=2)
        self.assertEqual(delta["threshold_cross"], 1)
        self.assertGreater(delta["rank_corr"], 0.4)

    def test_hold_then_finish_writes_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cube_root = os.path.join(tmp, "cubes")
            run_root = os.path.join(tmp, "run")
            uh = "abc123"
            day = "2026-11-23"
            src = os.path.join(cube_root, "v1", uh, "open")
            os.makedirs(src)
            codes = np.asarray(["A", "B"], dtype="U16")
            names = np.asarray(["gap_pct", "raw_aoo_YRANGE_PCT"], dtype="U64")
            before = np.asarray([[1.0, 0.2], [0.5, 0.2]], dtype=np.float64)
            np.savez(os.path.join(src, f"{day}.npz"), X_raw=before, codes=codes, names=names)
            meta = {
                "schema": 1,
                "phase": "provisional",
                "builder": "session_open_quote",
                "open_source": "quote",
            }
            with open(os.path.join(src, f"{day}.meta.json"), "w", encoding="utf-8") as fh:
                json.dump(meta, fh)

            def _root() -> str:
                return cube_root

            def _promote() -> str:
                return run_root

            with patch("core.research.day_cube._root", _root), patch(
                "core.research.open_promote_report._promote_root", _promote
            ), patch(
                "core.research.open_promote_report._session_day", lambda: day
            ):
                self.assertEqual(hold_provisional_opens(day), 1)
                held = os.path.join(run_root, day, uh, "before.npz")
                self.assertTrue(os.path.isfile(held))
                after = np.asarray([[1.01, 0.2], [0.5, 0.2]], dtype=np.float64)
                np.savez(os.path.join(src, f"{day}.npz"), X_raw=after, codes=codes, names=names)
                meta["phase"] = "final"
                meta["builder"] = "lake_open"
                meta["open_source"] = "daily"
                with open(os.path.join(src, f"{day}.meta.json"), "w", encoding="utf-8") as fh:
                    json.dump(meta, fh)
                summary = finish_open_promote(day)
            self.assertIsNotNone(summary)
            self.assertIn("今开列中位差", summary["line"])
            self.assertIn("历史列中位差 0", summary["line"])
            morning = {
                "A": {"y_oo_fuse": 0.01, "y_oo_rank": 1},
                "B": {"y_oo_fuse": 0.02, "y_oo_rank": 2},
            }
            os.makedirs(os.path.join(run_root, day), exist_ok=True)
            with open(os.path.join(run_root, day, "morning_ranks.json"), "w", encoding="utf-8") as fh:
                json.dump({"as_of": day, "by_code": morning}, fh)
            with patch("core.research.open_promote_report._promote_root", _promote), patch(
                "core.research.feature_registry.universe_hash", lambda codes: uh
            ):
                updated = apply_score_delta(
                    day,
                    ["A", "B"],
                    [
                        {"stock_code": "A", "y_oo_fuse": 0.011, "y_oo_rank": 2},
                        {"stock_code": "B", "y_oo_fuse": 0.02, "y_oo_rank": 1},
                    ],
                    fuse_enter=0.0,
                )
            self.assertIn("ŷ 平均差", updated["line"])
            self.assertIn("跨门槛", updated["line"])
            # 第二次 hold：已有 before 则短路，不再依赖立方目录
            with patch(
                "core.research.open_promote_report._promote_root", _promote
            ), patch(
                "core.research.open_promote_report._session_day", lambda: day
            ):
                self.assertEqual(hold_provisional_opens(day), 1)

    def test_prune_old_open_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "2026-01-01", "uh")
            keep = os.path.join(tmp, "2026-11-23", "uh")
            os.makedirs(old)
            os.makedirs(keep)
            open(os.path.join(old, "before.npz"), "wb").close()
            open(os.path.join(keep, "before.npz"), "wb").close()
            with patch(
                "core.research.open_promote_report._promote_root", lambda: tmp
            ):
                n = prune_old_open_promotes(keep_days=7, keep_day="2026-11-23")
            self.assertEqual(n, 1)
            self.assertFalse(os.path.isdir(os.path.join(tmp, "2026-01-01")))
            self.assertTrue(os.path.isdir(keep))


if __name__ == "__main__":
    unittest.main()
