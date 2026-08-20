"""当日首次分组强制日线标记。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestClusterBarsDaily(unittest.TestCase):
    def test_needs_and_mark_session(self):
        from quant.research import cluster_bars_daily as mod

        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "cluster_bars_forced_session.json")
            with patch("core.paths.CLUSTER_BARS_FORCED_SESSION_PATH", marker):
                self.assertTrue(mod.needs_force_latest_bars(session_date="2026-08-20"))
                mod.mark_force_latest_bars_done(
                    session_date="2026-08-20", remote_count=3, total=10
                )
                self.assertFalse(mod.needs_force_latest_bars(session_date="2026-08-20"))
                self.assertTrue(mod.needs_force_latest_bars(session_date="2026-08-21"))
                self.assertTrue(os.path.isfile(marker))


if __name__ == "__main__":
    unittest.main()
