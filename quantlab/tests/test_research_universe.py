"""日线研究宇宙：与观察池 / 分钟暖仓分离；写入默认可交易严筛。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import mock_open, patch


class TestResearchUniverse(unittest.TestCase):
    def test_save_load_dedupe_cap(self):
        from core.research_universe import (
            load_research_universe,
            save_research_universe,
        )

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "research_universe.json")
            codes = [f"{i:06d}" for i in range(10)] + ["000001", "000002"]
            out = save_research_universe(
                {"codes": codes, "max_size": 8}, path=path, apply_screen=False
            )
            self.assertTrue(out.get("ok"))
            uni = load_research_universe(path=path)
            self.assertEqual(uni["count"], 8)
            self.assertEqual(len(uni["codes"]), 8)
            self.assertEqual(uni["codes"][0], "000000")
            self.assertEqual(len(set(uni["codes"])), 8)

    def test_resolve_prefers_research_then_watching(self):
        from core.research_universe import resolve_research_codes

        with patch(
            "core.research_universe.load_research_universe",
            return_value={
                "codes": ["600000", "600001", "600002"],
                "max_size": 2000,
            },
        ):
            r = resolve_research_codes(limit=2)
        self.assertEqual(r["source"], "research_universe")
        self.assertEqual(r["codes"], ["600000", "600001"])
        self.assertFalse(r["minute_warmup"])

        with patch(
            "core.research_universe.load_research_universe",
            return_value={"codes": [], "max_size": 2000},
        ):
            r2 = resolve_research_codes(
                watching_codes=["000001", "000001", "000002"],
                limit=500,
            )
        self.assertEqual(r2["source"], "watching_fallback")
        self.assertEqual(r2["codes"], ["000001", "000002"])
        from core.watching.store import WATCHING_MAX_SIZE

        self.assertLessEqual(r2["cap"], WATCHING_MAX_SIZE)

    def test_minute_warmup_pool_watching_skips_validation(self):
        from core.schedule_jobs import _resolve_warmup_codes

        with patch(
            "core.validation_universe.resolve_validation_codes"
        ) as m_vu:
            m_vu.return_value = {"codes": ["111111", "222222"]}
            with patch("os.path.isfile", return_value=True), patch(
                "builtins.open", mock_open(read_data="{}")
            ), patch(
                "core.platform.schedule_jobs.json.load",
                return_value={"watchlist": ["000001", "000002"]},
            ):
                out = _resolve_warmup_codes(
                    None,
                    cap=10,
                    pool="watching",
                    prefer_validation_universe=False,
                )
        self.assertEqual(out, ["000001", "000002"])
        m_vu.assert_not_called()

    def test_board_overlap_subset_flag(self):
        from core.research_universe import (
            build_research_universe_board,
            save_research_universe,
        )

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ru.json")
            save_research_universe(
                {"codes": ["600000", "000001"]}, path=path, apply_screen=False
            )
            with patch(
                "core.research_universe.load_research_universe",
                side_effect=lambda **_k: {
                    "codes": ["600000", "000001"],
                    "max_size": 2000,
                    "note": "t",
                    "count": 2,
                },
            ), patch(
                "core.research_universe._watching_codes",
                return_value=["000001", "000002"],
            ):
                board = build_research_universe_board(include_coverage=False)
            self.assertTrue(board.get("ok"))
            self.assertEqual(board["overlap"]["count"], 1)
            self.assertEqual(board["overlap"]["only_research"], 1)
            self.assertEqual(board["overlap"]["only_watching"], 1)
            self.assertFalse(board["watching_subset_ok"])
            self.assertFalse(board["resolved"]["minute_warmup"])

    def test_rebuild_and_reconcile_route_chains(self):
        from web.routers.quant_research import (
            quant_research_universe_rebuild_and_reconcile,
        )

        with patch(
            "core.universe.tradable_screen.rebuild_tradable_research_universe",
            return_value={
                "ok": True,
                "source": "tradable_rebuild",
                "universe": {"codes": ["000001"], "count": 1},
            },
        ) as m_rebuild, patch(
            "core.watching.repository.reconcile_watching_to_research",
            return_value={
                "ok": True,
                "before": 2,
                "after": 1,
                "dropped_count": 1,
                "dropped": ["000002"],
            },
        ) as m_rec, patch(
            "core.research_universe.build_research_universe_board",
            return_value={
                "ok": True,
                "watching_subset_ok": True,
                "universe": {"count": 1},
            },
        ):
            out = quant_research_universe_rebuild_and_reconcile()
        m_rebuild.assert_called_once()
        m_rec.assert_called_once()
        self.assertTrue(out.get("ok"))
        self.assertEqual(out["reconcile"]["dropped_count"], 1)
        self.assertTrue(out.get("watching_subset_ok"))


if __name__ == "__main__":
    unittest.main()
