"""观察池日线状态汇总。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestClusterBarsStatus(unittest.TestCase):
    def test_expected_latest_before_close(self):
        from datetime import datetime

        from quant.research.cluster_bars_status import expected_latest_daily_bar_date

        with patch("core.market.calendar.resolve_session_date", return_value="2026-08-25"), patch(
            "core.market.calendar.is_trading_day", return_value=True
        ), patch("core.market.calendar.prev_trading_day", return_value="2026-08-22"):
            dt = datetime(2026, 8, 25, 10, 0, 0)
            self.assertEqual(expected_latest_daily_bar_date(now=dt), "2026-08-22")

    def test_build_status_counts(self):
        from quant.research.cluster_bars_status import build_cluster_bars_status

        watch = ["600519", "000001"]
        bars_map = {
            "600519": [{"date": "2026-08-22", "close": 1.0}],
            "000001": [],
        }

        def _last_bar(code: str):
            bars = bars_map.get(code) or []
            if not bars:
                return None
            return str(bars[-1].get("date") or "")[:10] or None

        with patch("core.watching.store.read_watching", return_value={"watchlist": watch}), patch(
            "quant.research.cluster_bars_status._last_bar_date_for_code",
            side_effect=_last_bar,
        ), patch(
            "quant.research.cluster_bars_status.expected_latest_daily_bar_date",
            return_value="2026-08-22",
        ), patch(
            "quant.research.cluster_bars_daily.cluster_bars_session_date",
            return_value="2026-08-25",
        ), patch(
            "quant.research.cluster_bars_daily.needs_force_latest_bars",
            return_value=True,
        ), patch(
            "quant.research.cluster_bars_daily.read_force_latest_bars_marker",
            return_value={},
        ):
            st = build_cluster_bars_status(watching_limit=100)
        self.assertTrue(st["success"])
        self.assertEqual(st["universe_count"], 2)
        self.assertEqual(st["at_expected"], 1)
        self.assertEqual(st["missing"], 1)
        self.assertFalse(st["coverage_ok"])


if __name__ == "__main__":
    unittest.main()
