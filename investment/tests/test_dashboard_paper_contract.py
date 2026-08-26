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
            live = {
                "today_pnl_pct": 0.5,
                "total_pnl_pct": 1.0,
                "equity": 101000,
                "holdings": paper["holdings"],
            }
            with patch.object(qd.deps, "paper", fake), patch(
                "core.paper.mark_to_market", return_value=live
            ):
                kpis = qd._build_kpis()
                self.assertAlmostEqual(kpis["total_return"], 1.0, places=2)
                self.assertAlmostEqual(kpis["today_return"], 0.5, places=2)
                self.assertEqual(kpis["sharpe"], 1.5)
                alloc = qd.dashboard_allocation()
                self.assertEqual(alloc["holdings_count"], 1)
                self.assertGreater(alloc["total_value"], 0)
                sig = qd.dashboard_signals(limit=5)
                self.assertGreaterEqual(len(sig["signals"]), 1)
                self.assertEqual(sig["signals"][0]["code"], "600519")
                nav = qd.dashboard_nav_curve(range="all", benchmark="none")
                self.assertEqual(nav["count"], 2)

    def test_sector_heatmap_uses_day_change(self):
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 10000,
            "holdings": [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 1000,
                    "sector": "消费",
                },
                {
                    "stock_code": "601318",
                    "stock_name": "平安",
                    "shares": 200,
                    "cost": 50,
                    "sector": "金融",
                },
            ],
        }
        live = {
            "holdings": [
                {
                    "stock_code": "600519",
                    "market_value": 110000,
                    "change_pct": 1.5,
                    "price": 1100,
                },
                {
                    "stock_code": "601318",
                    "market_value": 9800,
                    "change_pct": -0.8,
                    "price": 49,
                },
            ]
        }
        with patch(
            "web.dashboard.portfolio_views._load_raw_paper", return_value=paper
        ), patch(
            "web.dashboard.portfolio_views._live_holdings_by_code",
            return_value={h["stock_code"]: h for h in live["holdings"]},
        ):
            out = qd.dashboard_sector_heatmap()
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("basis"), "day_change")
        by_name = {s["name"]: s for s in out["sectors"]}
        self.assertAlmostEqual(by_name["消费"]["change_pct"], 1.5, places=2)
        self.assertAlmostEqual(by_name["金融"]["change_pct"], -0.8, places=2)
        self.assertGreater(by_name["消费"]["up_count"], 0)
        self.assertGreater(by_name["金融"]["down_count"], 0)

    def test_signals_from_operation_log_meta(self):
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 10000,
            "holdings": [
                {
                    "stock_code": "600426",
                    "stock_name": "华鲁恒升",
                    "shares": 100,
                    "cost": 21.0,
                }
            ],
            "trades": [],
            "snapshots": [],
            "signal_log": [],
            "operation_log": [
                {
                    "ts": "2026-08-11T22:09:23",
                    "type": "cluster_pool_rebalance",
                    "type_label": "分池调仓",
                    "detail": "分池 live 调仓",
                    "meta": {"book_codes": ["600426"], "buy_count": 1, "sell_count": 0},
                },
                {
                    "ts": "2026-08-11T22:09:23",
                    "type": "buy",
                    "type_label": "买入",
                    "detail": "买入 华鲁恒升 100股 @ 21.0",
                    "meta": {
                        "stock_code": "600426",
                        "stock_name": "华鲁恒升",
                        "score": 2.7,
                    },
                },
            ],
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
                sig = qd.dashboard_signals(limit=10)
                codes = [s.get("code") for s in sig["signals"]]
                self.assertIn("600426", codes)
                row = next(s for s in sig["signals"] if s["code"] == "600426")
                self.assertEqual(row["name"], "华鲁恒升")
                self.assertAlmostEqual(float(row["score"]), 2.7)
                # 无单票 code 的调仓摘要不应占位
                self.assertTrue(all(s.get("code") for s in sig["signals"]))

                alloc = qd.dashboard_allocation()
                self.assertEqual(alloc["holdings_count"], 1)
                self.assertGreater(alloc["total_value"], 10000)
                # 手动仓无 sector/market_value：用成本市值 + 板块启发式，不应全进「其他」
                holding_sectors = [
                    s for s in alloc["sectors"] if s["name"] not in ("现金", "其他")
                ]
                self.assertTrue(holding_sectors)
                self.assertGreater(sum(s["value"] for s in holding_sectors), 0)

    def test_exposure_cost_fallback_for_unmarked_holdings(self):
        from core.risk.exposure import build_exposure_matrix

        paper = {
            "cash": 5000,
            "strategy_id": "short",
            "holdings": [
                {
                    "stock_code": "002594",
                    "stock_name": "比亚迪",
                    "shares": 200,
                    "cost": 90.0,
                },
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 10,
                    "cost": 1000.0,
                    "sector": "消费",
                    "market_value": 18000,
                },
            ],
        }
        out = build_exposure_matrix(
            paper, risk={"max_sector_pct": 90, "max_position_pct": 90}, sector_map={"002594": "新能源"}
        )
        self.assertTrue(out["ok"])
        codes = {n["stock_code"] for n in out["names"]}
        self.assertIn("002594", codes)
        self.assertIn("600519", codes)
        neo = next(r for r in out["sectors"] if r["name"] == "新能源")
        self.assertGreater(neo["market_value"], 0)

    def test_nav_curve_unique_intraday_times(self):
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 10000,
            "holdings": [],
            "trades": [],
            "snapshots": [
                {"ts": "2026-08-11T10:00:00", "equity": 100000},
                {"ts": "2026-08-11T11:00:00", "equity": 100100},
                {"ts": "2026-08-11T12:00:00", "equity": 99900},
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
            with patch.object(qd.deps, "paper", fake), patch(
                "core.paper.mark_to_market",
                return_value={"equity": 99900, "cash": 10000, "today_pnl_pct": 0.0},
            ):
                nav = qd.dashboard_nav_curve(range="all", benchmark="none")
                times = [p["time"] for p in nav["points"]]
                self.assertEqual(len(times), 3)
                self.assertEqual(len(set(times)), 3)
                risk = qd.dashboard_risk_metrics()
                self.assertTrue(risk.get("ok"))
                self.assertIsNotNone(risk.get("max_drawdown"))
                # 盯市失败时回退 snapshots：累计相对首末快照
                with patch(
                    "core.paper.mark_to_market", side_effect=RuntimeError("no quote")
                ):
                    kpis = qd._build_kpis()
                self.assertIsNotNone(kpis.get("sharpe"))
                # 99900/100000 - 1 = -0.1%
                self.assertAlmostEqual(kpis["total_return"], -0.1, places=2)

    def test_nav_curve_overlays_live_mtm(self):
        """盘中盯市与落盘快照分叉时，仪表盘净值末点应对齐现价。"""
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 55040.3,
            "initial_cash": 99999.85,
            "holdings": [],
            "trades": [],
            "snapshots": [
                {"ts": "2026-08-12T09:45:39.040", "equity": 99999.85},
                {"ts": "2026-08-18T11:48:25.999", "equity": 99976.3},
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
            live = {"equity": 100278.3, "cash": 55040.3, "total_pnl_pct": 0.28}
            with patch.object(qd.deps, "paper", fake), patch(
                "core.paper.mark_to_market", return_value=live
            ):
                nav = qd.dashboard_nav_curve(range="all", benchmark="none")
            self.assertGreaterEqual(nav["count"], 3)
            last = nav["points"][-1]
            self.assertTrue(last.get("live"))
            self.assertAlmostEqual(last["value"], round(100278.3 / 99999.85 * 100, 2))
            self.assertFalse(nav["points"][0].get("live"))

    def test_kpis_prefer_live_mtm_over_stale_snapshots(self):
        """回零后 snapshots 未续写时，仪表盘收益仍应对齐交易执行盯市。"""
        from web.routers import quant_dashboard as qd

        paper = {
            "cash": 50000,
            "initial_cash": 100000,
            "holdings": [
                {"stock_code": "600519", "shares": 100, "cost": 10.0},
            ],
            "trades": [],
            "snapshots": [
                {"ts": "2026-08-12T09:00:00", "equity": 100000, "total_pnl_pct": 0.0},
                {"ts": "2026-08-12T09:01:00", "equity": 99900, "total_pnl_pct": -0.1},
            ],
            "signal_log": [],
            "operation_log": [],
            "last_north_star": {"ok": True, "paper_risk": {"rolling_sharpe": 1.2}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _P:
                pass

            fake = _P()
            fake.path = path
            live = {"today_pnl_pct": 0.09, "total_pnl_pct": 0.24, "equity": 100240}
            with patch.object(qd.deps, "paper", fake), patch(
                "core.paper.mark_to_market", return_value=live
            ):
                kpis = qd._build_kpis()
            self.assertAlmostEqual(kpis["today_return"], 0.09, places=2)
            self.assertAlmostEqual(kpis["total_return"], 0.24, places=2)
            # 不得再吃陈旧快照 -0.1% / -0.04%
            self.assertNotAlmostEqual(kpis["total_return"], -0.1, places=2)
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
