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
        # 昨收 / 今开
        hist["600887"][-1]["close"] = 28.89
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
                "features_co": {"raw_aoo_GAP": 0.01},
                "_score_source": "open_pit",
                "lake_score_day": "2026-10-28",
            },
            {
                "stock_code": "000001",
                "sub_scores": {"raw_aoo_GAP": -0.01, "raw_aoo_CLOSE0": 1.01},
                "features_tau": {"raw_atc_x": -0.1},
                "features_co": {"raw_aoo_GAP": -0.01},
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


if __name__ == "__main__":
    unittest.main()
