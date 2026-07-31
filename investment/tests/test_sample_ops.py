"""样本运营：TTM cycle 配对 · history 落盘 · 覆盖报告。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta


class TestTtmCyclePairing(unittest.TestCase):
    def test_cycle_id_preferred(self):
        from core.north_star import (
            TTM_EVENT_BACKTEST,
            TTM_EVENT_IDEA,
            TTM_EVENT_PAPER,
            compute_ttm_metrics,
        )

        t0 = datetime(2026, 1, 1, 10, 0, 0)
        events = [
            {
                "ts": t0.isoformat(),
                "event": TTM_EVENT_IDEA,
                "meta": {"cycle_id": "c1"},
            },
            {
                "ts": (t0 + timedelta(hours=1)).isoformat(),
                "event": TTM_EVENT_IDEA,
                "meta": {"cycle_id": "c2"},
            },
            # 时间上最近的是 c2 的 bt，但 c1 应配到 10h 后的 bt
            {
                "ts": (t0 + timedelta(hours=2)).isoformat(),
                "event": TTM_EVENT_BACKTEST,
                "meta": {"cycle_id": "c2"},
            },
            {
                "ts": (t0 + timedelta(hours=10)).isoformat(),
                "event": TTM_EVENT_BACKTEST,
                "meta": {"cycle_id": "c1"},
            },
            {
                "ts": (t0 + timedelta(hours=12)).isoformat(),
                "event": TTM_EVENT_PAPER,
                "meta": {"cycle_id": "c1"},
            },
        ]
        out = compute_ttm_metrics(events)
        self.assertTrue(out["ok"])
        self.assertEqual(out["pair_counts"]["idea_to_backtest"], 2)
        self.assertEqual(out["median_idea_to_paper_hours"], 12.0)


class TestSampleOps(unittest.TestCase):
    def test_seed_ttm_and_status(self):
        from core.sample_ops import sample_status, seed_ttm_cycles

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ttm.jsonl")
            seeded = seed_ttm_cycles(cycles=2, path=path, write=True)
            self.assertTrue(seeded["ok"])
            self.assertEqual(seeded["ttm"]["status"], "ok")
            self.assertGreaterEqual(
                seeded["ttm"]["pair_counts"]["idea_to_paper"], 2
            )

    def test_persist_and_ladder(self):
        from core.fundamentals_pit import load_fundamentals_panel, select_point_as_of
        from core.sample_ops import (
            persist_fundamentals_history,
            seed_fundamentals_history_ladder,
        )
        from core.store import snapshot_cache_path

        with tempfile.TemporaryDirectory() as td:
            code = "T001"
            path = snapshot_cache_path("fundamentals", code, td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "version": 1,
                "kind": "fundamentals",
                "code": code,
                "data_source": "test",
                "fetched_at": "2026-07-01T12:00:00",
                "non_pit": True,
                "data": {
                    "success": True,
                    "metrics": {"pe": 20.0, "pb": 2.0, "roe": 0.15},
                },
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)

            pers = persist_fundamentals_history(
                codes=[code], store_dir=td, write=True
            )
            self.assertIn(code, pers["updated"])
            panel = load_fundamentals_panel(code, store_dir=td)
            self.assertGreaterEqual(panel["history_count"], 1)

            ladder = seed_fundamentals_history_ladder(
                codes=[code], store_dir=td, quarters=2, write=True, day_step=90
            )
            self.assertEqual(ladder["seeded_count"], 1)
            panel2 = load_fundamentals_panel(code, store_dir=td)
            self.assertGreaterEqual(panel2["history_count"], 3)
            point, meta = select_point_as_of(
                panel2["history"], "2026-01-15"
            )
            self.assertTrue(meta["ok"])
            self.assertIsNotNone(point)

    def test_densify_snapshots(self):
        from core.sample_ops import densify_paper_snapshots

        paper = {"snapshots": [{"ts": "2026-07-29T15:00:00", "equity": 1_000_000}]}
        out = densify_paper_snapshots(paper, target_days=25)
        self.assertTrue(out["changed"])
        self.assertGreaterEqual(len(paper["snapshots"]), 25)


if __name__ == "__main__":
    unittest.main()
