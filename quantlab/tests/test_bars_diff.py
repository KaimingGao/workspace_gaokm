"""日线写入前后：价变、量纲、只补尾。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from quant.research.bars_diff import diff_daily_series, read_bars_refresh_diff, write_bars_refresh_diff


def _bar(close: float, volume: float):
    return (close, volume)


class TestBarsDiff(unittest.TestCase):
    def test_price_shift_volume_unit_and_tail(self) -> None:
        before = {
            "600519": {"2026-06-01": _bar(100.0, 100.0), "2026-10-21": _bar(110.0, 100.0)},
            "000001": {"2026-10-21": _bar(10.0, 100.0)},
            "300750": {"2026-10-21": _bar(20.0, 200.0)},
        }
        after = {
            "600519": {"2026-06-01": _bar(90.0, 10000.0), "2026-10-21": _bar(110.0, 10000.0)},
            "000001": {"2026-10-21": _bar(10.0, 100.0), "2026-10-22": _bar(10.2, 120.0)},
            "300750": {"2026-10-21": _bar(20.0, 200.0)},
        }
        diff = diff_daily_series(before, after, mode="full", universe_count=3)
        self.assertEqual(diff["price_count"], 1)
        self.assertEqual(diff["volume_count"], 1)
        self.assertEqual(diff["tail_count"], 1)
        self.assertEqual(diff["unchanged_count"], 1)
        by_code = {row["code"]: row for row in diff["rows"]}
        self.assertIn("price", by_code["600519"]["tags"])
        self.assertIn("volume", by_code["600519"]["tags"])
        self.assertAlmostEqual(by_code["600519"]["max_close_rel"], 0.1, places=4)
        self.assertAlmostEqual(by_code["600519"]["vol_ratio"], 100.0, places=2)
        self.assertEqual(by_code["000001"]["tags"], ["tail"])
        self.assertNotIn("300750", by_code)
        self.assertIn("价变 1", diff["headline"])

    def test_roundtrip_file(self) -> None:
        diff = diff_daily_series({}, {}, mode="topup", universe_count=0)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bars_refresh_diff.json")
            with patch("quant.research.bars_diff.write_bars_refresh_diff", wraps=write_bars_refresh_diff):
                with patch("core.paths.BARS_REFRESH_DIFF_PATH", path):
                    write_bars_refresh_diff(diff)
                    loaded = read_bars_refresh_diff()
        self.assertEqual(loaded["mode"], "topup")
        self.assertEqual(loaded["compared"], 0)
        self.assertTrue(loaded["headline"])
