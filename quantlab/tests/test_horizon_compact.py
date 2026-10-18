"""ŷ_τ* enrich → float64 紧凑矩阵。"""

from __future__ import annotations

import unittest

import numpy as np


class TestHorizonCompact(unittest.TestCase):
    def test_fold_clears_panels_and_keeps_shape(self):
        from core.research.horizon_compact import (
            compact_rows,
            fold_horizon_enriched,
            release_stock_bar_payloads,
        )

        enriched = [
            {
                "code": "A",
                "xs": [
                    {"z_τh_gap_pct": 1.0, "raw_ath_RET_LAST_30": 0.2, "noise": 9},
                    {"z_τh_gap_pct": 2.0, "raw_ath_RET_LAST_30": None},
                ],
                "ys": [0.1, -0.2],
                "dates": ["2024-01-02", "2024-01-03"],
                "metas": [{"theme": "a"}, {"theme": "b"}],
            },
            {
                "code": "B",
                "xs": [{"z_τh_gap_pct": 0.5, "raw_ath_RET_LAST_30": 0.1}],
                "ys": [0.3],
                "dates": ["2024-01-02"],
                "metas": [{"theme": "c"}],
            },
        ]
        names = ("z_τh_gap_pct", "raw_ath_RET_LAST_30")
        compact = fold_horizon_enriched(enriched, names, clear_panels=True)
        self.assertEqual(len(enriched), 0)
        self.assertEqual(compact["stock_count"], 2)
        self.assertEqual(len(compact["ys"]), 3)
        X = compact["X"]
        self.assertEqual(X.shape, (3, 2))
        self.assertTrue(np.isfinite(X[0, 0]))
        self.assertFalse(np.isfinite(X[1, 1]))

        rows = compact_rows(compact, idxs=[0, 2], names=names)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].get("z_τh_gap_pct"), 1.0)
        self.assertNotIn("noise", rows[0])

        bars = [{"code": "A", "bars": [{"date": "2024-01-02"}], "minute_bars": [1]}]
        release_stock_bar_payloads(bars)
        self.assertIsNone(bars[0].get("bars"))
        self.assertIsNone(bars[0].get("minute_bars"))
        self.assertEqual(bars[0].get("code"), "A")


if __name__ == "__main__":
    unittest.main()
