"""仪表盘纸面契约：snapshots / holdings / signal_log（不依赖 lite 形状）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestDashboardPaperContract(unittest.TestCase):
    def test_equity_from_snapshots_and_signals(self):
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 50000,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 1000,
                    "sector": "消费",
                    "market_value": 120000,
                }
            ],
            "trades": [],
            "snapshots": [
                {"ts": "2026-01-01T10:00:00", "equity": 100000},
                {"ts": "2026-01-02T10:00:00", "equity": 101000},
            ],
            "signal_log": [
                {
                    "ts": "2026-01-02T12:00:00",
                    "success": True,
                    "observation_pool": [
                        {
                            "stock_code": "600519",
                            "stock_name": "茅台",
                            "predicted_score": 1.2,
                            "sector": "消费",
                        }
                    ],
                }
            ],
            "operation_log": [],
            "last_north_star": {
                "ok": True,
                "paper_risk": {"rolling_sharpe": 1.5, "max_drawdown_pct": 2.0},
            },
        }

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _P:
                pass

            fake = _P()
            fake.path = path
            with patch.object(qd.deps, "paper", fake):
                kpis = qd._build_kpis()
                self.assertAlmostEqual(kpis["total_return"], 1.0, places=2)
                self.assertEqual(kpis["sharpe"], 1.5)
                alloc = qd.dashboard_allocation()
                self.assertEqual(alloc["holdings_count"], 1)
                self.assertGreater(alloc["total_value"], 0)
                sig = qd.dashboard_signals(limit=5)
                self.assertGreaterEqual(len(sig["signals"]), 1)
                self.assertEqual(sig["signals"][0]["code"], "600519")
                nav = qd.dashboard_nav_curve(range="all", benchmark="none")
                self.assertEqual(nav["count"], 2)

    def test_unpack_index_bars_tuple(self):
        from web.routers.quant_dashboard import _unpack_index_bars

        bars = [{"date": "2026-01-01", "close": 1}, {"date": "2026-01-02", "close": 2}]
        self.assertEqual(len(_unpack_index_bars((bars, "上证"))), 2)
        self.assertEqual(_unpack_index_bars(None), [])
        self.assertEqual(_unpack_index_bars(bars), bars)

    def test_ic_series_from_daily_tail(self):
        from web.routers.quant_dashboard import _ic_series_from_daily_tail

        tail = []
        for i in range(12):
            tail.append(
                {
                    "date": f"2026-01-{i + 1:02d}",
                    "n_names": 8,
                    "factors": {
                        "momentum": {"pearson": 0.1 + i * 0.01, "spearman": 0.05},
                        "value": {"pearson": -0.05, "spearman": -0.02},
                    },
                }
            )
        factors, summary = _ic_series_from_daily_tail(tail, lookback=60, top_n=5)
        self.assertEqual(len(factors), 2)
        self.assertTrue(all(isinstance(p, dict) and "time" in p for p in factors[0]["ic_values"]))
        self.assertIn("ic_mean", summary)
        self.assertEqual(summary["factor_count"], 2)

    def test_var_historical_range_param_does_not_shadow_builtin(self):
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 100000,
            "holdings": [],
            "trades": [],
            "snapshots": [
                {"ts": f"2026-07-{d:02d}T10:00:00", "equity": 100000 + d * 10}
                for d in range(1, 20)
            ],
            "signal_log": [],
            "operation_log": [],
            "last_north_star": {"ok": True, "paper_risk": {}},
        }

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _P:
                pass

            fake = _P()
            fake.path = path
            with patch.object(qd.deps, "paper", fake):
                out = qd.dashboard_var_historical(range_key="all")
                self.assertTrue(out.get("ok"))
                self.assertGreaterEqual(out.get("sample_count") or 0, 5)

    def test_fetch_index_bars_bounded_timeout(self):
        from web.routers import quant_dashboard as qd

        def _hang(*_a, **_k):
            import time

            time.sleep(30)
            return ([], "")

        with patch("core.ports.market.fetch_index_bars", side_effect=_hang):
            t0 = __import__("time").time()
            bars = qd._fetch_index_bars_bounded("上证", limit=2, timeout_sec=0.6)
            elapsed = __import__("time").time() - t0
            self.assertEqual(bars, [])
            self.assertLess(elapsed, 2.5)

    def test_index_quotes_for_overview_from_batch(self):
        from web.routers import quant_dashboard as qd

        fake = {
            "sh000001": {
                "success": True,
                "stock_name": "上证指数",
                "price_raw": 3000.5,
                "change_raw": 1.2,
                "volume_raw": 1e8,
            },
            "sz399001": {
                "success": True,
                "stock_name": "深证成指",
                "price_raw": 10000.0,
                "change_raw": -0.5,
                "volume_raw": 2e8,
            },
            "sz399006": {
                "success": True,
                "stock_name": "创业板指",
                "price_raw": 2000.0,
                "change": "+0.8%",
                "volume_raw": 3e8,
            },
        }
        with patch("core.ports.market.batch_query_quotes", return_value=fake):
            rows = qd._index_quotes_for_overview()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["close"], 3000.5)
        self.assertEqual(rows[0]["change_pct"], 1.2)
        self.assertEqual(rows[1]["change_pct"], -0.5)
        self.assertEqual(rows[2]["change_pct"], 0.8)


if __name__ == "__main__":
    unittest.main()
