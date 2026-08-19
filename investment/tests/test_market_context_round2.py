"""概念缓存 / macro as-of / minute prefetch 补强测试。"""

from __future__ import annotations

import os
import tempfile
import unittest

from core.concept_graph_store import (
    load_concept_graph_cache,
    merge_concept_into_index,
    save_concept_graph_cache,
)
from core.research.macro_asof import macro_view_asof, pct_change_from_bars
from core.signal.minute_prefetch import prefetch_minute_bars


class TestMarketContextRound2(unittest.TestCase):
    def test_concept_graph_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["INVESTMENT_STORE_DIR"] = tmp
            try:
                from core import paths

                paths.STORE_DIR = tmp
                idx = merge_concept_into_index({}, "机器人", ["600001", "600002"])
                save_concept_graph_cache(
                    concepts={"机器人": ["600001", "600002"]},
                    code_index=idx,
                )
                loaded, meta = load_concept_graph_cache(max_age_hours=1.0)
                self.assertTrue(meta.get("cache_hit"))
                self.assertIn("600001", loaded.get("code_index") or {})
            finally:
                os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_macro_view_asof(self):
        macro = {
            "liquidity_stress_score": 0,
            "series": {
                "sox": {
                    "recent_bars": [
                        {"date": "2026-08-17", "close": 100.0},
                        {"date": "2026-08-18", "close": 98.0},
                        {"date": "2026-08-19", "close": 96.0},
                    ]
                },
                "ndx": {
                    "recent_bars": [
                        {"date": "2026-08-17", "close": 200.0},
                        {"date": "2026-08-18", "close": 199.0},
                        {"date": "2026-08-19", "close": 197.0},
                    ]
                },
            },
        }
        view = macro_view_asof(macro, "2026-08-18")
        self.assertEqual(view.get("as_of"), "2026-08-18")
        self.assertIsNotNone(view.get("overseas_tech_1d_pct"))
        self.assertTrue(view.get("synthetic"))

    def test_pct_change_from_bars(self):
        bars = [
            {"date": "2026-08-18", "close": 100.0},
            {"date": "2026-08-19", "close": 95.0},
        ]
        self.assertAlmostEqual(pct_change_from_bars(bars, days=1), -5.0)

    def test_minute_prefetch_empty(self):
        out = prefetch_minute_bars([], fetch_if_missing=False)
        self.assertTrue(out.get("ok"))


if __name__ == "__main__":
    unittest.main()
