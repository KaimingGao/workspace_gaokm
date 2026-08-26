"""观察池 5m 分钟线状态汇总。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestClusterMinuteStatus(unittest.TestCase):
    def test_build_status_counts(self):
        from quant.research.cluster_minute_status import build_cluster_minute_status

        watch = ["600519", "000001", "300750"]

        def _snap(code: str, *, period: str = "5"):
            if code == "600519":
                return {"span_days": 95, "bar_count": 1000, "fetched_at": "2026-08-26T10:00:00"}
            if code == "000001":
                return {"span_days": 25, "bar_count": 200, "fetched_at": "2026-08-26T10:00:00"}
            return None

        with patch("core.watching.store.read_watching", return_value={"watchlist": watch}), patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ):
            st = build_cluster_minute_status(watching_limit=100, min_span_days=40)
        self.assertTrue(st["success"])
        self.assertEqual(st["universe_count"], 3)
        self.assertEqual(st["cached_ok"], 1)
        self.assertEqual(st["short"], 1)
        self.assertEqual(st["missing"], 1)
        self.assertFalse(st["coverage_ok"])
        self.assertEqual(st["coverage_pct"], round(100 / 3, 1))
        self.assertEqual(st["minute_span_days_med"], 95)

    def test_minute_cache_ready(self):
        from quant.research.cluster_minute_status import minute_cache_ready

        with patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 50,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ):
            ok, _, reason = minute_cache_ready("600519")
        self.assertTrue(ok)
        self.assertEqual(reason, "ready")

        with patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 20,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ):
            ok, _, reason = minute_cache_ready("000001")
        self.assertFalse(ok)
        self.assertEqual(reason, "short")

    def test_warmup_skips_ready(self):
        from core.schedule_jobs import _minute_warmup_core

        with patch(
            "core.schedule_jobs._resolve_warmup_codes",
            return_value=["600519", "000001"],
        ), patch(
            "quant.research.cluster_minute_status.minute_cache_ready",
            side_effect=lambda code, **kw: (code == "600519", {}, "ready"),
        ), patch(
            "core.data.policy.minute_warmup_skip_if_ready", return_value=True
        ), patch(
            "core.ports.market.fetch_minute_bars", return_value=([], {})
        ) as fetch:
            out = _minute_warmup_core(codes=["600519", "000001"], cap=2)
        self.assertEqual(out["skipped_ready"], 1)
        self.assertEqual(out["warmed"], 1)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "000001")

    def test_refresh_delegates_warmup(self):
        from quant.research.cluster_minute_status import refresh_cluster_minute_only

        warmup = {
            "ok": True,
            "kind": "minute_warmup",
            "total": 2,
            "warmed": 2,
            "period": "5",
            "lookback_days": 120,
        }
        with patch(
            "quant.research.cluster_minute_status._resolve_watching_codes",
            return_value=["600519", "000001"],
        ), patch("core.schedule_jobs._minute_warmup_core", return_value=warmup) as warm, patch(
            "quant.research.cluster_minute_status.build_cluster_minute_status",
            return_value={"success": True, "cached_ok": 2},
        ):
            out = refresh_cluster_minute_only(watching_limit=100, lookback_days=120)
        self.assertTrue(out["success"])
        warm.assert_called_once()
        self.assertEqual(warm.call_args.kwargs["cap"], 2)
        self.assertEqual(warm.call_args.kwargs["lookback_days"], 120)


if __name__ == "__main__":
    unittest.main()
