"""观察池 5m 分钟线状态汇总。"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch


class TestMinuteStatus(unittest.TestCase):
    def test_build_status_counts(self):
        from quant.research.minute_status import build_minute_status

        watch = ["600519", "000001", "300750"]

        def _snap(code: str, *, period: str = "5"):
            if code == "600519":
                return {
                    "span_days": 95,
                    "bar_count": 1000,
                    "fetched_at": "2026-08-26T10:00:00",
                    "date_max": "2026-08-26",
                }
            if code == "000001":
                return {
                    "span_days": 25,
                    "bar_count": 200,
                    "fetched_at": "2026-08-26T11:30:00",
                    "date_max": "2026-08-25",
                }
            return None

        with patch("core.watching.store.read_watching", return_value={"watchlist": watch}        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "quant.research.bars_integrity.minute_windows_clean",
            return_value={"600519": True, "000001": False},
        ):
            st = build_minute_status(watching_limit=100, min_span_days=40)
        self.assertTrue(st["success"])
        self.assertEqual(st["universe_count"], 3)
        self.assertEqual(st["cached_ok"], 1)
        self.assertEqual(st["short"], 1)
        self.assertEqual(st["missing"], 1)
        self.assertFalse(st["coverage_ok"])
        self.assertEqual(st["coverage_pct"], round(100 / 3, 1))
        self.assertEqual(st["minute_span_days_med"], 95)
        self.assertEqual(st["last_bar_min"], "2026-08-25")
        self.assertEqual(st["last_bar_max"], "2026-08-26")
        self.assertEqual(st["last_fetched_at"], "2026-08-26T11:30:00")
        self.assertTrue(st.get("expected_latest_bar"))
        self.assertEqual(
            st["span_distribution"],
            [
                {"bucket": "<30d", "count": 1},
                {"bucket": "<40d", "count": 1},
                {"bucket": "<50d", "count": 1},
            ],
        )

    def test_span_mix_fine_cuts(self):
        from quant.research.minute_status import build_minute_status

        watch = ["A", "B", "C", "D", "E"]

        def _snap(code: str, *, period: str = "5"):
            spans = {"A": 95, "B": 45, "C": 35, "D": 28, "E": None}
            if spans.get(code) is None:
                return None
            return {
                "span_days": spans[code],
                "bar_count": 100,
                "fetched_at": "2026-08-26T10:00:00",
            }

        with patch("core.watching.store.read_watching", return_value={"watchlist": watch}        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "quant.research.bars_integrity.minute_windows_clean",
            return_value={"A": True, "B": True},
        ):
            st = build_minute_status(watching_limit=100, min_span_days=20)
        self.assertEqual(st["cached_ok"], 2)
        self.assertEqual(st["short"], 2)
        self.assertEqual(st["missing"], 1)
        # 累计（含已过 Ready 闸）：D28 → <30d；C35+D28 → <40d；B45+C35+D28 → <50d
        self.assertEqual(
            st["span_distribution"],
            [
                {"bucket": "<30d", "count": 1},
                {"bucket": "<40d", "count": 2},
                {"bucket": "<50d", "count": 3},
            ],
        )

    def test_minute_cache_ready(self):
        from quant.research.minute_status import minute_cache_ready

        with patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 50,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ), patch(
            "quant.research.bars_integrity.minute_windows_clean",
            return_value={"600519": True},
        ):
            ok, _, reason = minute_cache_ready("600519")
        self.assertTrue(ok)
        self.assertEqual(reason, "ready")

        with patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            return_value={
                "span_days": 20,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "date_max": datetime.now().strftime("%Y-%m-%d"),
            },
        ), patch(
            "quant.research.bars_integrity.minute_windows_clean",
            return_value={"000001": False},
        ):
            ok, _, reason = minute_cache_ready("000001")
        self.assertFalse(ok)
        self.assertEqual(reason, "gap")

        with patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            return_value=None,
        ):
            ok, _, reason = minute_cache_ready("600050")
        self.assertFalse(ok)
        self.assertEqual(reason, "missing")

    def test_warmup_skips_ready(self):
        from core.platform.schedule_jobs import _minute_warmup_core

        def _ready_port(name, *args, **kwargs):
            if name != "minute_cache_ready":
                raise AssertionError(f"unexpected port call: {name}")
            code = args[0] if args else kwargs.get("code")
            return (code == "600519", {}, "ready")

        with patch(
            "core.platform.schedule_jobs._resolve_warmup_codes",
            return_value=["600519", "000001"],
        ), patch(
            "core.ports.registry.call",
            side_effect=_ready_port,
        ), patch(
            "core.data.policy.minute_warmup_skip_if_ready", return_value=True
        ), patch(
            "core.data.policy.minute_warmup_pace_sec", return_value=0.0
        ), patch(
            "core.ports.market.fetch_minute_bars_isolated",
            return_value=([], {}),
        ) as fetch:
            out = _minute_warmup_core(codes=["600519", "000001"], cap=2)
        self.assertEqual(out["skipped_ready"], 1)
        self.assertEqual(out["warmed"], 1)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "000001")
    def test_warmup_continues_after_isolated_timeout(self):
        """子进程超时只跳过该票，不收尾跳过剩余。"""
        from core.platform.schedule_jobs import _minute_warmup_core

        calls = []

        def _iso(code, **kw):
            calls.append(code)
            if code == "600519":
                return [], {"error": "timeout (90s)", "timeout_sec": 90}
            return ([{"datetime": "2026-01-01 09:35:00", "date": "2026-01-01", "close": 1.0}], {"ok": True})

        with patch(
            "core.platform.schedule_jobs._resolve_warmup_codes",
            return_value=["600519", "000001", "300750"],
        ), patch(
            "core.data.policy.minute_warmup_skip_if_ready", return_value=False
        ), patch(
            "core.data.policy.minute_warmup_pace_sec", return_value=0.0
        ), patch(
            "core.ports.market.fetch_minute_bars_isolated",
            side_effect=_iso,
        ):
            out = _minute_warmup_core(codes=["600519", "000001", "300750"], cap=3)

        self.assertCountEqual(calls, ["600519", "000001", "300750"])
        self.assertEqual(out.get("timed_out"), 1)
        self.assertEqual(out.get("warmed"), 2)
        self.assertGreaterEqual(int(out.get("workers") or 0), 1)
        self.assertTrue(out.get("isolated"))

    def test_refresh_delegates_warmup(self):
        from quant.research.minute_status import refresh_minute_only

        warmup = {
            "ok": True,
            "kind": "minute_warmup",
            "total": 2,
            "warmed": 2,
            "period": "5",
        }
        with patch(
            "quant.research.minute_status._resolve_watching_codes",
            return_value=["600519", "000001"],
        ), patch("core.platform.schedule_jobs._minute_warmup_core", return_value=warmup) as warm, patch(
            "quant.research.minute_status.build_minute_status",
            return_value={"success": True, "cached_ok": 2},
        ):
            out = refresh_minute_only(watching_limit=100)
        self.assertTrue(out["success"])
        self.assertEqual(out["mode"], "full")
        warm.assert_called_once()
        self.assertEqual(warm.call_args.kwargs["cap"], 2)
        self.assertNotIn("lookback_days", warm.call_args.kwargs)

    def test_expected_minute_asof_before_open(self):
        from datetime import datetime

        from quant.research.minute_status import expected_minute_asof

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
        from quant.research.minute_status import _minute_topup_core

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
            calls.append(code)
            return [{"date": "2026-08-28 10:00:00"}], {"ok": True}

        with patch(
            "quant.research.minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "adapters.market.minute_history.fetch_a_minute_bars_isolated",
            side_effect=_fetch,
        ):
            out = _minute_topup_core(
                codes=["600519", "000001", "300750"],
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("isolated"))
        self.assertEqual(out["skipped_aligned"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out["bootstrapped"], 1)
        self.assertEqual(set(calls), {"000001", "300750"})

    def test_topup_skips_fetched_today(self):
        from quant.research.minute_status import _minute_topup_core

        today = datetime.now().strftime("%Y-%m-%dT10:00:00")

        def _snap(code: str, *, period: str = "5"):
            if code == "600050":
                # Short 但今日已拉过且会话齐窗 → 应跳过，避免新浪近端空转
                return {
                    "span_days": 15,
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
            "quant.research.minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "adapters.market.minute_history.fetch_a_minute_bars_isolated",
            side_effect=_fetch,
        ):
            out = _minute_topup_core(
                codes=["600050", "000001", "300750"],
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped_today"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out["bootstrapped"], 1)
        self.assertEqual(set(calls), {"000001", "300750"})
        self.assertNotIn("600050", calls)

    def test_topup_refreshes_truncated_session_even_if_fetched_today(self):
        from quant.research.minute_status import _minute_topup_core

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
            calls.append((code, kwargs.get("use_cache")))
            return [{"date": f"{expected} 14:55:00"}], {"ok": True}

        with patch(
            "quant.research.minute_status.expected_minute_asof",
            return_value=expected,
        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "adapters.market.minute_history.fetch_a_minute_bars_isolated",
            side_effect=_fetch,
        ):
            out = _minute_topup_core(
                codes=["002415", "000001"],
                workers=2,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["skipped_today"], 1)
        self.assertEqual(out["topped"], 1)
        self.assertEqual(out.get("refreshed_truncated"), 1)
        self.assertEqual(calls, [("002415", False)])

    def test_topup_continues_after_isolated_timeout(self):
        from quant.research.minute_status import _minute_topup_core

        calls = []

        def _snap(code: str, *, period: str = "5"):
            return None

        def _fetch(code, **kwargs):
            calls.append((code, kwargs.get("throttle")))
            if code == "600519":
                return [], {"error": "timeout (90s)", "timeout_sec": 90}
            return [{"date": "2026-08-28 10:00:00"}], {"ok": True}

        with patch(
            "quant.research.minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "adapters.market.minute_history.fetch_a_minute_bars_isolated",
            side_effect=_fetch,
        ):
            out = _minute_topup_core(
                codes=["600519", "000001", "300750"],
                workers=2,
            )
        self.assertEqual({code for code, _throttle in calls}, {"600519", "000001", "300750"})
        self.assertTrue(all(throttle is False for _code, throttle in calls))
        self.assertEqual(out.get("timed_out"), 1)
        self.assertEqual(out.get("bootstrapped"), 2)
        self.assertTrue(out.get("isolated"))
        self.assertTrue(out.get("pace_stretched"))
        self.assertGreater(float(out.get("pace_sec") or 0), 1.0)

    def test_refresh_topup_mode_delegates(self):
        from quant.research.minute_status import refresh_minute_only

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
            "quant.research.minute_status._resolve_watching_codes",
            return_value=["600519", "000001"],
        ), patch(
            "quant.research.minute_status._minute_topup_core",
            return_value=warmup,
        ) as top, patch(
            "quant.research.minute_status.build_minute_status",
            return_value={"success": True, "cached_ok": 2},
        ):
            out = refresh_minute_only(watching_limit=100, mode="topup")
        self.assertTrue(out["success"])
        self.assertEqual(out["mode"], "topup")
        top.assert_called_once()
        self.assertNotIn("topup_lookback_days", top.call_args.kwargs)

    def test_progress_line_uses_daily_counter_shape(self):
        """分钟进度句与日线一样：拉5m N/M（远端 · 跳过 · 失败 · 并发）。"""
        from core.platform.schedule_jobs import format_minute_progress
        from quant.research.minute_status import _minute_topup_core

        line = format_minute_progress(
            3,
            10,
            remote=2,
            skipped_today=1,
            skipped_aligned=4,
            timed_out=0,
            failed=1,
            workers=4,
            cap_sec=180,
        )
        self.assertTrue(line.startswith("拉5m 3/10（"))
        for bit in ("远端 2", "今日跳过 1", "对齐跳过 4", "失败 1", "4 并发", "上限 180s"):
            self.assertIn(bit, line)
        # 坏计数不炸：夹到合法区间
        bad = format_minute_progress("x", None, remote=None, workers="n", cap_sec="bad")
        self.assertRegex(bad, r"^拉5m 0/1（")

        seen = []

        def _snap(code: str, *, period: str = "5"):
            if code == "600519":
                return {
                    "span_days": 40,
                    "date_max": "2026-08-28",
                    "bar_count": 100,
                    "session_complete": True,
                }
            return None

        def _fetch(code, **kwargs):
            return [{"date": "2026-08-28 10:00:00"}], {"ok": True}

        with patch(
            "quant.research.minute_status.expected_minute_asof",
            return_value="2026-08-28",
        ), patch(
            "quant.research.minute_status._minute_snapshot_for_code",
            side_effect=_snap,
        ), patch(
            "adapters.market.minute_history.fetch_a_minute_bars_isolated",
            side_effect=_fetch,
        ):
            _minute_topup_core(
                codes=["600519", "000001"],
                workers=2,
                progress_cb=lambda cur, total, label: seen.append(str(label)),
            )
        self.assertTrue(seen)
        last = seen[-1]
        self.assertRegex(last, r"^拉5m \d+/\d+（")
        self.assertIn("远端", last)
        self.assertIn("今日跳过", last)
        self.assertIn("对齐跳过", last)
        self.assertIn("失败", last)
        self.assertIn("并发", last)

    def test_label_portrait_sign_counts(self):
        from quant.research.minute_status import build_minute_label_portrait

        # day1: up open→close
        day_up = [
            {"time": "09:35", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
            {"time": "10:00", "open": 10.0, "high": 10.5, "low": 10.0, "close": 10.4},
        ]
        # day2: down open→close
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
            "quant.research.minute_status._resolve_watching_codes",
            return_value=["600519"],
        ), patch(
            "core.ports.market.resolve_market_code",
            return_value=("CN", "600519"),
        ), patch(
            "core.store.load_minute_span_snapshot",
            return_value={"date_max": "2026-08-21", "date_min": "2026-08-20"},
        ), patch(
            "core.store.load_minute_since",
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
        self.assertNotIn("path", out)
        self.assertNotIn("joint", out)

if __name__ == "__main__":
    unittest.main()
