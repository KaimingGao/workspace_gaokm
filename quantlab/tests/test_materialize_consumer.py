"""物化是 L1/L2 的生产方；消费开关打开时缺覆盖要提示物化。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime


class MaterializeRequiredPickleTest(unittest.TestCase):
    def test_roundtrip_keeps_payload(self) -> None:
        import pickle

        from core.research.materialize.consumer import MaterializeRequired

        exc = MaterializeRequired(
            {"error": "请先日线物化", "families": ["oo_rank"], "stock_code": "000001"}
        )
        back = pickle.loads(pickle.dumps(exc))
        self.assertIsInstance(back, MaterializeRequired)
        self.assertEqual(back.payload.get("stock_code"), "000001")
        self.assertEqual(back.payload.get("families"), ["oo_rank"])
        self.assertIn("日线物化", str(back))

class InvalidateDecisionTest(unittest.TestCase):
    def test_only_when_fingerprint_changes(self) -> None:
        from core.research.materialize.coverage import l1_invalidate_decision

        self.assertFalse(l1_invalidate_decision(None, "abc"))
        self.assertTrue(l1_invalidate_decision("", "abc"))
        self.assertFalse(l1_invalidate_decision("abc", "abc"))
        self.assertTrue(l1_invalidate_decision("abc", "zzz"))

class ConsumerCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_consumer = os.environ.get("QUANTLAB_MATERIALIZE_CONSUMER")
        self._old_lake = os.environ.get("QUANTLAB_FEATURE_LAKE_DIR")
        self._old_cube = os.environ.get("QUANTLAB_DAY_CUBE_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self.tmp.name, "lake")
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = os.path.join(self.tmp.name, "cubes")
        os.environ.pop("QUANTLAB_MATERIALIZE_CONSUMER", None)

    def tearDown(self) -> None:
        if self._old_consumer is None:
            os.environ.pop("QUANTLAB_MATERIALIZE_CONSUMER", None)
        else:
            os.environ["QUANTLAB_MATERIALIZE_CONSUMER"] = self._old_consumer
        if self._old_lake is None:
            os.environ.pop("QUANTLAB_FEATURE_LAKE_DIR", None)
        else:
            os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = self._old_lake
        if self._old_cube is None:
            os.environ.pop("QUANTLAB_DAY_CUBE_DIR", None)
        else:
            os.environ["QUANTLAB_DAY_CUBE_DIR"] = self._old_cube
        self.tmp.cleanup()

    def test_writer_ignores_concurrent_consumer_flag(self) -> None:
        """物化线程不因别的请求打开的消费开关而拒绝重算。"""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from core.research.materialize.consumer import (
            bind_materialize_writer,
            consumer_read_only,
            context_initargs,
            materialize_writer,
            materialized_consumer,
            raise_if_consumer_rebuild,
        )

        started = threading.Event()
        release = threading.Event()
        seen = {}

        def _hold_consumer() -> None:
            with materialized_consumer():
                seen["holder"] = consumer_read_only()
                started.set()
                release.wait(3)

        holder = threading.Thread(target=_hold_consumer)
        holder.start()
        self.assertTrue(started.wait(2))
        try:
            self.assertTrue(seen["holder"])
            self.assertFalse(consumer_read_only())
            with materialized_consumer():
                pool_seen = {}

                def _read() -> None:
                    pool_seen["ro"] = consumer_read_only()

                with ThreadPoolExecutor(
                    max_workers=1,
                    initializer=bind_materialize_writer,
                    initargs=context_initargs(),
                ) as pool:
                    pool.submit(_read).result()
                self.assertTrue(pool_seen["ro"])
                with materialize_writer():
                    self.assertFalse(consumer_read_only())
                    raise_if_consumer_rebuild("tc", "001391")

                    def _job() -> None:
                        seen["writer"] = consumer_read_only()
                        raise_if_consumer_rebuild("tc", "001391")

                    with ThreadPoolExecutor(
                        max_workers=1,
                        initializer=bind_materialize_writer,
                        initargs=context_initargs(),
                    ) as pool:
                        pool.submit(_job).result()
                    self.assertFalse(seen["writer"])
        finally:
            release.set()
            holder.join(3)

    def test_rebind_clears_stale_writer(self) -> None:
        from core.research.materialize.consumer import (
            bind_materialize_writer,
            consumer_read_only,
            writer_active,
        )

        try:
            bind_materialize_writer(True, False)
            self.assertTrue(writer_active())
            self.assertFalse(consumer_read_only())
            bind_materialize_writer(False, True)
            self.assertFalse(writer_active())
            self.assertTrue(consumer_read_only())
        finally:
            bind_materialize_writer(False, False)
        self.assertFalse(writer_active())
        self.assertFalse(consumer_read_only())

    def test_off_does_not_block_collect(self) -> None:
        from core.research.materialize.coverage import enforce_consumer_coverage

        enforce_consumer_coverage(
            [("000001", [{"date": "2024-01-02"}]), ("000002", [{"date": "2024-01-02"}])],
            families=["oo"],
            clocks=["open"],
        )

    def test_l2_cube_key_ignores_shorter_trade_list(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.coverage import assess_coverage

        days = ["2099-01-04", "2099-01-05"]

        def _status(day, codes, clock="open"):
            return "ok" if len(list(codes)) == 3 else "miss"

        def _dates(fam, code):
            return set(days)

        with patch(
            "core.research.day_cube.day_cube_status", side_effect=_status
        ), patch(
            "core.research.feature_lake.load_stock_dates", side_effect=_dates
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ):
            report = assess_coverage(
                ["a", "b"],
                days,
                families=["oo"],
                clocks=["open"],
                cube_codes=["a", "b", "c"],
            )
        self.assertTrue(report.get("ok"), report)

    def test_replay_gap_uses_fit_universe(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.consumer import bind_materialize_writer
        from core.research.materialize.coverage import replay_coverage_gap

        days = ["2099-01-04", "2099-01-05"]
        bars = {code: [{"date": day} for day in days] for code in ("a", "b")}

        def _status(_day, codes, clock="open"):
            return "ok" if len(list(codes)) == 3 else "miss"

        def _dates(_fam, _code):
            return set(days)

        bind_materialize_writer(False, True)
        try:
            with patch(
                "core.research.day_cube.day_cube_status", side_effect=_status
            ), patch(
                "core.research.feature_lake.load_stock_dates", side_effect=_dates
            ), patch(
                "core.research.materialize.coverage._other_hash_days", return_value=set()
            ), patch(
                "core.research.materialize.cube_z.universe_codes",
                return_value=["a", "b", "c"],
            ):
                gap = replay_coverage_gap(
                    bars, days, score_from=0, fill_clock="09:30"
                )
        finally:
            bind_materialize_writer(False, False)
        self.assertIsNone(gap)

    def test_scorable_dates_drop_warmup_and_thin_names(self) -> None:
        from core.research.materialize.coverage import scorable_bar_dates

        items = [
            (
                "a",
                [
                    {"date": "2024-01-02"},
                    {"date": "2024-01-03"},
                    {"date": "2024-01-04"},
                    {"date": "2024-01-05"},
                ],
            ),
            (
                "b",
                [
                    {"date": "2024-01-02"},
                    {"date": "2024-01-03"},
                    {"date": "2024-01-04"},
                    {"date": "2024-01-05"},
                ],
            ),
            ("c", [{"date": "2019-11-26"}]),
        ]
        _codes, dates = scorable_bar_dates(items, min_history=3, min_names=2)
        self.assertEqual(dates, ["2024-01-04", "2024-01-05"])

    def test_warmup_days_are_not_coverage_gaps(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.consumer import materialized_consumer
        from core.research.materialize.coverage import enforce_consumer_coverage

        items = [
            (
                "a",
                [
                    {"date": "2024-01-02"},
                    {"date": "2024-01-03"},
                    {"date": "2024-01-04"},
                ],
            ),
            (
                "b",
                [
                    {"date": "2024-01-02"},
                    {"date": "2024-01-03"},
                    {"date": "2024-01-04"},
                ],
            ),
        ]
        seen = {}

        def _dates(_fam, _code):
            return {"2024-01-04"}

        def _status(day, _codes, clock="open"):
            seen["day"] = day
            return "ok" if day == "2024-01-04" else "miss"

        with materialized_consumer(), patch(
            "core.research.feature_lake.load_stock_dates", side_effect=_dates
        ), patch(
            "core.research.day_cube.day_cube_status", side_effect=_status
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ), patch(
            "core.research.materialize.coverage.read_cube_codes", return_value=[]
        ):
            enforce_consumer_coverage(
                items, families=["oo"], clocks=["open"], min_history=3
            )
        self.assertEqual(seen.get("day"), "2024-01-04")

    def test_coverage_drops_pad_boundary_day(self) -> None:
        """加载 = lookback + 垫窗时，可算日多出的最早一天不要求立方。"""
        from unittest.mock import patch

        from core.research.materialize.consumer import materialized_consumer
        from core.research.materialize.coverage import enforce_consumer_coverage

        days = [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
        ]
        items = [(code, [{"date": day} for day in days]) for code in ("a", "b")]
        seen = []

        def _dates(_fam, _code):
            return set(days)

        def _status(day, _codes, clock="open"):
            seen.append(day)
            return "ok"

        with materialized_consumer(), patch(
            "core.research.feature_lake.load_stock_dates", side_effect=_dates
        ), patch(
            "core.research.day_cube.day_cube_status", side_effect=_status
        ), patch(
            "core.research.materialize.coverage._other_hash_days", return_value=set()
        ), patch(
            "core.research.materialize.coverage.read_cube_codes", return_value=[]
        ):
            enforce_consumer_coverage(
                items, families=["oo"], clocks=["open"], min_history=3
            )
        # 5 根、垫窗 3 → 训练日 2；可算日是后 3 天，再丢掉最早的边界日
        self.assertEqual(seen, ["2024-01-05", "2024-01-08"])

    def test_coverage_error_keeps_each_span(self) -> None:
        from core.research.materialize.coverage import _coverage_error_bits

        text = _coverage_error_bits(
            [
                {
                    "family": "oo",
                    "n_days": 1,
                    "date_from": "2026-10-19",
                    "date_to": "2026-10-19",
                },
                {
                    "layer": "l2",
                    "clock": "open",
                    "n_days": 10,
                    "date_from": "2026-09-29",
                    "date_to": "2026-10-19",
                },
            ]
        )
        self.assertEqual(
            text,
            "oo 缺 1 日（2026-10-19），立方 open 缺 10 日（2026-09-29 … 2026-10-19）",
        )

    def test_on_missing_prompts_materialize(self) -> None:
        from core.research.materialize.consumer import MaterializeRequired, materialized_consumer
        from core.research.materialize.coverage import enforce_consumer_coverage

        items = [
            ("000001", [{"date": "2099-01-04"}, {"date": "2099-01-05"}]),
            ("000002", [{"date": "2099-01-04"}, {"date": "2099-01-05"}]),
        ]
        with materialized_consumer(), self.assertRaises(MaterializeRequired) as ctx:
            enforce_consumer_coverage(items, families=["oo"], clocks=["open"])
        payload = ctx.exception.to_dict()
        self.assertEqual(payload.get("error_code"), "materialize_required")
        self.assertIn("物化", str(payload.get("error") or ""))
        self.assertFalse(payload.get("success"))

    def test_attach_slim_keeps_matrix(self) -> None:
        import numpy as np

        from core.research.day_cube import attach_slim, build_day_cube, load_day_cube

        day = "2024-01-03"
        feats = {
            "000001": {"a": 1.0, "b": 2.0},
            "000002": {"a": 3.0, "b": 4.0},
        }
        cube = build_day_cube(day, feats, clock="open")
        self.assertIsNotNone(cube)
        from core.research.day_cube import save_day_cube

        self.assertTrue(save_day_cube(cube))
        self.assertTrue(
            attach_slim(
                day,
                ["000001", "000002"],
                clock="open",
                slim={"gaps_by_code": {"000001": 1.5}},
            )
        )
        loaded = load_day_cube(day, ["000001", "000002"], clock="open")
        self.assertIsNotNone(loaded)
        self.assertEqual(list(loaded.names), ["a", "b"])
        self.assertEqual(loaded.slim.get("gaps_by_code", {}).get("000001"), 1.5)
        self.assertEqual(loaded.X_raw.shape, (2, 2))
        self.assertTrue(np.isfinite(loaded.X_raw).all())

    def test_watching_insights_prompt_materialize(self) -> None:
        from unittest.mock import patch

        from core.research.materialize.consumer import materialized_consumer
        from core.watching.insights import build_watching_insights

        with patch(
            "core.research.day_cube.day_cube_status", return_value="miss"
        ), patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(["000001", "000002"], {}),
        ), materialized_consumer():
            out = build_watching_insights(["000001", "000002"])
        self.assertEqual(out.get("error_code"), "materialize_required")
        self.assertIn("物化", str(out.get("error") or ""))
        self.assertEqual(out.get("items"), [])

    def test_live_score_pool_prompts_materialize(self) -> None:
        from unittest.mock import patch

        from core.paper.rebalance.watching_matrix import _score_pool
        from core.research.materialize.consumer import MaterializeRequired, materialized_consumer

        with patch("core.research.day_cube.day_cube_status", return_value="miss"), materialized_consumer():
            with self.assertRaises(MaterializeRequired) as ctx:
                _score_pool(["000001", "000002"], offline_only=True, use_minute_tau=False)
        payload = ctx.exception.to_dict()
        self.assertEqual(payload.get("error_code"), "materialize_required")
        self.assertIn("日线物化", str(payload.get("error") or ""))
        self.assertIn("日线物化", str(payload.get("hint") or ""))

    def test_minute_clock_maps_to_grid(self) -> None:
        from core.paper.rebalance.watching_matrix import _consumer_score_clock

        self.assertEqual(_consumer_score_clock(False, "10:00"), "open")
        self.assertEqual(_consumer_score_clock(True, "10:00"), "1000")
        self.assertEqual(_consumer_score_clock(True, "09:30"), "open")

    def test_score_pool_from_lake_uses_oo_cycle_date(self) -> None:
        """出分按候选日试覆盖；全缺才 materialize_required。"""
        from unittest.mock import patch

        from core.paper.rebalance.watching_matrix import _score_pool_from_lake
        from core.research.materialize.consumer import MaterializeRequired

        seen: list = []

        def _fake_assess(codes, days, **kwargs):  # noqa: ANN001
            seen.extend(list(days or []))
            return {
                "ok": False,
                "error_code": "materialize_required",
                "error": "请先日线物化 L1/L2",
                "hint": "日线物化",
            }

        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-10-20"
        ), patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-10-20",
        ), patch(
            "core.market.calendar.prev_trading_day", return_value="2026-10-19"
        ), patch(
            "core.research.materialize.coverage.assess_coverage", side_effect=_fake_assess
        ), patch(
            "core.research.materialize.coverage.read_cube_codes", return_value=[]
        ):
            with self.assertRaises(MaterializeRequired) as ctx:
                _score_pool_from_lake(
                    ["300750", "002594"],
                    use_minute_tau=False,
                    tau_clock="03:00",
                )
        self.assertEqual(seen, ["2026-10-20", "2026-10-19"])
        self.assertEqual(
            (ctx.exception.to_dict() or {}).get("tried_days"),
            ["2026-10-20", "2026-10-19"],
        )

    def test_score_pool_from_lake_falls_back_to_prev_day(self) -> None:
        """盘中周期日=今日但 open 湖未齐时，回退上一交易日出分。"""
        from unittest.mock import patch

        from core.paper.rebalance.watching_matrix import _score_pool_from_lake

        def _fake_assess(codes, days, **kwargs):  # noqa: ANN001
            day = (days or [""])[0]
            if day == "2026-10-20":
                return {"ok": True, "success": True}
            return {
                "ok": False,
                "error_code": "materialize_required",
                "error": f"缺 {day}",
                "hint": "日线物化",
            }

        def _assemble(code, day, **kwargs):  # noqa: ANN001
            return {"stock_code": code, "as_of": day}

        with patch(
            "core.signal.session_pit.oo_cycle_date", return_value="2026-10-21"
        ), patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-10-20",
        ), patch(
            "core.market.calendar.prev_trading_day", return_value="2026-10-20"
        ), patch(
            "core.research.materialize.coverage.assess_coverage", side_effect=_fake_assess
        ), patch(
            "core.research.materialize.coverage.read_cube_codes", return_value=["a", "b"]
        ), patch(
            "core.research.feature_lake.load_day_rows", return_value={}
        ), patch(
            "core.research.lake_replay.assemble_item_from_lake_rows",
            side_effect=_assemble,
        ), patch(
            "core.research.batch_score.ensure_day_cube_for_items", return_value=object()
        ), patch(
            "core.signal.cross_section_rescore.rescore_fitted_cross_section"
        ):
            scored, rejected = _score_pool_from_lake(
                ["300750", "002594"],
                use_minute_tau=False,
                tau_clock="11:00",
            )
        self.assertEqual(rejected, [])
        self.assertEqual(len(scored), 2)
        self.assertTrue(all(it.get("lake_score_day") == "2026-10-20" for it in scored))
        self.assertTrue(
            all(it.get("lake_score_fallback_from") == "2026-10-21" for it in scored)
        )

    def test_lake_score_day_candidates_dedupe(self) -> None:
        from unittest.mock import patch

        from core.paper.rebalance.watching_matrix import _lake_score_day_candidates

        with patch(
            "core.market.calendar.expected_latest_daily_bar_date",
            return_value="2026-10-20",
        ), patch(
            "core.market.calendar.prev_trading_day", return_value="2026-10-20"
        ):
            self.assertEqual(
                _lake_score_day_candidates("2026-10-21"),
                ["2026-10-21", "2026-10-20"],
            )

    def test_recent_trading_days_are_weekdays(self) -> None:
        from core.research.materialize.coverage import recent_trading_days

        days = recent_trading_days(5, end="2024-01-08")
        self.assertEqual(len(days), 5)
        for day in days:
            wd = datetime.strptime(day, "%Y-%m-%d").weekday()
            self.assertLess(wd, 5)

if __name__ == "__main__":
    unittest.main()
