"""观察池日频 +1 趋势预判主干验收。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _synth_bars(n: int = 50, start: float = 10.0, step: float = 0.2):
    bars = []
    px = start
    d = datetime(2024, 1, 2)
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        # 制造可分方向的涨跌
        px = px * (1.01 if i % 4 != 3 else 0.985)
        bars.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1000 + i * 10,
            }
        )
        d += timedelta(days=1)
    return bars


class TestNextDayTrendCore(unittest.TestCase):
    def test_score_to_bias(self):
        from core.backtest.next_day_trend import score_to_bias

        self.assertEqual(score_to_bias(65), "up")
        self.assertEqual(score_to_bias(40), "down")
        self.assertEqual(score_to_bias(50), "flat")

    def test_report_eval_and_latest(self):
        from core.backtest.next_day_trend import compute_next_day_trend_report

        stock_bars = {
            f"S{i:02d}": _synth_bars(55, start=10 + i, step=0.15 + i * 0.01)
            for i in range(5)
        }

        def _fake_score(code, window, **kwargs):
            # 用末收相对均价做伪 score，保证有方差
            closes = [float(b["close"]) for b in window]
            avg = sum(closes) / len(closes)
            last = closes[-1]
            score = 50 + (last / avg - 1.0) * 200
            score = max(20.0, min(80.0, score))
            return {
                "stock_code": code,
                "score": score,
                "sub_scores": {},
                "hard_reject": False,
            }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item",
            side_effect=_fake_score,
        ):
            out = compute_next_day_trend_report(
                stock_bars,
                flat_band_pct=0.5,
                lookback_eval_days=40,
                min_history=12,
                pit_fundamentals=False,
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("horizon_days"), 1)
        self.assertEqual(out.get("mode"), "watchlist_daily_plus1")
        self.assertEqual(out.get("calendar"), "cn_lite")
        ev = out.get("eval") or {}
        self.assertGreaterEqual(int(ev.get("sample_count") or 0), 10)
        self.assertIsNotNone(ev.get("hit_rate"))
        latest = out.get("latest") or []
        self.assertGreaterEqual(len(latest), 3)
        for row in latest:
            self.assertIn(row.get("bias"), ("up", "down", "flat"))
            self.assertIn("score", row)
            self.assertIn("as_of", row)

    def test_latest_without_future_bar(self):
        """最后一日无 t+1 时仍给 bias，不编造 realized。"""
        from core.backtest.next_day_trend import compute_next_day_trend_report

        bars = _synth_bars(30)
        last = bars[-1]["date"]
        with patch(
            "core.signal.cross_section_batch.score_window_as_item",
            return_value={
                "stock_code": "T1",
                "score": 62.0,
                "sub_scores": {},
                "hard_reject": False,
            },
        ):
            out = compute_next_day_trend_report(
                {"T1": bars},
                lookback_eval_days=20,
                pit_fundamentals=False,
                session_date=last,
            )
        latest = out.get("latest") or []
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["bias"], "up")
        self.assertEqual(latest[0]["decision_phase"], "bar_for_next")
        # 最后一根无次日 → realized 应为 None
        self.assertIsNone(latest[0].get("realized_next_pct"))

    def test_session_uses_live_quote_for_tomorrow(self):
        """日线停在昨收 + 现价 → 决策今日、预判明日（勿与今日涨跌假对照）。"""
        from core.backtest.next_day_trend import compute_next_day_trend_report

        bars = _synth_bars(30)
        last = bars[-1]["date"]
        # 下一交易日（跳过周末）
        from core.market_calendar import next_trading_day

        sess = next_trading_day(last)
        self.assertTrue(sess)
        with patch(
            "core.signal.cross_section_batch.score_window_as_item",
            return_value={
                "stock_code": "T1",
                "score": 70.0,
                "sub_scores": {},
                "hard_reject": False,
            },
        ) as mocked:
            out = compute_next_day_trend_report(
                {"T1": bars},
                lookback_eval_days=20,
                pit_fundamentals=False,
                session_date=sess,
                live_quotes={
                    "T1": {
                        "success": True,
                        "price_raw": 12.5,
                        "change_raw": 1.2,
                        "open": 12.3,
                        "high": 12.6,
                        "low": 12.2,
                    }
                },
            )
        latest = out.get("latest") or []
        self.assertEqual(len(latest), 1)
        row = latest[0]
        self.assertEqual(row["as_of"], sess)
        self.assertEqual(row["decision_phase"], "session_for_tomorrow")
        self.assertTrue(row["intraday_provisional"])
        self.assertEqual(row["target_date"], next_trading_day(sess))
        self.assertIsNone(row.get("realized_next_pct"))
        # 评分窗口末根应为暂估今日
        call_window = mocked.call_args[0][1]
        self.assertEqual(call_window[-1]["date"], sess)
        self.assertEqual(call_window[-1]["close"], 12.5)

    def test_prior_close_when_no_quote(self):
        from core.backtest.next_day_trend import compute_next_day_trend_report
        from core.market_calendar import next_trading_day

        bars = _synth_bars(30)
        last = bars[-1]["date"]
        sess = next_trading_day(last)
        with patch(
            "core.signal.cross_section_batch.score_window_as_item",
            return_value={
                "stock_code": "T1",
                "score": 40.0,
                "sub_scores": {},
                "hard_reject": False,
            },
        ):
            out = compute_next_day_trend_report(
                {"T1": bars},
                lookback_eval_days=20,
                pit_fundamentals=False,
                session_date=sess,
                live_quotes={},
            )
        row = (out.get("latest") or [])[0]
        self.assertEqual(row["decision_phase"], "prior_close_for_today")
        self.assertEqual(row["as_of"], last)
        self.assertEqual(row["target_date"], sess)

    def test_weekend_filtered(self):
        from core.market_calendar import filter_trading_dates

        raw = ["2024-01-05", "2024-01-06", "2024-01-07"]
        filtered = filter_trading_dates(raw)
        self.assertNotIn("2024-01-06", filtered)
        self.assertNotIn("2024-01-07", filtered)


class TestNextDayTrendApi(unittest.TestCase):
    def test_schema_and_route_import(self):
        from web.schemas import NextDayTrendRequest
        from web.routers import quant as quant_router

        body = NextDayTrendRequest(lookback=80, flat_band_pct=0.5)
        self.assertEqual(body.lookback_eval_days, 60)
        paths = [getattr(r, "path", "") for r in quant_router.router.routes]
        self.assertTrue(any("next-day-trend" in str(p) for p in paths))

    def test_watching_panel_auto_ndbias_column(self):
        panel = os.path.join(
            ROOT, "web", "static", "partials", "watching_panel.html"
        )
        with open(panel, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("watching-next-day-trend-run", html)
        self.assertNotIn("watching-next-day-section", html)
        island = os.path.join(
            ROOT, "web", "static", "js", "watching_table_island.js"
        )
        with open(island, encoding="utf-8") as f:
            js = f.read()
        self.assertIn('id: "ndBias"', js)
        quant_js = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(quant_js, encoding="utf-8") as f:
            qjs = f.read()
        self.assertIn("fillWatchingNextDayTrend", qjs)
        quant = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(quant, encoding="utf-8") as f:
            qhtml = f.read()
        self.assertNotIn("quant-next-day-trend-run", qhtml)


if __name__ == "__main__":
    unittest.main()
