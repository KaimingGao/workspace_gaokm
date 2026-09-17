"""因子异常闸：数据缺失与时间错位。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestInspectFactorAnomaly(unittest.TestCase):
    def test_missing_open_t_intraday(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        now = datetime(2026, 9, 17, 10, 15)
        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-09-16", "dual_score_window": "intraday"},
            open_t_info={"trade_day": "2026-09-17", "open": None, "prev_close": 10.0},
            quote={},
            trade_day="2026-09-17",
            now=now,
            live=True,
        )
        self.assertFalse(out["ok"])
        self.assertTrue(out["fatal_eod"])
        self.assertEqual(out["gate_reason"], "factor_anomaly:missing:open_t")
        kinds = {(i["kind"], i["code"]) for i in out["issues"]}
        self.assertIn(("missing", "open_t"), kinds)

    def test_pit_eod_as_of_stale(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        now = datetime(2026, 9, 17, 10, 15)
        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-09-15", "dual_score_window": "intraday"},
            open_t_info={
                "trade_day": "2026-09-17",
                "open": 10.2,
                "prev_close": 10.0,
                "gap_pct": 2.0,
            },
            quote={},
            trade_day="2026-09-17",
            now=now,
            live=True,
        )
        self.assertTrue(out["fatal_eod"])
        self.assertEqual(out["gate_reason"], "factor_anomaly:pit:eod_as_of")
        self.assertTrue(any(i["kind"] == "pit" and i["code"] == "eod_as_of" for i in out["issues"]))

    def test_intraday_leak_t_bar(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-09-17", "dual_score_window": "intraday"},
            open_t_info={
                "trade_day": "2026-09-17",
                "open": 10.2,
                "prev_close": 10.0,
                "gap_pct": 2.0,
            },
            quote={"date": "2026-09-17"},
            trade_day="2026-09-17",
            now=datetime(2026, 9, 17, 10, 15),
            live=False,
        )
        self.assertTrue(out["fatal_eod"])
        codes = {i["code"] for i in out["issues"] if i["kind"] == "pit"}
        self.assertTrue("eod_intraday_leak" in codes or "eod_as_of" in codes)

    def test_historical_matching_dates_ok(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-08-14", "dual_score_window": "eod_next"},
            open_t_info={
                "trade_day": "2026-08-14",
                "open": 10.0,
                "prev_close": 9.8,
                "gap_pct": round((10.0 / 9.8 - 1.0) * 100.0, 4),
            },
            quote={"date": "2026-08-14", "open": 10.0},
            trade_day="2026-08-14",
            now=datetime(2026, 9, 17, 10, 15),
            live=False,
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["fatal_eod"])

    def test_as_of_tau_mismatch_nulls_tau_only(self):
        from core.signal.factor_anomaly import (
            apply_factor_anomaly_to_item,
            inspect_factor_anomaly,
        )

        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-09-16", "dual_score_window": "intraday"},
            open_t_info={
                "trade_day": "2026-09-17",
                "open": 10.2,
                "prev_close": 10.0,
                "gap_pct": 2.0,
            },
            quote={"date": "2026-09-17"},
            trade_day="2026-09-17",
            now=datetime(2026, 9, 17, 10, 15),
            as_of_tau="2026-09-16T10:00",
            live=False,
        )
        self.assertTrue(out["fatal_tau"])
        self.assertFalse(out["fatal_eod"])
        item = {
            "predicted_score": 1.2,
            "y_oo": 1.2,
            "y_oc": 0.4,
            "y_co": 0.3,
            "ranking": 0.9,
            "predicted_score_tau": 0.4,
        }
        apply_factor_anomaly_to_item(item, out, bypass=False)
        self.assertEqual(item.get("predicted_score"), 1.2)
        self.assertIsNone(item.get("y_oc"))
        self.assertIsNone(item.get("ranking"))
        self.assertIsNone(item.get("predicted_score_tau"))

    def test_last_change_vs_gap(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-08-14", "dual_score_window": "eod_next"},
            open_t_info={
                "trade_day": "2026-08-14",
                "open": 10.0,
                "prev_close": 10.0,
                "gap_pct": 0.5,
            },
            quote={"date": "2026-08-14"},
            trade_day="2026-08-14",
            last_change=3.2,
            live=False,
        )
        self.assertTrue(out["fatal_eod"])
        self.assertTrue(
            any(i["code"] == "last_change_vs_gap" for i in out["issues"])
        )

    def test_missing_all_beta_factors(self):
        from core.signal.factor_anomaly import inspect_factor_anomaly

        out = inspect_factor_anomaly(
            eod_pit={"eod_as_of": "2026-08-14", "dual_score_window": "eod_next"},
            open_t_info={
                "trade_day": "2026-08-14",
                "open": 10.0,
                "prev_close": 9.9,
                "gap_pct": 1.01,
            },
            quote={"date": "2026-08-14"},
            trade_day="2026-08-14",
            required_keys=["momentum", "reversal"],
            sub_scores={},
            live=False,
        )
        self.assertTrue(out["fatal_eod"])
        self.assertEqual(out["gate_reason"], "factor_anomaly:missing:sub_scores")


class TestStampWindowFactorAnomaly(unittest.TestCase):
    def test_stamp_drops_ranking_when_tau_fatal(self):
        from core.signal.yhat_windows import stamp_window_scores

        item = {
            "predicted_score": 1.0,
            "y_oo": 1.0,
            "predicted_score_tau": 0.5,
            "y_oc": 0.5,
            "y_co": 0.2,
            "factor_anomaly": {"fatal_tau": True, "fatal_eod": False, "issues": []},
        }
        stamped = stamp_window_scores(item)
        self.assertIsNone(stamped.get("y_oc"))
        self.assertIsNone(stamped.get("ranking"))
        self.assertAlmostEqual(stamped.get("y_oo"), 1.0)


class TestScoreStockFactorAnomaly(unittest.TestCase):
    def _bars(self, last="2026-09-16", n=20):
        from datetime import timedelta

        end = datetime.strptime(last, "%Y-%m-%d")
        rows = []
        for i in range(n):
            d = end - timedelta(days=n - 1 - i)
            rows.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.1,
                    "volume": 1000,
                }
            )
        return rows

    def test_live_missing_open_gates(self):
        from core.signal.score_stock import score_stock

        now = datetime(2026, 9, 17, 10, 15)
        bars = self._bars("2026-09-16")
        quote = {
            "success": True,
            "stock_code": "999999",
            "stock_name": "测试",
            "price": 10.4,
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars",
            return_value=(bars, "akshare_cn_daily"),
        ), patch(
            "core.signal.session_pit.shanghai_now", return_value=now
        ), patch(
            "core.signal.minute_tau_feats.minutes_for_open_t", return_value=[]
        ), patch(
            "core.signal.score_stock.score_bars"
        ) as mock_score:
            out = score_stock(
                "999999",
                quote=quote,
                skip_fundamentals=True,
                skip_sentiment=True,
                use_minute_tau=False,
            )
        mock_score.assert_not_called()
        self.assertTrue(out.get("quality_gate"))
        item = out.get("signal_item") or {}
        self.assertTrue(item.get("hard_reject"))
        self.assertIn("factor_anomaly:missing:open_t", str(item.get("gate_reason") or ""))
        self.assertEqual((item.get("factor_anomaly") or {}).get("gate_reason"), "factor_anomaly:missing:open_t")


class TestComputeFactorExceptionOmit(unittest.TestCase):
    def test_one_factor_crash_does_not_abort(self):
        from core.signal.factors.meta import registry as reg

        bars = [
            {"date": "2026-08-13", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"date": "2026-08-14", "open": 10, "high": 11, "low": 9, "close": 10.2, "volume": 1},
        ]

        def _boom(*_a, **_k):
            raise RuntimeError("factor boom")

        with patch.dict(
            reg._REGISTRY,
            {"momentum": {**reg._REGISTRY["momentum"], "compute": _boom}},
        ):
            subs, _c, meta = reg.compute_configured_factors(
                bars, weights={"momentum": 1.0, "volatility": 1.0}
            )
        self.assertNotIn("momentum", subs)
        self.assertIn("volatility", subs)
        self.assertIn("momentum_compute_error", meta)


if __name__ == "__main__":
    unittest.main()
