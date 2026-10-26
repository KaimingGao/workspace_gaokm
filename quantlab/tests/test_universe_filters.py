"""宇宙过滤：剔 ST 默认不打 live quote。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.backtest.universe_filters import filter_universe_bars


class TestUniverseFiltersSt(unittest.TestCase):
    def test_exclude_st_uses_names_without_quote(self):
        bars = {
            "000001": [{"date": "2026-01-01", "close": 10, "volume": 1}],
            "600519": [{"date": "2026-01-01", "close": 10, "volume": 1}],
        }
        with patch("core.data.facade.get_quote") as gq:
            kept, dropped, meta = filter_universe_bars(
                bars,
                exclude_st=True,
                name_by_code={"000001": "平安银行", "600519": "ST茅台"},
                allow_live_names=False,
            )
            gq.assert_not_called()
        self.assertIn("000001", kept)
        self.assertNotIn("600519", kept)
        self.assertEqual(meta["dropped_count"], 1)
        self.assertFalse(meta.get("allow_live_names"))
        self.assertEqual(len(dropped), 1)

    def test_empty_saved_name_uses_local_code_map_for_st(self):
        watching = {
            "watchlist": ["000610", "000001"],
            "watchlist_names": ["", "平安银行"],
        }
        with patch(
            "core.t0.intraday._load_a_code_name_map",
            return_value={"000610": "*ST西旅", "000001": "平安银行"},
        ):
            from core.backtest.universe_filters import st_filter_name_map

            names = st_filter_name_map(watching)
        self.assertIn("ST", str(names.get("000610") or "").upper())
        kept, dropped, meta = filter_universe_bars(
            {
                "000610": [{"date": "2026-01-01", "close": 10, "volume": 1}],
                "000001": [{"date": "2026-01-01", "close": 10, "volume": 1}],
            },
            exclude_st=True,
            name_by_code=names,
            allow_live_names=False,
        )
        self.assertNotIn("000610", kept)
        self.assertIn("000001", kept)
        self.assertEqual(meta["dropped_count"], 1)
        self.assertEqual(len(dropped), 1)

    def test_missing_name_kept_when_offline(self):
        bars = {"000002": [{"date": "2026-01-01", "close": 10, "volume": 1}]}
        with patch("core.data.facade.get_quote") as gq:
            kept, dropped, _meta = filter_universe_bars(
                bars, exclude_st=True, name_by_code={}, allow_live_names=False
            )
            gq.assert_not_called()
        self.assertIn("000002", kept)
        self.assertEqual(dropped, [])

if __name__ == "__main__":
    unittest.main()
