"""T0 持仓回测、自动任务与盘中记录管理的集成测试。"""

import os
import unittest
from unittest.mock import patch


class TestT0HoldingsVirtualSizing(unittest.TestCase):
    def test_align_daily_bars_to_minute_drops_uncovered(self):
        from quant.research.t0_backtest import _align_daily_bars_to_minute

        bars = [
            {"date": "2026-07-01", "close": 10},
            {"date": "2026-07-02", "close": 11},
            {"date": "2026-07-03", "close": 12},
            {"date": "2026-07-04", "close": 13},
        ]
        mins = {
            "2026-07-02": [{"t": 1}, {"t": 2}],
            "2026-07-03": [{"t": 1}, {"t": 2}],
            "2026-07-04": [{"t": 1}],  # <2 → 不可用
        }
        aligned, meta = _align_daily_bars_to_minute(bars, mins)
        self.assertTrue(meta.get("aligned"))
        self.assertEqual([b["date"] for b in aligned], ["2026-07-02", "2026-07-03"])
        self.assertEqual(meta.get("bars_before"), 4)
        self.assertEqual(meta.get("bars_after"), 2)

    def test_holdings_uses_virtual_shares_and_cash(self):
        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "t0_pnl_total": 1000.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 5,
            "t0_cover_days": 5,
            "skip_days": 10,
            "signal_skip_days": 2,
            "sell_then_buy_days": 5,
            "buy_then_sell_days": 0,
            "sell_then_buy_pnl": 1000.0,
            "buy_then_sell_pnl": 0.0,
            "sell_then_buy_cover_days": 5,
            "buy_then_sell_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 15,
            "missing_minute_days": 0,
            "hold_mv_start": 1_000_000.0,
            "t0_win_days": 3,
            "t0_loss_days": 2,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)) as mocked:
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台", "shares": 200, "cost": 34}],
                lookback=20,
            )
        self.assertEqual(m.T0_BT_VIRTUAL_SHARES, 1_000.0)
        self.assertEqual(m.T0_BT_VIRTUAL_CASH, 200_000.0)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], m.T0_BT_VIRTUAL_SHARES)
        self.assertEqual(kwargs["initial_cash"], m.T0_BT_VIRTUAL_CASH)
        self.assertIsNone(kwargs["initial_cost"])
        self.assertTrue(out.get("virtual_sizing"))
        self.assertTrue(kwargs.get("skip_quote"))
        self.assertEqual(kwargs.get("compare_no_t0"), False)
        self.assertEqual(kwargs.get("stock_name"), "茅台")
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), 0.5)
        self.assertIn("虚拟每票", out.get("scope_label") or "")
        self.assertIn("本金", out.get("scope_label") or "")

    def test_holdings_passes_custom_virtual_sizing(self):
        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "t0_pnl_total": 200.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 1,
            "t0_cover_days": 1,
            "skip_days": 0,
            "signal_skip_days": 0,
            "sell_then_buy_days": 1,
            "buy_then_sell_days": 0,
            "sell_then_buy_pnl": 200.0,
            "buy_then_sell_pnl": 0.0,
            "sell_then_buy_cover_days": 1,
            "buy_then_sell_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 1,
            "missing_minute_days": 0,
            "hold_mv_start": 50_000.0,
            "t0_win_days": 1,
            "t0_loss_days": 0,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)) as mocked:
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台"}],
                lookback=10,
                virtual_shares=2000,
                virtual_cash=300_000,
            )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], 2000)
        self.assertEqual(kwargs["initial_cash"], 300_000)
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), round(200.0 / 300_000 * 100.0, 4))
        self.assertEqual(out["virtual_shares"], 2000)
        self.assertEqual(out["virtual_cash"], 300_000)

    def test_holdings_hit_rate_uses_filled_rounds_including_flat(self):
        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "t0_pnl_total": 10.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 1,
            "t0_cover_days": 1,
            "skip_days": 0,
            "signal_skip_days": 0,
            "sell_then_buy_days": 1,
            "buy_then_sell_days": 0,
            "sell_then_buy_pnl": 10.0,
            "buy_then_sell_pnl": 0.0,
            "sell_then_buy_cover_days": 1,
            "buy_then_sell_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 1,
            "missing_minute_days": 0,
            "hold_mv_start": 50_000.0,
            "t0_win_days": 1,
            "t0_loss_days": 0,
            "t0_hit_n": 1,
            "t0_fill_n": 3,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)):
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台"}],
                lookback=10,
            )
        self.assertEqual(out["t0_hit_n"], 1)
        self.assertEqual(out["t0_fill_n"], 3)
        self.assertAlmostEqual(float(out["t0_win_rate_pct"]), round(100.0 / 3.0, 2))

    def test_holdings_deadline_starts_before_tau_pool(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_holdings)
        dl_at = src.find("t_deadline = time.time()")
        tau_at = src.find("tau_pool = build_tau_pool_by_date")
        self.assertGreater(dl_at, 0)
        self.assertGreater(tau_at, 0)
        self.assertLess(dl_at, tau_at)
        self.assertGreaterEqual(m._HOLDINGS_DEADLINE_SEC, 900.0)
        self.assertLessEqual(m._HOLDINGS_DEADLINE_SEC, 3600.0)
        self.assertIn("DayCube", m._HOLDINGS_DEADLINE_ERR)
        self.assertIn("progress_cb", src)
        self.assertIn("_one_progress", src)
        self.assertIn("scoring_model_role_context(requested)", src)
        self.assertIn("normalize_backtest_model_role", src)
        self.assertIn("准备回测", src)
        self.assertIn("_seed_pool_minutes", src)
        self.assertIn('label="加载截面 5m"', src)
        self.assertIn("_seed_pool_minutes(\n        pool_codes", src)
        seed_src = inspect.getsource(m._seed_pool_minutes)
        self.assertIn("_local_minute_by_date", seed_src)
        self.assertIn("ThreadPoolExecutor", seed_src)
        self.assertNotIn("_fetch_minute_by_date(", seed_src)
        self.assertLess(src.find("准备回测"), src.find("_seed_pool_minutes"))
        self.assertLess(
            src.find("_seed_pool_minutes"), src.find("tau_pool = build_tau_pool_by_date")
        )
        self.assertIn("加载截面 5m", seed_src)
        self.assertIn("seed_peer_minute_bars_map", seed_src)

    def test_seed_pool_minutes_local_cache_only_parallel(self):
        from quant.research import t0_backtest as m

        msgs: list = []
        by_date = {"2026-09-01": [{"time": "09:35", "close": 10}]}
        with patch.object(m, "_local_minute_by_date", return_value=(by_date, {})) as loc:
            with patch.object(m, "_fetch_minute_by_date") as fetched:
                with patch("core.signal.minute_tau_feats.seed_peer_minute_bars_map") as seeded:
                    n = m._seed_pool_minutes(
                        ["600000", "000001", "600000"],
                        "5",
                        progress_cb=lambda _c, _t, msg: msgs.append(msg),
                    )
        self.assertEqual(n, 2)
        self.assertEqual(loc.call_count, 2)
        fetched.assert_not_called()
        seeded.assert_called_once()
        self.assertTrue(any("加载截面 5m" in str(x) for x in msgs))
        self.assertTrue(any("本地" in str(x) for x in msgs))

    def test_t0_job_emits_prepare_before_run(self):
        import inspect

        from quant.services.quant_service_follow import QuantFollowMixin

        src = inspect.getsource(QuantFollowMixin.start_t0_backtest_job)
        self.assertIn('message="准备回测…"', src)
        self.assertLess(src.find("准备回测"), src.find("self.run_t0_backtest("))

    def test_for_code_always_seeds_pool_minutes(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_code)
        self.assertIn("_seed_pool_minutes(", src)
        extras_at = src.find("_seed_pool_minutes(")
        none_at = src.find("if tau_pool is None:")
        self.assertGreater(extras_at, 0)
        self.assertGreater(none_at, extras_at)

    def test_for_code_reuses_holdings_peer_minutes(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_code)
        self.assertIn("reuse_cs", src)
        self.assertIn("peer_minute_seed_count", src)
        self.assertIn("if not reuse_cs:", src)
        self.assertLess(src.find("if not reuse_cs:"), src.find("_seed_pool_minutes("))

    def test_t0_walk_does_not_clear_score_cache_per_stock(self):
        import inspect

        from core.t0 import backtest as m

        src = inspect.getsource(m.backtest_t0_on_bars)
        self.assertIn("scoring_model_role_context(requested)", src)
        self.assertIn("normalize_backtest_model_role", src)
        self.assertNotIn("clear_score_model_cache", src)
        walk_src = inspect.getsource(m._walk_t0)
        self.assertIn("progress_cb", walk_src)
        self.assertIn("from core.research.holdout import current_scoring_model_role", walk_src)
        self.assertIn("current_scoring_model_role()", walk_src)
        sig = inspect.signature(m._walk_t0)
        self.assertIn("progress_cb", sig.parameters)

    def test_fetch_minute_prefers_complete_local_cache(self):
        from quant.research import t0_backtest as m

        # 齐窗日：铺满到 15:00 → 不打远端
        bars = []
        for i in range(80):
            mins = 9 * 60 + 35 + i * 5
            if 11 * 60 + 30 <= mins < 13 * 60:
                continue
            hh, mm = divmod(mins, 60)
            if hh > 15 or (hh == 15 and mm > 0):
                break
            bars.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        meta = {"data_source": "cache", "from_cache": True, "ok": True}
        with patch(
            "adapters.market.minute_history._load_stale_minute",
            return_value=(bars, meta),
        ), patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch("core.ports.market.fetch_minute_bars") as remote:
            by_date, out_meta = m._fetch_minute_by_date("600519", period="5")
        remote.assert_not_called()
        self.assertIn("2026-08-20", by_date)
        self.assertTrue(out_meta.get("from_cache"))
        self.assertTrue(m._minute_day_complete(by_date["2026-08-20"]))

    def test_fetch_minute_refreshes_truncated_local_cache(self):
        """本地只有午前残缺日 → 补拉并用远端覆盖。"""
        from quant.research import t0_backtest as m

        short = []
        for i in range(10):
            mins = 9 * 60 + 35 + i * 5
            hh, mm = divmod(mins, 60)
            short.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        full = []
        for i in range(80):
            mins = 9 * 60 + 35 + i * 5
            if 11 * 60 + 30 <= mins < 13 * 60:
                continue
            hh, mm = divmod(mins, 60)
            if hh > 15 or (hh == 15 and mm > 0):
                break
            full.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        local_meta = {"data_source": "cache", "from_cache": True, "ok": True}
        with patch.object(
            m, "_local_minute_by_date", return_value=({"2026-08-20": short}, local_meta)
        ), patch(
            "core.ports.market.fetch_minute_bars",
            return_value=(full, {"ok": True, "from_cache": False}),
        ) as remote, patch(
            "core.ports.market.group_minute_bars_by_date",
            return_value={"2026-08-20": full},
        ):
            by_date, out_meta = m._fetch_minute_by_date("600519", period="5", timeout_sec=1)
        remote.assert_called_once()
        self.assertFalse(remote.call_args.kwargs.get("use_cache"))
        self.assertTrue(out_meta.get("refreshed_truncated"))
        self.assertTrue(m._minute_day_complete(by_date["2026-08-20"]))
        self.assertGreaterEqual(len(by_date["2026-08-20"]), 40)

    def test_align_prefers_complete_minute_days(self):
        from quant.research.t0_backtest import _align_daily_bars_to_minute

        bars = [
            {"date": "2026-07-01", "close": 10},
            {"date": "2026-07-02", "close": 11},
            {"date": "2026-07-03", "close": 12},
        ]
        short = [{"datetime": "2026-07-01 09:35:00"} for _ in range(10)]
        full = [{"datetime": "2026-07-02 14:55:00"}] + [
            {"datetime": "2026-07-02 09:35:00"} for _ in range(45)
        ]
        # 故意把末根放到列表末尾供 complete 检测
        full = [{"datetime": "2026-07-02 09:35:00"} for _ in range(45)] + [
            {"datetime": "2026-07-02 14:55:00"}
        ]
        other = [{"datetime": "2026-07-03 14:55:00"} for _ in range(40)]
        mins = {
            "2026-07-01": short,
            "2026-07-02": full,
            "2026-07-03": other,
        }
        aligned, meta = _align_daily_bars_to_minute(bars, mins)
        self.assertEqual([b["date"] for b in aligned], ["2026-07-02", "2026-07-03"])
        self.assertEqual(meta.get("complete_minute_days"), 2)

    def test_fetch_minute_remote_when_no_cache(self):
        from quant.research import t0_backtest as m

        with patch.object(m, "_local_minute_by_date", return_value=({}, {"ok": False})), patch(
            "core.ports.market.fetch_minute_bars", return_value=([], {"ok": False})
        ) as remote:
            by_date, meta = m._fetch_minute_by_date("600519", timeout_sec=1)
        remote.assert_called_once()
        self.assertFalse(by_date)
        self.assertIn("无分钟", str(meta.get("error") or ""))

    def test_call_with_timeout_does_not_wait_on_shutdown(self):
        import time

        from quant.research.t0_backtest import _call_with_timeout

        t0 = time.time()
        with self.assertRaises(TimeoutError):
            _call_with_timeout(lambda: time.sleep(30), 0.3)
        self.assertLess(time.time() - t0, 2.0)

    def test_tau_pool_bars_offline_only(self):
        from core.t0 import score_policy as sp

        seen = []

        def fake(code, limit=80, **kw):
            seen.append(dict(kw, code=code, limit=limit))
            return (
                [
                    {"date": "2026-08-01", "open": 10, "close": 10},
                    {"date": "2026-08-02", "open": 11, "close": 11},
                ],
                "cache",
            )

        with patch("core.data.facade.bars_and_source", side_effect=fake):
            out = sp.load_bars_by_code_for_tau_pool(["600519"], limit=40)
        self.assertIn("600519", out)
        self.assertTrue(seen[0].get("offline_ok"))
        self.assertTrue(seen[0].get("offline_only"))


class TestT0AutoWorker(unittest.TestCase):
    def test_worker_tick_waits_outside_session(self):
        from core.t0.auto_worker import T0AutoWorker

        w = T0AutoWorker()
        with patch(
            "core.t0.intraday.session_in_market",
            return_value=(False, "2026-08-24"),
        ), patch(
            "core.t0.auto_worker._session_closed",
            return_value=(False, "2026-08-24"),
        ):
            w._enabled = True
            w._tick()
        self.assertIn("待机", w.status()["last_tick_message"])

    def test_worker_tick_interval_is_five_minutes(self):
        from core.t0.auto_worker import TICK_INTERVAL_SEC
        from core.t0.config import T0_INTRADAY_TICK_SEC

        self.assertEqual(TICK_INTERVAL_SEC, T0_INTRADAY_TICK_SEC)
        self.assertEqual(TICK_INTERVAL_SEC, 300.0)

    def test_set_worker_starts_and_stops(self):
        import tempfile

        from core.t0.auto_worker import t0_auto_worker
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.t0.auto_worker._worker_config_path") as path_mock, patch(
                "services.paper_service.PaperService.run_t0_intraday_tick",
                return_value={"ok": True, "trade_count": 0, "skipped": True, "reason": "mock"},
            ), patch(
                "core.t0.intraday.session_in_market",
                return_value=(False, "2026-08-24"),
            ), patch(
                "core.t0.auto_worker._session_closed",
                return_value=(False, "2026-08-24"),
            ):
                path = os.path.join(tmp, "t0_auto_worker.json")
                path_mock.return_value = path
                paper_path = os.path.join(tmp, "paper.json")
                svc = PaperService(paper_path)
                svc.init()
                out = svc.set_t0_worker(True)
                self.assertTrue(out["worker"]["running"])
                self.assertTrue(out["t0_auto"]["enabled"])
                svc.set_t0_worker(False)
                self.assertFalse(t0_auto_worker.is_running())
                paper = __import__("core.paper", fromlist=["load_paper"]).load_paper(
                    paper_path
                )
                self.assertFalse(paper["rules"]["t0_auto"]["enabled"])


class TestT0AutoLastRunDetail(unittest.TestCase):
    def test_last_run_stores_compact_results(self):
        import tempfile

        from core.paper import load_paper, save_paper
        from services.paper_trades import _compact_t0_result_rows, _write_t0_auto_last_run

        sample = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "date": "2026-08-24",
                "skipped": True,
                "reason": "振幅不足",
                "direction_used": None,
                "trades": [{"side": "sell", "price": 100, "shares": 100, "at": "10:05"}],
                "minute_bars": 48,
            },
            {
                "stock_code": "000001",
                "stock_name": "平安",
                "date": "2026-08-24",
                "skipped": False,
                "direction_used": "sell_then_buy",
                "pnl": 12.5,
                "exposure_pnl": 0,
                "sold_qty": 100,
                "covered_qty": 100,
                "trades": [
                    {"side": "t0_sell", "price": 10.2, "shares": 100, "at": "10:15"},
                    {"side": "t0_buy", "price": 10.0, "shares": 100, "at": "14:30"},
                ],
            },
        ]
        compact = _compact_t0_result_rows(sample)
        self.assertEqual(len(compact), 2)
        self.assertTrue(compact[0].get("skip_category"))
        self.assertEqual(compact[1]["direction"], "sell_then_buy")
        self.assertNotIn("minute_bars", compact[0])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "rules": {},
                "trades": [],
                "operation_log": [],
            }
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": compact[1]["trades"],
                    "pnl_total": 12.5,
                    "results": sample,
                    "execution": {"t0": {"direction": "dual_y", "y_tau_map": "scalp"}},
                },
                source="paper_t0_auto",
            )
            save_paper(paper, path)
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(lr["source"], "paper_t0_auto")
            self.assertEqual(lr["session_date"], "2026-08-24")
            self.assertEqual(lr["rules"]["direction"], "dual_y")
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "000001")

    def test_compact_keeps_close_band_table_fields(self):
        from services.paper_trades import _compact_t0_result_rows

        compact = _compact_t0_result_rows(
            [
                {
                    "stock_code": "600875",
                    "date": "2026-09-10",
                    "skipped": False,
                    "direction_used": "sell_then_buy",
                    "pnl": 0,
                    "minute_bars": 48,
                    "scores": {
                        "y_tau": 0.08,
                        "y_path": 0.29,
                        "score_formula_terms": {"terms": [1, 2, 3]},
                        "factor_coefficients": {"momentum": 0.1},
                    },
                    "close_band_scan": [
                        {
                            "hm": "09:35",
                            "o": 25.5,
                            "l": 25.4,
                            "h": 25.8,
                            "c": 25.7,
                            "c_tau": 25.45,
                            "r_pct": 0.95,
                            "formula_terms_tau": {"terms": [1]},
                            "score_formula_terms_tau": {"terms": [1]},
                            "features_tau": {"gap_pct": 1.0},
                        }
                    ],
                    "forward_trace": [
                        {
                            "time": "09:35",
                            "open": 25.5,
                            "high": 25.8,
                            "low": 25.4,
                            "close": 25.7,
                            "leg1_fill": True,
                            "wait_reason": "drop-me",
                        }
                    ],
                    "t0_slot_results": [
                        {
                            "id": "r1",
                            "hm": "09:35",
                            "direction": "sell_then_buy",
                            "sold_qty": 100,
                            "trades": 1,
                            "close_band": {"c_tau": 25.45, "band_r_pct": 0.95},
                            "_t0_score_snap": {"huge": True},
                        }
                    ],
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                            "ts": "2026-09-10T10:29:15.941",
                            "t0_slot": "r1",
                            "t0_slot_hm": "09:35",
                        }
                    ],
                }
            ]
        )
        row = compact[0]
        self.assertNotIn("minute_bars", row)
        self.assertNotIn("score_formula_terms", row.get("scores") or {})
        self.assertAlmostEqual(float(row["scores"]["y_tau"]), 0.08)
        self.assertEqual(row["close_band_scan"][0]["c_tau"], 25.45)
        self.assertNotIn("formula_terms_tau", row["close_band_scan"][0])
        self.assertNotIn("score_formula_terms_tau", row["close_band_scan"][0])
        self.assertTrue(row["forward_trace"][0]["leg1_fill"])
        self.assertNotIn("wait_reason", row["forward_trace"][0])
        self.assertEqual(row["t0_slot_results"][0]["close_band"]["c_tau"], 25.45)
        self.assertNotIn("_t0_score_snap", row["t0_slot_results"][0])
        self.assertEqual(row["trades"][0]["t0_slot"], "r1")
        self.assertEqual(row["trades"][0]["ts"], "2026-09-10T10:29:15.941")

    def test_patch_last_run_scans_keeps_trades(self):
        from services.paper_trades import _patch_t0_last_run_scans

        paper = {
            "rules": {
                "t0_auto": {
                    "enabled": True,
                    "last_run": {
                        "session_date": "2026-09-16",
                        "trade_count": 2,
                        "pnl_total": -12.5,
                        "results": [
                            {
                                "stock_code": "600183",
                                "pnl": -12.5,
                                "close": 152.6,
                                "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                                "trades": [
                                    {
                                        "side": "t0_buy",
                                        "price": 150.1,
                                        "shares": 100,
                                        "at": "2026-09-16 09:45:00",
                                    }
                                ],
                            }
                        ],
                    },
                }
            }
        }
        self.assertTrue(
            _patch_t0_last_run_scans(
                paper,
                [
                    {
                        "stock_code": "600183",
                        "close": 154.48,
                        "close_band_scan": [{"hm": "09:45", "y_oc": -0.62}],
                    }
                ],
                session_date="2026-09-16",
            )
        )
        row = paper["rules"]["t0_auto"]["last_run"]["results"][0]
        self.assertEqual(row["close_band_scan"][0]["y_oc"], -0.62)
        self.assertEqual(row["close"], 154.48)
        self.assertEqual(row["pnl"], -12.5)
        self.assertEqual(row["trades"][0]["price"], 150.1)

    def test_sync_preview_scans_to_ledgers_patches_last_run_and_snapshot(self):
        """午夜后日历滚日时，仍按成交日把预演 ŷ 写回 last_run / 盘中快照。"""
        import tempfile

        from core.paper import load_paper, save_paper
        from services.paper_trades import _sync_preview_scans_to_ledgers

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "trades": [],
                "operation_log": [],
                "rules": {
                    "t0_auto": {
                        "enabled": True,
                        "last_run": {
                            "session_date": "2026-09-16",
                            "trade_count": 2,
                            "pnl_total": -12.5,
                            "results": [
                                {
                                    "stock_code": "600183",
                                    "date": "2026-09-16",
                                    "close": 152.6,
                                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                                    "scores": {"y_oc": -0.73},
                                    "trades": [
                                        {
                                            "side": "t0_sell",
                                            "price": 150.06,
                                            "shares": 200,
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
            save_paper(paper, path)
            state = {
                "session_date": "2026-09-16",
                "stocks": {
                    "600183": {
                        "day_snapshot": {
                            "date": "2026-09-16",
                            "close": 152.6,
                            "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                            "scores": {"y_oc": -0.73},
                        }
                    }
                },
            }
            saved_state = {}

            def _save_state(st):
                saved_state.clear()
                saved_state.update(st)

            result = {
                "results": [
                    {
                        "stock_code": "600183",
                        "date": "2026-09-16",
                        "close": 154.48,
                        "close_band_scan": [
                            {"hm": "09:45", "y_oc": -0.62, "y_tw": 1}
                        ],
                        "scores": {"y_oc": -0.62},
                    }
                ]
            }
            with patch(
                "core.t0.intraday.load_intraday_state", return_value=state
            ), patch(
                "core.t0.intraday.save_intraday_state", side_effect=_save_state
            ):
                _sync_preview_scans_to_ledgers(
                    result, paper_path=path, session_date="2026-09-17"
                )
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]["results"][0]
            self.assertEqual(lr["close_band_scan"][0]["y_oc"], -0.62)
            self.assertEqual(lr["close"], 154.48)
            self.assertEqual(lr["trades"][0]["price"], 150.06)
            snap = saved_state["stocks"]["600183"]["day_snapshot"]
            self.assertEqual(snap["close_band_scan"][0]["y_oc"], -0.62)
            self.assertEqual(snap["close"], 154.48)

    def test_hydrate_last_run_from_intraday_snapshot(self):
        from services.paper_trades import _hydrate_t0_last_run_display

        lr = {
            "session_date": "2026-09-10",
            "results": [
                {
                    "stock_code": "600875",
                    "date": "2026-09-10",
                    "direction": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                        }
                    ],
                    "pnl": 0,
                }
            ],
        }
        state = {
            "session_date": "2026-09-10",
            "stocks": {
                "600875": {
                    "day_snapshot": {
                        "stock_code": "600875",
                        "open": 25.4,
                        "close": 25.2,
                        "t0_slots_enabled": True,
                        "t0_slot_results": [
                            {
                                "id": "r1",
                                "hm": "09:35",
                                "sold_qty": 100,
                                "trades": 1,
                                "close_band": {"c_tau": 25.45, "band_r_pct": 0.95},
                            }
                        ],
                        "close_band_scan": [
                            {
                                "hm": "09:35",
                                "o": 25.5,
                                "l": 25.4,
                                "h": 25.8,
                                "c": 25.7,
                                "c_tau": 25.45,
                                "r_pct": 0.95,
                            }
                        ],
                        "forward_trace": [
                            {
                                "time": "09:35",
                                "open": 25.5,
                                "high": 25.8,
                                "low": 25.4,
                                "close": 25.7,
                                "leg1_fill": True,
                            }
                        ],
                        "trades": [
                            {
                                "side": "t0_sell",
                                "price": 25.7,
                                "shares": 100,
                                "at": "2026-09-10 09:35:00",
                                "t0_slot": "r1",
                                "t0_slot_hm": "09:35",
                            }
                        ],
                    }
                }
            },
        }
        with patch("core.t0.intraday.load_intraday_state", return_value=state):
            self.assertTrue(_hydrate_t0_last_run_display(lr))
        row = lr["results"][0]
        self.assertEqual(row["t0_slot_results"][0]["hm"], "09:35")
        self.assertEqual(row["close_band_scan"][0]["c"], 25.7)
        self.assertEqual(row["forward_trace"][0]["open"], 25.5)
        self.assertEqual(row["trades"][0]["t0_slot"], "r1")
        self.assertAlmostEqual(float(row["trades"][0]["price"]), 25.7)

    def test_stamp_book_ts_from_paper_trades(self):
        from services.paper_trades import _stamp_book_ts_from_paper

        lr = {
            "session_date": "2026-09-10",
            "results": [
                {
                    "stock_code": "600875",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                            "t0_slot_hm": "09:35",
                        }
                    ],
                }
            ],
        }
        paper = {
            "trades": [
                {
                    "stock_code": "600875",
                    "side": "t0_sell",
                    "price": 25.7,
                    "shares": 100,
                    "at": "2026-09-10 09:35:00",
                    "ts": "2026-09-10T10:29:15.941",
                }
            ]
        }
        self.assertTrue(_stamp_book_ts_from_paper(lr, paper))
        self.assertEqual(lr["results"][0]["trades"][0]["ts"], "2026-09-10T10:29:15.941")
        self.assertFalse(_stamp_book_ts_from_paper(lr, paper))

    def test_last_run_skipped_without_write(self):
        import tempfile

        from core.paper import load_paper, save_paper
        from services.paper_trades import _write_t0_auto_last_run

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {"cash": 100000, "holdings": [], "rules": {}, "trades": [], "operation_log": []}
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": [],
                    "results": [{"stock_code": "600519", "skipped": True, "reason": "振幅不足"}],
                },
                source="paper_t0_auto",
            )
            self.assertNotIn("last_run", paper.get("rules", {}).get("t0_auto", {}))

    def test_last_run_from_flat_trades_when_results_empty(self):
        import tempfile

        from core.paper import load_paper, save_paper
        from services.paper_trades import _write_t0_auto_last_run

        legs = [
            {
                "side": "t0_sell",
                "stock_code": "600519",
                "shares": 100,
                "price": 10.2,
                "at": "2026-08-28 10:05:00",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {"cash": 100000, "holdings": [], "rules": {}, "trades": [], "operation_log": []}
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": legs,
                    "session_date": "2026-08-28",
                    "results": [],
                },
                source="paper_t0_auto",
            )
            save_paper(paper, path)
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertIsNotNone(lr.get("ts"))
            self.assertEqual(len(lr.get("results") or []), 1)
            row = lr["results"][0]
            self.assertEqual(row["stock_code"], "600519")
            self.assertEqual(len(row.get("trades") or []), 1)


class TestDeleteT0Records(unittest.TestCase):
    def test_delete_voids_roundtrip_and_restores_sellable(self):
        import tempfile

        from core.paper import load_paper, save_paper
        from core.paper.tplus1 import sellable_shares
        from services.paper_service import PaperService

        sess = "2026-08-26"
        prev = "2026-08-25"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 99989.91,
                "cost_model": "zero",
                "holdings": [
                    {
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "shares": 100,
                        "cost": 10.0,
                        "lots": [
                            {
                                "shares": 100,
                                "bought_at": f"{sess}T09:40:00",
                                "bought_date": sess,
                            }
                        ],
                        "t0": {"enabled": True, "last_date": sess},
                    }
                ],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.0,
                        "net_cash_delta": 1005.0,
                        "ts": f"{sess}T09:35:00",
                        "at": f"{sess} 09:35",
                    },
                    {
                        "side": "t0_buy",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.09,
                        "net_cash_delta": -1015.09,
                        "ts": f"{sess}T09:40:00",
                        "at": f"{sess} 09:40",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": -10.09,
                            "source": "paper_t0_auto",
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "stock_name": "中国铝业",
                                    "date": sess,
                                    "direction": "sell_then_buy",
                                    "pnl": -10.09,
                                    "trades": [
                                        {
                                            "side": "t0_sell",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:35",
                                        },
                                        {
                                            "side": "t0_buy",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:40",
                                        },
                                    ],
                                }
                            ],
                        },
                    }
                },
                "operation_log": [
                    {
                        "ts": f"{sess}T09:35:00",
                        "type": "sell",
                        "detail": "做T卖出 中国铝业 100股 @ 10.1",
                        "meta": {
                            "stock_code": "601600",
                            "origin": "t0",
                            "source": "paper_t0_auto",
                        },
                    },
                    {
                        "ts": f"{sess}T09:35:00",
                        "type": "t0_batch",
                        "detail": "做T自动·盘中 · 成交 1 笔",
                        "meta": {"origin": "t0", "source": "paper_t0_auto", "trade_count": 1},
                    },
                ],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 100000.0, "total_value": 101000.0},
            ), patch(
                "services.paper_trades.append_snapshot",
                return_value=None,
            ), patch(
                "core.market.calendar.prev_trading_day",
                return_value=prev,
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("voided_legs"), 2)
            self.assertAlmostEqual(out.get("cash_delta"), 10.09, places=2)
            paper2 = load_paper(path)
            self.assertIsNone((paper2.get("rules") or {}).get("t0_auto", {}).get("last_run"))
            self.assertAlmostEqual(float(paper2.get("cash") or 0), 100000.0, places=2)
            h = next(x for x in paper2["holdings"] if x["stock_code"] == "601600")
            self.assertEqual(float(h.get("shares") or 0), 100)
            self.assertEqual(sellable_shares(h, as_of=sess), 100)
            voided = [t for t in paper2["trades"] if t.get("voided")]
            self.assertEqual(len(voided), 2)
            self.assertTrue(any(e.get("type") == "t0_void" for e in paper2.get("operation_log") or []))
            voided_logs = [
                e
                for e in (paper2.get("operation_log") or [])
                if e.get("voided") and e.get("type") in ("sell", "t0_batch")
            ]
            self.assertEqual(len(voided_logs), 2)

    def test_delete_only_last_run_keeps_other_code(self):
        import tempfile

        from core.paper import load_paper, save_paper
        from services.paper_service import PaperService

        sess = "2026-08-26"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000.0,
                "holdings": [],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "shares": 100,
                        "price": 10.0,
                        "net_cash_delta": 1000.0,
                        "ts": f"{sess}T09:35:00",
                    },
                    {
                        "side": "t0_sell",
                        "stock_code": "002352",
                        "shares": 100,
                        "price": 20.0,
                        "net_cash_delta": 2000.0,
                        "ts": f"{sess}T10:00:00",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": 0,
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 10}],
                                },
                                {
                                    "stock_code": "002352",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 20}],
                                },
                            ],
                        },
                    }
                },
                "operation_log": [],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 99000.0, "total_value": 99000.0},
            ), patch("services.paper_trades.append_snapshot", return_value=None), patch(
                "core.market.calendar.prev_trading_day",
                return_value="2026-08-25",
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"))
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "002352")
            paper2 = load_paper(path)
            self.assertTrue(
                any(
                    t.get("stock_code") == "601600" and t.get("voided")
                    for t in paper2["trades"]
                )
            )
            self.assertFalse(
                any(
                    t.get("stock_code") == "002352" and t.get("voided")
                    for t in paper2["trades"]
                )
            )


class TestClearT0Intraday(unittest.TestCase):
    def test_clear_skips_legs_unless_force(self):
        import json
        import tempfile

        from core.t0 import intraday as intraday_mod
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "t0_intraday_state.json")
            paper_path = os.path.join(tmp, "paper.json")
            state = {
                "session_date": "2026-08-26",
                "stocks": {
                    "000651": {"phase": "skipped", "legs_written": 0, "score_locked": True},
                    "002352": {"phase": "after_leg1", "legs_written": 1},
                },
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
            svc = PaperService(paper_path)
            with patch.object(intraday_mod, "_state_path", return_value=state_path), patch(
                "core.t0.intraday.build_intraday_desk_status",
                return_value={"rows": [], "universe_count": 0},
            ):
                out = svc.clear_t0_intraday(clear_all=True, force=False)
                self.assertEqual(out["cleared"], 1)
                self.assertEqual(out["stock_codes"], ["000651"])
                self.assertEqual(out["skipped_with_legs"], ["002352"])
                left = json.load(open(state_path, encoding="utf-8"))
                self.assertIn("002352", left["stocks"])
                self.assertNotIn("000651", left["stocks"])

                out2 = svc.clear_t0_intraday(stock_codes=["002352"], force=True)
                self.assertEqual(out2["cleared"], 1)
                left2 = json.load(open(state_path, encoding="utf-8"))
                self.assertEqual(left2.get("stocks") or {}, {})


class TestT0PreviewMinuteRefresh(unittest.TestCase):
    """手动预演：会话日 5m 停在午前时须 use_cache=False 补拉。"""

    def test_dry_run_fetches_when_session_day_stops_at_1000(self):
        import json
        import tempfile
        from datetime import datetime

        from services.paper_trades import PaperTradesMixin

        day = "2026-09-01"
        paper = {
            "strategy_id": "short_conservative",
            "cash": 2_000_000,
            "holdings": [{"stock_code": "002415", "shares": 500, "cost": 35.0}],
            "trades": [],
            "snapshots": [],
            "operation_log": [],
            "rules": {},
            "cost_model": "zero",
            "updated_at": f"{day}T12:00:00",
        }
        stale = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": 35.5,
                "high": 35.6,
                "low": 35.4,
                "close": 35.5,
            },
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 35.5,
                "high": 35.9,
                "low": 35.5,
                "close": 35.8,
            },
        ]
        fresh = list(stale) + [
            {
                "date": day,
                "datetime": f"{day} 14:55:00",
                "open": 36.0,
                "high": 36.1,
                "low": 35.9,
                "close": 36.0,
            }
        ]
        daily = [
            {
                "date": "2026-08-29",
                "open": 35.0,
                "high": 35.5,
                "low": 34.8,
                "close": 35.2,
            },
            {
                "date": day,
                "open": 35.5,
                "high": 36.2,
                "low": 35.3,
                "close": 36.0,
                "prev_close": 35.2,
            },
        ]
        captured = {}

        def _sim_holdings(*args, **kwargs):
            captured["minute_bars_by_code"] = kwargs.get("minute_bars_by_code")
            return {
                "success": True,
                "trades": [],
                "pnl_total": 0.0,
                "results": [],
                "note": "ok",
            }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _T(PaperTradesMixin):
                pass

            svc = _T()
            svc.path = path
            with patch(
                "core.market.calendar.resolve_session_date", return_value=day
            ), patch(
                "core.market.calendar.is_trading_day", return_value=True
            ), patch(
                "core.signal.session_pit.shanghai_now",
                return_value=datetime(2026, 9, 1, 17, 30, 0),
            ), patch(
                "core.signal.session_pit.asof_session_final", return_value=True
            ), patch(
                "core.t0.intraday.session_in_market", return_value=(False, "closed")
            ), patch(
                "core.data.facade.bars_and_source", return_value=(daily, "test")
            ), patch(
                "core.execution.resolve_effective_execution",
                return_value={
                    "t0": {"enabled": True, "minute_period": "5"},
                    "coupling": {"t0_vs_stance": "independent"},
                    "channel": "paper",
                },
            ), patch(
                "core.execution.strip_execution_meta",
                side_effect=lambda t0: dict(t0 or {}),
            ), patch(
                "core.execution.execution_public_view",
                return_value={"ok": True, "t0": {}},
            ), patch(
                "adapters.market.minute_history.load_minute_cache",
                return_value=(stale, {"from_cache": True}),
            ), patch(
                "core.ports.market.fetch_minute_bars",
                return_value=(fresh, {"ok": True, "data_source": "test"}),
            ) as fetch, patch(
                "core.ports.market.resolve_market_code",
                return_value=("CN", "002415"),
            ), patch(
                "core.t0.rules.simulate_t0_on_holdings", side_effect=_sim_holdings
            ):
                out = svc.simulate_t0(dry_run=True)
            self.assertTrue(out.get("ok"), out)
            fetch.assert_called()
            self.assertFalse(fetch.call_args.kwargs.get("use_cache"))
            mins = (captured.get("minute_bars_by_code") or {}).get("002415") or []
            self.assertGreaterEqual(len(mins), 3)
            self.assertTrue(str(mins[-1].get("datetime") or "").startswith(f"{day} 14:55"))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
