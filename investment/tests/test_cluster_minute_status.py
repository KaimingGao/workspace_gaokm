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
        self.assertEqual(st["span_distribution"], [{"bucket": "<40d", "count": 1}])

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

        with patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 30,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ):
            ok, _, reason = minute_cache_ready("600900")
        self.assertTrue(ok)
        self.assertEqual(reason, "ready")

        with patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 29,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ):
            ok, _, reason = minute_cache_ready("600050")
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
        self.assertFalse(fetch.call_args.kwargs.get("skip_em"))

    def test_refresh_delegates_warmup(self):
        from quant.research.cluster_minute_status import refresh_cluster_minute_only

        warmup = {
            "ok": True,
            "kind": "minute_warmup",
            "total": 2,
            "warmed": 2,
            "period": "5",
            "lookback_days": 30,
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
        self.assertEqual(out["mode"], "full")
        warm.assert_called_once()
        self.assertEqual(warm.call_args.kwargs["cap"], 2)
        self.assertEqual(warm.call_args.kwargs["lookback_days"], 30)

    def test_expected_minute_asof_before_open(self):
        from datetime import datetime
        from quant.research.cluster_minute_status import expected_minute_asof

        # 交易日开盘前 → 上一交易日
        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-28"
        ), patch("core.market.calendar.is_trading_day", return_value=True), patch(
            "core.market.calendar.prev_trading_day", return_value="2026-08-27"
        ):
            asof = expected_minute_asof(now=datetime(2026, 8, 28, 9, 0, 0))
        self.assertEqual(asof, "2026-08-27")

        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-28"
        ), patch("core.market.calendar.is_trading_day", return_value=True):
            asof2 = expected_minute_asof(now=datetime(2026, 8, 28, 10, 0, 0))
        self.assertEqual(asof2, "2026-08-28")

    def test_topup_skips_aligned_and_short_lookback(self):
        from quant.research.cluster_minute_status import _minute_topup_core

        def _snap(code: str, *, period: str = "5"):
            if code == "600519":
                return {"span_days": 40, "date_max": "2026-08-28", "bar_count": 100}
            if code == "000001":
                return {"span_days": 40, "date_max": "2026-08-26", "bar_count": 100}
            return None

        calls = []

        def _fetch(code, **kwargs):
            calls.append((code, kwargs.get("lookback_days"), kwargs.get("skip_em")))
            return [{"date": "2026-08-28 10:00:00"}], {"ok": True}

        with patch(
            "quant.research.cluster_minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch("core.ports.market.fetch_minute_bars", side_effect=_fetch):
            out = _minute_topup_core(
                codes=["600519", "000001", "300750"],
                topup_lookback_days=5,
                full_lookback_days=30,
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped_aligned"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out["bootstrapped"], 1)
        self.assertEqual(len(calls), 2)
        by_code = {c: (lb, sem) for c, lb, sem in calls}
        self.assertEqual(by_code["000001"], (5, True))
        self.assertEqual(by_code["300750"], (30, False))

    def test_refresh_topup_mode_delegates(self):
        from quant.research.cluster_minute_status import refresh_cluster_minute_only

        warmup = {
            "ok": True,
            "kind": "minute_topup",
            "mode": "topup",
            "total": 2,
            "warmed": 2,
            "skipped_aligned": 1,
            "topped": 1,
        }
        with patch(
            "quant.research.cluster_minute_status._resolve_watching_codes",
            return_value=["600519", "000001"],
        ), patch(
            "quant.research.cluster_minute_status._minute_topup_core",
            return_value=warmup,
        ) as top, patch(
            "quant.research.cluster_minute_status.build_cluster_minute_status",
            return_value={"success": True, "cached_ok": 2},
        ):
            out = refresh_cluster_minute_only(
                watching_limit=100, mode="topup", topup_lookback_days=5
            )
        self.assertTrue(out["success"])
        self.assertEqual(out["mode"], "topup")
        top.assert_called_once()
        self.assertEqual(top.call_args.kwargs["topup_lookback_days"], 5)


if __name__ == "__main__":
    unittest.main()
