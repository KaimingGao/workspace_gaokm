"""Live 开盘 open-pit：今开截面、覆盖闸、禁止写入官方立方。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


def _bars(n: int = 70, *, last_day: str = "2026-10-27") -> list:
    """升序日线；末根 last_day（按日历日回退，够长度即可）。"""
    from datetime import datetime, timedelta

    end = datetime.strptime(last_day, "%Y-%m-%d")
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            i = len(out)
            px = 10.0 + (i % 7) * 0.1
            out.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "open": px,
                    "high": px + 0.2,
                    "low": px - 0.2,
                    "close": px + 0.05,
                    "volume": 1_000_000 + i,
                }
            )
        d -= timedelta(days=1)
    out.reverse()
    return out


class TestOpenPitScore(unittest.TestCase):
    def test_build_items_marks_open_pit_and_close0(self) -> None:
        from core.paper.rebalance.open_pit_score import build_open_pit_feature_items

        codes = ["600887", "000001"]
        hist = {
            "600887": _bars(70, last_day="2026-10-27"),
            "000001": _bars(70, last_day="2026-10-27"),
        }
        # 昨收 / 今开（高低与收盘同口径，避免 yest_close_loc 被 clamp）
        prev = hist["600887"][-1]
        prev["close"] = 28.89
        prev["open"] = 28.80
        prev["high"] = 29.10
        prev["low"] = 28.50
        quotes = {
            "600887": {"success": True, "open": "29.06元", "prev_close": 28.89},
            "000001": {"success": True, "open": 11.0, "prev_close": 10.5},
        }
        with patch(
            "core.paper.rebalance.open_pit_score._load_hist_bars", return_value=hist
        ), patch(
            "core.paper.rebalance.open_pit_score._batch_quotes", return_value=quotes
        ):
            scored, rejected, meta = build_open_pit_feature_items(
                codes,
                cycle_day="2026-10-28",
                universe_codes=codes,
                quotes=quotes,
            )
        self.assertTrue(meta.get("ok"))
        self.assertEqual(len(scored), 2)
        self.assertEqual(rejected, [])
        row = next(it for it in scored if it["stock_code"] == "600887")
        self.assertEqual(row.get("_score_source"), "open_pit")
        self.assertEqual(row.get("lake_score_day"), "2026-10-28")
        self.assertNotIn("lake_score_fallback_from", row)
        self.assertAlmostEqual(float(row.get("day_open")), 29.06, places=4)
        close0 = (row.get("sub_scores") or {}).get("raw_aoo_CLOSE0")
        self.assertIsNotNone(close0)
        self.assertAlmostEqual(float(close0), 28.89 / 29.06, places=5)
        # 风格裸分进 sub_scores，立方盖章后才是 z_oo_*；今开缺口不改写 raw_aoo。
        vp = (row.get("sub_scores") or {}).get("volume_price")
        self.assertIsNotNone(vp)
        self.assertTrue(float(vp) == float(vp))
        self.assertNotIn("features_on", row)
        # τc_rank tip 依赖开盘 z_τc_*；open-pit 不得只剩 raw_atc_*
        tau = row.get("features_tau") or {}
        self.assertIn("z_τc_gap_pct", tau)
        self.assertIn("z_τc_gap_atr", tau)
        self.assertIn("z_τc_theme_day", tau)
        self.assertAlmostEqual(
            float(tau["z_τc_gap_pct"]), (29.06 / 28.89 - 1.0) * 100.0, places=4
        )

    def test_style_window_excludes_cycle_day_and_keeps_aoo(self) -> None:
        from core.paper.rebalance.open_pit_score import build_open_pit_feature_items

        codes = ["600887", "000001"]
        hist = {
            "600887": _bars(70, last_day="2026-10-27"),
            "000001": _bars(70, last_day="2026-10-27"),
        }
        # 会话日 K 不得进风格窗（今收不进 X）
        prev = hist["600887"][-1]
        prev["close"] = 28.89
        prev["open"] = 28.80
        prev["high"] = 29.10
        prev["low"] = 28.50
        hist["600887"] = list(hist["600887"]) + [
            {
                "date": "2026-10-28",
                "open": 29.06,
                "high": 40.0,
                "low": 20.0,
                "close": 39.0,
                "volume": 9_000_000,
            }
        ]
        quotes = {
            "600887": {"success": True, "open": "29.06元", "prev_close": 28.89},
            "000001": {"success": True, "open": 11.0, "prev_close": 10.5},
        }
        seen: dict = {}

        def _fake(window, **kwargs):
            if "dates" not in seen:
                seen["dates"] = [str(b.get("date") or "")[:10] for b in window]
                seen["change"] = (kwargs.get("quote") or {}).get("change_raw")
                seen["names"] = kwargs.get("factor_names")
            row = {str(n): 50.0 for n in (kwargs.get("factor_names") or ())}
            row["volume_price"] = 39.2
            row["raw_volume_price_volume_ratio"] = 1.2
            row["raw_aoo_CLOSE0"] = 9.9
            row["alpha_oo"] = 1.0
            return row

        with patch(
            "core.paper.rebalance.open_pit_score._load_hist_bars", return_value=hist
        ), patch(
            "core.paper.rebalance.open_pit_score._batch_quotes", return_value=quotes
        ), patch(
            "core.research.oo_panel._research_sub_scores", side_effect=_fake
        ):
            scored, rejected, meta = build_open_pit_feature_items(
                codes,
                cycle_day="2026-10-28",
                universe_codes=codes,
                quotes=quotes,
            )
        self.assertTrue(meta.get("ok"))
        self.assertEqual(rejected, [])
        self.assertTrue(seen.get("dates"))
        self.assertTrue(all(d < "2026-10-28" for d in seen["dates"]))
        self.assertEqual(len(seen["dates"]), 61)
        names = set(seen.get("names") or ())
        self.assertNotIn("alpha_oo", names)
        self.assertNotIn("alpha_tc", names)
        self.assertNotIn("alpha_th", names)
        # 与训练面板一致：aoo 已覆盖的重叠列、无真源资金流不进早盘截面
        self.assertNotIn("momentum", names)
        self.assertNotIn("gap_risk", names)
        self.assertNotIn("overheat", names)
        self.assertNotIn("money_flow", names)
        self.assertIn("volume_price", names)
        self.assertAlmostEqual(float(seen["change"]), (29.06 / 28.89 - 1.0) * 100.0, places=3)
        row = next(it for it in scored if it["stock_code"] == "600887")
        subs = row.get("sub_scores") or {}
        self.assertAlmostEqual(float(subs["volume_price"]), 39.2, places=4)
        self.assertNotIn("raw_volume_price_volume_ratio", subs)
        self.assertAlmostEqual(float(subs["raw_aoo_CLOSE0"]), 28.89 / 29.06, places=5)

    def test_style_hist_refuses_short_window(self) -> None:
        from core.paper.rebalance.open_pit_score import _open_style_hist

        self.assertEqual(_open_style_hist(_bars(40)), [])
        self.assertEqual(len(_open_style_hist(_bars(80))), 61)

    def test_missing_style_refuses_pool(self) -> None:
        from core.paper.rebalance.open_pit_score import build_open_pit_feature_items

        codes = ["600887", "000001"]
        hist = {c: _bars(70, last_day="2026-10-27") for c in codes}
        quotes = {
            "600887": {"success": True, "open": 29.06, "prev_close": 28.89},
            "000001": {"success": True, "open": 11.0, "prev_close": 10.5},
        }
        with patch(
            "core.paper.rebalance.open_pit_score._load_hist_bars", return_value=hist
        ), patch(
            "core.paper.rebalance.open_pit_score._batch_quotes", return_value=quotes
        ), patch(
            "core.paper.rebalance.open_pit_score._open_style_scores", return_value={}
        ):
            scored, rejected, meta = build_open_pit_feature_items(
                codes,
                cycle_day="2026-10-28",
                universe_codes=codes,
                quotes=quotes,
            )
        self.assertFalse(meta.get("ok"))
        self.assertEqual(meta.get("reason"), "style_cover_short")
        self.assertEqual(scored, [])
        self.assertEqual(meta.get("n_style_gap"), 2)
        self.assertTrue(all("风格因子不足" in str(r.get("reason")) for r in rejected))

    def test_cover_short_returns_not_ok(self) -> None:
        from core.paper.rebalance.open_pit_score import build_open_pit_feature_items

        codes = [f"{i:06d}" for i in range(10)]
        hist = {c: _bars(70) for c in codes}
        # 只有 2/10 有今开 → 低于 0.5
        quotes = {
            codes[0]: {"success": True, "open": 10.0, "prev_close": 9.5},
            codes[1]: {"success": True, "open": 11.0, "prev_close": 10.5},
        }
        with patch(
            "core.paper.rebalance.open_pit_score._load_hist_bars", return_value=hist
        ):
            scored, _rej, meta = build_open_pit_feature_items(
                codes,
                cycle_day="2026-10-28",
                universe_codes=codes,
                quotes=quotes,
            )
        self.assertFalse(meta.get("ok"))
        self.assertEqual(scored, [])
        self.assertEqual(meta.get("reason"), "open_cover_short")

    def test_score_open_pit_does_not_save_cube(self) -> None:
        from core.paper.rebalance.open_pit_score import score_open_pit_pool

        codes = ["600887", "000001"]
        pool_items = [
            {
                "stock_code": "600887",
                "sub_scores": {"raw_aoo_GAP": 0.01, "raw_aoo_CLOSE0": 0.99},
                "features_tau": {"raw_atc_x": 0.1},
                "_score_source": "open_pit",
                "lake_score_day": "2026-10-28",
            },
            {
                "stock_code": "000001",
                "sub_scores": {"raw_aoo_GAP": -0.01, "raw_aoo_CLOSE0": 1.01},
                "features_tau": {"raw_atc_x": -0.1},
                "_score_source": "open_pit",
                "lake_score_day": "2026-10-28",
            },
        ]
        meta = {
            "ok": True,
            "pool_items": pool_items,
            "n_open": 2,
            "n_items": 2,
        }
        stamp = MagicMock()
        with patch(
            "core.paper.rebalance.open_pit_score.build_open_pit_feature_items",
            return_value=(pool_items, [], meta),
        ), patch(
            "core.research.day_cube.save_day_cube"
        ) as save, patch(
            "core.research.day_cube.remember"
        ) as rem:
            out = score_open_pit_pool(
                codes,
                cycle_day="2026-10-28",
                cube_codes=codes,
                stamp_heads=stamp,
            )
        self.assertIsNotNone(out)
        scored, _rej, cube = out
        self.assertEqual(len(scored), 2)
        self.assertIsNotNone(cube)
        self.assertTrue(all(it.get("_score_source") == "open_pit" for it in scored))
        save.assert_not_called()
        rem.assert_called()
        stamp.assert_called()

    def test_lake_prefers_session_cube_over_pit(self) -> None:
        from core.paper.rebalance.watching_matrix import _score_pool_from_lake

        fake_cube = object()
        cube_hit = (
            [
                {
                    "stock_code": "a",
                    "lake_score_day": "2026-10-28",
                    "_score_source": "day_cube",
                    "y_oo": 0.1,
                },
                {
                    "stock_code": "b",
                    "lake_score_day": "2026-10-28",
                    "_score_source": "day_cube",
                    "y_oo": 0.2,
                },
            ],
            [],
            fake_cube,
        )
        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-10-28"
        ), patch(
            "core.research.materialize.coverage.read_cube_codes",
            return_value=["a", "b"],
        ), patch(
            "core.paper.rebalance.watching_matrix._try_score_pool_from_cube",
            side_effect=[cube_hit],
        ) as cube_fn, patch(
            "core.paper.rebalance.watching_matrix._try_score_pool_from_open_pit"
        ) as pit_fn:
            scored, rejected, got_cube = _score_pool_from_lake(
                ["a", "b"],
                use_minute_tau=False,
                refuse_day_fallback=True,
            )
        self.assertEqual(rejected, [])
        self.assertEqual(len(scored), 2)
        self.assertIs(got_cube, fake_cube)
        pit_fn.assert_not_called()
        cube_fn.assert_called()
        self.assertEqual(cube_fn.call_args.kwargs.get("cycle_day_only"), True)

    def test_lake_uses_open_pit_when_session_cube_missing(self) -> None:
        from core.paper.rebalance.watching_matrix import _score_pool_from_lake

        fake_cube = object()
        pit_hit = (
            [
                {
                    "stock_code": "a",
                    "lake_score_day": "2026-10-28",
                    "_score_source": "open_pit",
                    "y_oo": 0.11,
                },
                {
                    "stock_code": "b",
                    "lake_score_day": "2026-10-28",
                    "_score_source": "open_pit",
                    "y_oo": 0.22,
                },
            ],
            [],
            fake_cube,
        )
        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-10-28"
        ), patch(
            "core.research.materialize.coverage.read_cube_codes",
            return_value=["a", "b"],
        ), patch(
            "core.paper.rebalance.watching_matrix._try_score_pool_from_cube",
            return_value=None,
        ), patch(
            "core.paper.rebalance.watching_matrix._try_score_pool_from_open_pit",
            return_value=pit_hit,
        ):
            scored, rejected, got_cube = _score_pool_from_lake(
                ["a", "b"],
                use_minute_tau=False,
                refuse_day_fallback=True,
            )
        self.assertEqual(rejected, [])
        self.assertIs(got_cube, fake_cube)
        self.assertTrue(all(it.get("_score_source") == "open_pit" for it in scored))
        self.assertTrue(all(it.get("lake_score_day") == "2026-10-28" for it in scored))

    def test_simulate_passes_day_cube_to_rank(self) -> None:
        """调仓预演把出分立方显式交给 oo/τc rank。"""
        from core.paper.rebalance import watching_matrix as wm

        fake_cube = object()
        scored = [
            {"stock_code": "a", "lake_score_day": "2026-10-28", "y_oo": 0.1},
            {"stock_code": "b", "lake_score_day": "2026-10-28", "y_oo": 0.2},
        ]
        seen: dict = {}

        def _attach_oo(items, score_codes=None, day_cube=None, clock="open"):  # noqa: ANN001
            seen["oo_cube"] = day_cube
            seen["oo_clock"] = clock

        def _attach_tc(items, score_codes=None, day_cube=None, clock="open"):  # noqa: ANN001
            seen["tc_cube"] = day_cube

        paper = {"cash": 200000, "holdings": [], "execution": {}}
        with patch.object(
            wm, "_watching_codes", return_value=(["a", "b"], {"name_by_code": {}})
        ), patch.object(
            wm, "_apply_universe_fit_tier_filter", return_value=(["a", "b"], {})
        ), patch.object(
            wm, "_score_pool", return_value=(scored, [], fake_cube)
        ), patch.object(
            wm, "_attach_oo_rank_scores", side_effect=_attach_oo
        ), patch.object(
            wm, "_attach_tc_rank_scores", side_effect=_attach_tc
        ), patch.object(
            wm, "save_live_rank_snapshot"
        ), patch.object(
            wm, "live_rebalance_wants_minute_tau", return_value=False
        ), patch(
            "core.paper.ledger.mark_to_market",
            return_value={"equity": 200000, "cash": 200000},
        ), patch(
            "core.paper.rebalance.rank_lots.get_rank_lot_cfg",
            return_value={
                "top_k": 10,
                "live_fill_clock": "09:30",
                "score_backend": "ridge",
                "fuse_enter": 0.001,
                "fuse_strong": 0.001,
                "lot_base_amount": 10000,
                "lot_strong_amount": 20000,
            },
        ), patch(
            "core.paper.rebalance.rank_lots.plan_rank_lot_day",
            return_value={
                "sell_legs": [],
                "buy_legs": [],
                "hold_rows": [],
                "skips": [],
                "cash_after": 200000,
            },
        ):
            out = wm.simulate_watching_matrix_preview(paper, dry_run=True)
        self.assertTrue(out.get("success") or out.get("ok"))
        self.assertIs(seen.get("oo_cube"), fake_cube)
        self.assertIs(seen.get("tc_cube"), fake_cube)
        self.assertEqual(seen.get("oo_clock"), "open")

    def test_stamp_live_ranks_no_yesterday_snapshot(self) -> None:
        from core.paper.rebalance.watching_matrix import stamp_live_ranks

        rows = [{"stock_code": "600887", "lake_score_day": "2026-10-28"}]
        with patch(
            "core.paper.rebalance.watching_matrix.load_live_rank_snapshot",
            side_effect=lambda as_of=None: (
                {"600887": {"y_oo_rank": 1}} if as_of == "2026-10-27" else None
            ),
        ), patch(
            "core.paper.rebalance.watching_matrix._stamp_ranks_from_lake"
        ) as lake:
            stamp_live_ranks(rows)
        self.assertIsNone(rows[0].get("y_oo_rank"))
        lake.assert_called()


class TestOpenPitTipHint(unittest.TestCase):
    def test_format_eod_as_of_hint_open_pit(self) -> None:
        from pathlib import Path

        tip = Path("web/static/js/score_tooltip.js").read_text(encoding="utf-8")
        self.assertIn('scoreSrc === "open_pit"', tip)
        self.assertIn("今开截面", tip)
        self.assertIn("lake_score_fallback_from", tip)


class TestOpenPitQuoteCacheBust(unittest.TestCase):
    """持仓盯市空今开缓存不得毒死 open-pit 截面。"""

    def tearDown(self) -> None:
        from adapters.market.quote_api import StockAPI

        StockAPI.clear_cache()

    def test_batch_quotes_source_invalidates_before_fetch(self) -> None:
        from pathlib import Path

        src = Path("core/paper/rebalance/open_pit_score.py").read_text(encoding="utf-8")
        self.assertIn("def _batch_quotes(", src)
        self.assertIn("invalidate_codes", src)
        self.assertLess(
            src.index("invalidate_codes"),
            src.index("_batch_query_quotes"),
        )

    def test_empty_open_cache_hit_resolves_no_open(self) -> None:
        import time

        from adapters.market.quote_api import StockAPI
        from core.paper.rebalance.open_pit_score import _open_and_prev

        StockAPI.clear_cache()
        now = time.time()
        with StockAPI._cache_lock:
            StockAPI._cache["sz000636"] = (
                now,
                {
                    "success": True,
                    "stock_code": "000636",
                    "price_raw": 33.12,
                    "prev_close": 33.12,
                    "open": "",
                    "symbol": "sz000636",
                },
            )
        fresh = StockAPI._fresh_cached("sz000636", now=now)
        self.assertIsNotNone(fresh)
        ot, _pc, _src = _open_and_prev(
            cycle_day="2026-11-06", quote=fresh, hist=[]
        )
        self.assertIsNone(ot)

    def test_batch_quotes_invalidates_empty_open_cache(self) -> None:
        import time
        from unittest.mock import patch

        from adapters.market.quote_api import StockAPI
        from core.paper.rebalance.open_pit_score import _batch_quotes, _open_and_prev

        StockAPI.clear_cache()
        now = time.time()
        with StockAPI._cache_lock:
            StockAPI._cache["sz000636"] = (
                now,
                {
                    "success": True,
                    "stock_code": "000636",
                    "price_raw": 33.12,
                    "prev_close": 33.12,
                    "open": "",
                    "symbol": "sz000636",
                },
            )
        good = {
            "000636": {
                "success": True,
                "open": "31.0元",
                "open_raw": 31.0,
                "prev_close": 33.12,
            },
            "000988": {
                "success": True,
                "open": "61.72元",
                "open_raw": 61.72,
                "prev_close": 66.54,
            },
        }
        with patch(
            "core.paper.rebalance.match._batch_query_quotes", return_value=good
        ) as mocked:
            out = _batch_quotes(["000636", "000988"])
        mocked.assert_called_once()
        self.assertNotIn("sz000636", StockAPI._cache)
        ot, _pc, _src = _open_and_prev(
            cycle_day="2026-11-06", quote=out["000636"], hist=[]
        )
        self.assertAlmostEqual(float(ot), 31.0, places=4)

    def test_batch_quotes_retries_codes_still_missing_open(self) -> None:
        from unittest.mock import patch

        from adapters.market.quote_api import StockAPI
        from core.paper.rebalance.open_pit_score import _batch_quotes

        StockAPI.clear_cache()
        first = {
            "000636": {"success": True, "open": "", "price_raw": 33.12},
            "000988": {
                "success": True,
                "open": "61.72元",
                "open_raw": 61.72,
            },
        }
        second = {
            "000636": {
                "success": True,
                "open": "31.0元",
                "open_raw": 31.0,
            },
        }

        def _side_effect(codes):  # noqa: ANN001
            keys = [str(c) for c in codes]
            if keys == ["000636"]:
                return second
            return first

        with patch(
            "core.paper.rebalance.match._batch_query_quotes",
            side_effect=_side_effect,
        ) as mocked:
            out = _batch_quotes(["000636", "000988"])
        self.assertEqual(mocked.call_count, 2)
        self.assertAlmostEqual(float(out["000636"]["open_raw"]), 31.0, places=4)
        self.assertAlmostEqual(float(out["000988"]["open_raw"]), 61.72, places=4)


if __name__ == "__main__":
    unittest.main()
