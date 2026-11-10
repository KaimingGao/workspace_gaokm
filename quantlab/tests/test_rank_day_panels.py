"""排序头日截面统一壳。"""

from __future__ import annotations

import unittest

import numpy as np


class TestRankDayPanels(unittest.TestCase):
    def test_tau_compact_to_day_panels_groups_by_date(self):
        from core.research.rank_day_panels import tau_compact_to_day_panels

        # group_day_matrices 下限 max(4, min_names)，每日至少 4 只
        codes = ["A", "B", "C", "D"]
        rows = 8
        x = np.arange(rows * 2, dtype=np.float64).reshape(rows, 2)
        dates = ["2024-01-01"] * 4 + ["2024-01-02"] * 4
        metas = [{"stock_code": c} for c in codes + codes]
        days = tau_compact_to_day_panels(
            x,
            ["a", "b"],
            list(range(rows)),
            dates,
            metas,
            min_names=4,
        )
        self.assertEqual([d["date"] for d in days], ["2024-01-01", "2024-01-02"])
        self.assertEqual(days[0]["codes"], codes)
        self.assertEqual(days[0].get("clock"), "open")
        self.assertEqual(list(days[0]["X"].shape), [4, 0])  # a/b 不在 TAU_Z 入模列

    def test_tau_compact_to_day_panels_groups_by_date_clock(self):
        from core.research.rank_day_panels import tau_compact_to_day_panels

        codes = ["A", "B", "C", "D"]
        # 同日两钟各 4 只
        dates = ["2024-01-01"] * 8
        metas = (
            [{"stock_code": c, "tau": "09:35"} for c in codes]
            + [{"stock_code": c, "tau": "10:30"} for c in codes]
        )
        x = np.arange(8 * 2, dtype=np.float64).reshape(8, 2)
        days = tau_compact_to_day_panels(
            x,
            ["a", "b"],
            list(range(8)),
            dates,
            metas,
            min_names=4,
            group_by_clock=True,
        )
        self.assertEqual(len(days), 2)
        self.assertEqual(
            [(d["date"], d["clock"]) for d in days],
            [("2024-01-01", "09:35"), ("2024-01-01", "10:30")],
        )
        self.assertEqual(days[0]["codes"], codes)

    def test_build_rank_day_panels_rejects_unknown_family(self):
        from core.research.rank_day_panels import build_rank_day_panels

        with self.assertRaises(ValueError):
            build_rank_day_panels([], family="nope")

    def test_build_rank_day_panels_dispatches_oo_rank(self):
        from unittest.mock import patch

        from core.research.rank_day_panels import build_rank_day_panels

        with patch(
            "core.research.oo_rank_panel.build_oo_rank_day_panels",
            return_value=[{"date": "d"}],
        ) as mock_oo:
            out = build_rank_day_panels(
                [{"code": "A", "bars": [{"date": "2024-01-01"}]}],
                family="oo_rank",
                horizon_days=1,
            )
        self.assertEqual(out, [{"date": "d"}])
        mock_oo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
