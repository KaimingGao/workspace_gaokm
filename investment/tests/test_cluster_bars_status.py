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

    def test_expected_latest_weekend_uses_session(self):
        from datetime import datetime

        from quant.research.cluster_bars_status import expected_latest_daily_bar_date

        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-09-04"
        ), patch("core.market.calendar.is_trading_day", return_value=True):
            dt = datetime(2026, 9, 6, 1, 30, 0)
            self.assertEqual(expected_latest_daily_bar_date(now=dt), "2026-09-04")

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
        self.assertIn("bars_backend", st)
        self.assertIn("coverage_pct", st)
        self.assertEqual(st["coverage_pct"], 50.0)

    def test_refresh_modes_delegate(self):
        from quant.research.cluster_bars_status import refresh_cluster_bars_only

        built = {
            "bars_refresh": {
                "requested": True,
                "force_latest": True,
                "full_window": False,
                "mode": "topup",
                "remote_count": 1,
                "total": 2,
                "cache_count": 1,
                "note": "增量补齐到最新",
            }
        }
        with patch("core.watching.store.read_watching", return_value={"watchlist": ["600519"]}), patch(
            "quant.research.watching_universe.merge_cluster_universe",
            return_value={"codes": ["600519"], "code_roles": {}},
        ), patch(
            "quant.research.cluster_panels.build_cluster_ols_panels",
            return_value=built,
        ) as build, patch(
            "quant.research.cluster_bars_daily.mark_force_latest_bars_done"
        ), patch(
            "quant.research.cluster_bars_status.build_cluster_bars_status",
            return_value={"success": True},
        ):
            top = refresh_cluster_bars_only(watching_limit=100, mode="topup")
            full = refresh_cluster_bars_only(watching_limit=100, mode="full")
        self.assertEqual(top["mode"], "topup")
        self.assertEqual(full["mode"], "full")
        self.assertTrue(build.call_args_list[0].kwargs.get("force_latest_bars"))
        self.assertFalse(build.call_args_list[0].kwargs.get("full_window_bars"))
        self.assertFalse(build.call_args_list[1].kwargs.get("force_latest_bars"))
        self.assertTrue(build.call_args_list[1].kwargs.get("full_window_bars"))

    def test_bars_refresh_job_total_matches_watchlist(self):
        import tempfile
        import time

        from core.job_progress import JobProgress
        from quant.services.quant_service import QuantService

        watch = [f"{i:06d}" for i in range(160)]
        fake = {
            "success": True,
            "universe_count": 160,
            "bars_refresh": {"total": 160, "remote_count": 0},
        }

        def _run(*, progress_cb=None, **_kwargs):
            if progress_cb:
                progress_cb("拉日线 160/160", 160, 160)
            return fake

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cluster_bars.json")
            slot = JobProgress(name="cluster-bars-refresh", persist_path=path)
            svc = QuantService()
            with patch("core.job_progress.cluster_bars_refresh_job", slot), patch(
                "core.watching.store.read_watching",
                return_value={"watchlist": watch},
            ), patch.object(svc, "run_cluster_bars_refresh", side_effect=_run):
                out = svc.start_cluster_bars_refresh_job(watching_limit=200)
                self.assertTrue(out.get("background"))
                self.assertEqual(out["job"]["total"], 160)
                for _ in range(50):
                    snap = slot.get()
                    if snap.get("status") in ("done", "failed"):
                        break
                    time.sleep(0.05)
                snap = slot.get()
                self.assertEqual(snap["status"], "done")
                self.assertEqual(snap["total"], 160)
                self.assertEqual(snap["current"], 160)
                self.assertIn("160/160", str(snap.get("message") or ""))


if __name__ == "__main__":
    unittest.main()
