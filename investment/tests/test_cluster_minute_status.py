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
        ), patch(
            "quant.research.cluster_minute_status.build_minute_label_portrait",
            return_value={"success": True, "tau": {"pos": 1}, "path": {"pos": 1}, "joint": {}},
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
        self.assertEqual(
            st["span_distribution"],
            [
                {"bucket": "<90d", "count": 1},
                {"bucket": "<60d", "count": 1},
                {"bucket": "<30d", "count": 1},
            ],
        )

    def test_span_mix_fine_cuts(self):
        from quant.research.cluster_minute_status import build_cluster_minute_status

        watch = ["A", "B", "C", "D", "E"]

        def _snap(code: str, *, period: str = "5"):
            spans = {"A": 95, "B": 70, "C": 45, "D": 25, "E": None}
            if spans.get(code) is None:
                return None
            return {
                "span_days": spans[code],
                "bar_count": 100,
                "fetched_at": "2026-08-26T10:00:00",
            }

        with patch("core.watching.store.read_watching", return_value={"watchlist": watch}), patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "quant.research.cluster_minute_status.build_minute_label_portrait",
            return_value={"success": True, "tau": {}, "path": {}, "joint": {}},
        ):
            st = build_cluster_minute_status(watching_limit=100, min_span_days=30)
        self.assertEqual(st["cached_ok"], 3)
        self.assertEqual(st["short"], 1)
        self.assertEqual(st["missing"], 1)
        # 累计（含已过 Ready 闸）：B70+C45+D25 → <90d；C45+D25 → <60d；D25 → <30d
        self.assertEqual(
            st["span_distribution"],
            [
                {"bucket": "<90d", "count": 3},
                {"bucket": "<60d", "count": 2},
                {"bucket": "<30d", "count": 1},
            ],
        )

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
                return {
                    "span_days": 40,
                    "date_max": "2026-08-28",
                    "bar_count": 100,
                    "session_complete": True,
                }
            if code == "000001":
                return {
                    "span_days": 40,
                    "date_max": "2026-08-26",
                    "bar_count": 100,
                    "session_complete": True,
                }
            return None

        calls = []

        def _fetch(code, **kwargs):
            calls.append((code, kwargs.get("lookback_days"), kwargs.get("skip_em"), kwargs.get("skip_bs")))
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
        by_code = {c: (lb, sem, sbs) for c, lb, sem, sbs in calls}
        self.assertEqual(by_code["000001"], (5, True, True))
        self.assertEqual(by_code["300750"], (30, False, False))

    def test_topup_skips_fetched_today(self):
        from quant.research.cluster_minute_status import _minute_topup_core

        today = datetime.now().strftime("%Y-%m-%dT10:00:00")

        def _snap(code: str, *, period: str = "5"):
            if code == "600050":
                # Short 但今日已拉过且会话齐窗 → 应跳过，避免新浪近端空转
                return {
                    "span_days": 23,
                    "date_max": "2026-08-31",
                    "bar_count": 1071,
                    "fetched_at": today,
                    "session_complete": True,
                }
            if code == "000001":
                # Ready 未对齐、非今日 → 仍 topup
                return {
                    "span_days": 40,
                    "date_max": "2026-08-26",
                    "bar_count": 100,
                    "fetched_at": "2026-08-30T10:00:00",
                    "session_complete": True,
                }
            return None

        calls = []

        def _fetch(code, **kwargs):
            calls.append(code)
            return [{"date": "2026-08-28 10:00:00"}], {"ok": True}

        with patch(
            "quant.research.cluster_minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch("core.ports.market.fetch_minute_bars", side_effect=_fetch):
            out = _minute_topup_core(
                codes=["600050", "000001", "300750"],
                topup_lookback_days=5,
                full_lookback_days=30,
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped_today"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out["bootstrapped"], 1)
        self.assertEqual(set(calls), {"000001", "300750"})
        self.assertNotIn("600050", calls)

    def test_topup_refreshes_truncated_session_even_if_fetched_today(self):
        from quant.research.cluster_minute_status import _minute_topup_core

        today = datetime.now().strftime("%Y-%m-%dT10:05:00")
        expected = datetime.now().strftime("%Y-%m-%d")

        def _snap(code: str, *, period: str = "5"):
            if code == "002415":
                return {
                    "span_days": 40,
                    "date_max": expected,
                    "bar_count": 200,
                    "fetched_at": today,
                    "session_complete": False,
                    "last_bar_hm": "10:00",
                }
            return {
                "span_days": 40,
                "date_max": expected,
                "bar_count": 400,
                "fetched_at": today,
                "session_complete": True,
                "last_bar_hm": "14:55",
            }

        calls = []

        def _fetch(code, **kwargs):
            calls.append((code, kwargs.get("use_cache"), kwargs.get("skip_em"), kwargs.get("skip_bs")))
            return [{"date": f"{expected} 14:55:00"}], {"ok": True}

        with patch(
            "quant.research.cluster_minute_status.expected_minute_asof",
            return_value=expected,
        ), patch(
            "quant.research.cluster_minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch("core.ports.market.fetch_minute_bars", side_effect=_fetch):
            out = _minute_topup_core(
                codes=["002415", "000001"],
                topup_lookback_days=5,
                full_lookback_days=30,
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped_today"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out.get("refreshed_truncated"), 1)
        self.assertEqual(calls, [("002415", False, True, True)])

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

    def test_label_portrait_sign_counts(self):
        from quant.research.cluster_minute_status import build_minute_label_portrait

        # day1: up open→close, low then high → path+
        day_up = [
            {"time": "09:35", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
            {"time": "10:00", "open": 10.0, "high": 10.5, "low": 10.0, "close": 10.4},
        ]
        # day2: down open→close, high then low → path−
        day_dn = [
            {"time": "09:35", "open": 10.0, "high": 10.4, "low": 9.95, "close": 10.2},
            {"time": "10:00", "open": 10.2, "high": 10.2, "low": 9.5, "close": 9.6},
        ]
        bars = [
            {**b, "date": "2026-08-20"} for b in day_up
        ] + [
            {**b, "date": "2026-08-21"} for b in day_dn
        ]

        with patch(
            "quant.research.cluster_minute_status._resolve_watching_codes",
            return_value=["600519"],
        ), patch(
            "core.ports.market.resolve_market_code",
            return_value=("CN", "600519"),
        ), patch(
            "core.store.load_minute_cache",
            return_value=(bars, {"bar_count": len(bars)}),
        ), patch(
            "core.ports.market.group_minute_bars_by_date",
            return_value={"2026-08-20": day_up, "2026-08-21": day_dn},
        ):
            out = build_minute_label_portrait(watching_limit=10, codes=["600519"])
        self.assertTrue(out["success"])
        self.assertEqual(out["days_scanned"], 2)
        self.assertEqual(out["tau"]["pos"], 1)
        self.assertEqual(out["tau"]["neg"], 1)
        self.assertEqual(out["path"]["pos"], 1)
        self.assertEqual(out["path"]["neg"], 1)
        self.assertEqual(out["joint"]["same_sign"], 2)
        self.assertEqual(out["joint"]["opposite_sign"], 0)


if __name__ == "__main__":
    unittest.main()
