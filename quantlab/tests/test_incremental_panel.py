"""尾窗增量：stem 命中、merge、keep_dates。"""

from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np


def _bars(n: int = 80, start: float = 10.0, seed: int = 0):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out

class TestIncrementalPanel(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmpdir.name, "l1")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_stem_detects_tail_append(self) -> None:
        from core.research.incremental_panel import stem_extra_meta, tail_append_days

        bars = _bars(40)
        meta = stem_extra_meta(bars)
        extended = list(bars)
        extended.append(
            {
                "date": "2024-03-15",
                "open": 12.0,
                "high": 12.2,
                "low": 11.8,
                "close": 12.1,
                "volume": 2_000_000,
            }
        )
        self.assertEqual(tail_append_days(extended, meta), 1)
        # 多日追加
        extended2 = list(extended)
        extended2.append(
            {
                "date": "2024-03-16",
                "open": 12.2,
                "high": 12.4,
                "low": 12.0,
                "close": 12.3,
                "volume": 2_100_000,
            }
        )
        self.assertEqual(tail_append_days(extended2, meta), 2)
        # 只改末根
        tweaked = list(bars)
        tweaked[-1] = dict(tweaked[-1])
        tweaked[-1]["close"] = float(tweaked[-1]["close"]) + 0.5
        self.assertEqual(tail_append_days(tweaked, meta), 0)
        # 改中间根再追加 → 非纯尾
        mid = list(bars)
        mid[10] = dict(mid[10])
        mid[10]["close"] = float(mid[10]["close"]) + 5.0
        mid.append(extended[-1])
        self.assertIsNone(tail_append_days(mid, meta))

    def test_lake_row_covers_bar_end(self) -> None:
        from core.research.incremental_panel import lake_row_covers_bar_end

        bars = _bars(4)
        end = bars[-1]["date"]
        self.assertTrue(lake_row_covers_bar_end([end], bars, {}))
        self.assertFalse(lake_row_covers_bar_end([bars[0]["date"]], bars, {}))
        self.assertTrue(
            lake_row_covers_bar_end(
                [bars[0]["date"]],
                bars,
                {"scored_through": end},
            )
        )

    def test_merge_matrix_rows(self) -> None:
        from core.research.incremental_panel import merge_matrix_rows

        old_X = np.asarray([[1.0], [2.0], [3.0]])
        old_y = np.asarray([0.1, 0.2, 0.3])
        new_X = np.asarray([[30.0], [40.0]])
        new_y = np.asarray([3.0, 4.0])
        X, y, names, dates = merge_matrix_rows(
            old_X,
            old_y,
            ["2024-01-01", "2024-01-02", "2024-01-03"],
            ["f"],
            new_X,
            new_y,
            ["2024-01-03", "2024-01-04"],
            ["f"],
            keep_from="2024-01-03",
        )
        self.assertEqual(dates, ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
        np.testing.assert_allclose(X.ravel(), [1.0, 2.0, 30.0, 40.0])
        np.testing.assert_allclose(y, [0.1, 0.2, 3.0, 4.0])
        self.assertEqual(names, ["f"])

    def test_history_extend_merges_prefix(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta, try_incremental_merge
        from core.research.lake_bridge import FAMILY_OO
        from core.research.panel_fingerprint import make_fingerprint

        short = _bars(40)
        meta = stem_extra_meta(short)
        stored_fp = make_fingerprint(bars_fp=meta["bars_fp"], horizon=1)
        old_dates = [str(b["date"])[:10] for b in short[-10:]]
        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=old_dates,
            X=np.asarray([[float(i)] for i in range(len(old_dates))]),
            names=["f"],
            fingerprint=stored_fp,
            y=np.asarray([0.01 * i for i in range(len(old_dates))]),
            extra_meta=meta,
        )
        # 前面多 20 根：旧 bars 是新 bars 的后缀
        prefix = []
        for i in range(20):
            prefix.append(
                {
                    "date": f"2023-12-{(i % 28) + 1:02d}",
                    "open": 8.0 + i * 0.01,
                    "high": 8.1 + i * 0.01,
                    "low": 7.9 + i * 0.01,
                    "close": 8.05 + i * 0.01,
                    "volume": 500_000 + i,
                }
            )
        longer = prefix + list(short)
        seen = []

        def _tail(keep):
            seen.append(list(keep))
            # 只交回 keep 里相对旧首日更早的几天
            early = [d for d in keep if d < old_dates[0]][:3]
            if not early:
                early = [keep[0]]
            return (
                np.asarray([[100.0 + i] for i in range(len(early))]),
                np.asarray([1.0 + i * 0.1 for i in range(len(early))]),
                ["f"],
                early,
            )

        def _spec(bars_fp: str) -> str:
            return make_fingerprint(bars_fp=bars_fp, horizon=1)

        merged = try_incremental_merge(
            "000001",
            longer,
            pad=4,
            collect_tail=_tail,
            family=FAMILY_OO,
            spec_fp=_spec,
        )
        self.assertIsNotNone(merged)
        assert merged is not None
        _x, _y, _names, dates = merged
        self.assertTrue(seen)
        self.assertLess(dates[0], old_dates[0])
        self.assertEqual(dates[-1], old_dates[-1])
        self.assertGreater(len(dates), len(old_dates))

    def test_fingerprint_mismatch_skips_merge(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta, try_incremental_merge
        from core.research.lake_bridge import FAMILY_OO_RANK

        bars = _bars(40)
        write_stock_panel(
            FAMILY_OO_RANK,
            "000001",
            dates=["2024-01-01", "2024-01-02"],
            X=np.asarray([[1.0], [2.0]]),
            names=["f"],
            fingerprint="absolute",
            y=np.asarray([0.1, 0.2]),
            extra_meta=stem_extra_meta(bars),
        )

        def _tail(_keep):
            raise AssertionError("口径不同不能接尾窗")

        def _spec(bars_fp: str) -> str:
            from core.research.panel_fingerprint import make_fingerprint

            return make_fingerprint(bars_fp=bars_fp, horizon=1, excess_mode="index")

        self.assertIsNone(
            try_incremental_merge(
                "000001",
                bars,
                pad=8,
                collect_tail=_tail,
                family=FAMILY_OO_RANK,
                spec_fp=_spec,
            )
        )

    def test_same_spec_tail_still_merges(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta, try_incremental_merge
        from core.research.lake_bridge import FAMILY_OO
        from core.research.panel_fingerprint import make_fingerprint

        bars = _bars(40)
        meta = stem_extra_meta(bars)
        stored_fp = make_fingerprint(bars_fp=meta["bars_fp"], horizon=1)
        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=["2024-01-01", "2024-01-02"],
            X=np.asarray([[1.0], [2.0]]),
            names=["f"],
            fingerprint=stored_fp,
            y=np.asarray([0.1, 0.2]),
            extra_meta=meta,
        )
        extended = list(bars)
        extended.append(
            {
                "date": "2024-03-15",
                "open": 12.0,
                "high": 12.2,
                "low": 11.8,
                "close": 12.1,
                "volume": 2_000_000,
            }
        )
        seen = []

        def _tail(keep):
            seen.append(list(keep))
            return (
                np.asarray([[9.0]]),
                np.asarray([0.9]),
                ["f"],
                ["2024-03-15"],
            )

        def _spec(bars_fp: str) -> str:
            return make_fingerprint(bars_fp=bars_fp, horizon=1)

        merged = try_incremental_merge(
            "000001",
            extended,
            pad=8,
            collect_tail=_tail,
            family=FAMILY_OO,
            spec_fp=_spec,
        )
        self.assertIsNotNone(merged)
        assert merged is not None
        _x, y, _names, dates = merged
        self.assertTrue(seen)
        self.assertIn("2024-03-15", dates)
        self.assertEqual(float(y[-1]), 0.9)

    def test_bad_spec_or_shape_does_not_merge(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta, try_incremental_merge
        from core.research.lake_bridge import FAMILY_OO
        from core.research.materialize.consumer import MaterializeRequired
        from core.research.panel_fingerprint import make_fingerprint

        bars = _bars(40)
        meta = stem_extra_meta(bars)
        stored_fp = make_fingerprint(bars_fp=meta["bars_fp"], horizon=1)
        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=["2024-01-01", "2024-01-02"],
            X=np.asarray([[1.0], [2.0]]),
            names=["f"],
            fingerprint=stored_fp,
            y=np.asarray([0.1, 0.2]),
            extra_meta=meta,
        )

        def _tail(_keep):
            raise AssertionError("坏口径或坏形状不能接尾窗")

        def _boom(_bars_fp: str) -> str:
            raise RuntimeError("spec")

        self.assertIsNone(
            try_incremental_merge(
                "000001",
                bars,
                pad=8,
                collect_tail=_tail,
                family=FAMILY_OO,
                spec_fp=_boom,
            )
        )

        def _required(_keep):
            raise MaterializeRequired({"error": "请先日线物化", "families": ["oo"]})

        def _spec(bars_fp: str) -> str:
            return make_fingerprint(bars_fp=bars_fp, horizon=1)

        with self.assertRaises(MaterializeRequired):
            try_incremental_merge(
                "000001",
                bars,
                pad=8,
                collect_tail=_required,
                family=FAMILY_OO,
                spec_fp=_spec,
            )

        def _bad_tail(_keep):
            return (
                np.asarray([[1.0], [2.0]]),
                np.asarray([0.1]),
                ["f"],
                ["2024-01-03"],
            )

        self.assertIsNone(
            try_incremental_merge(
                "000001",
                bars,
                pad=8,
                collect_tail=_bad_tail,
                family=FAMILY_OO,
                spec_fp=_spec,
            )
        )

    def test_oo_stock_job_incremental_tail(self) -> None:
        from core.research.feature_lake import load_stock_panel
        from core.research.lake_bridge import FAMILY_OO
        from core.research.materialize.consumer import materialize_writer
        from core.research.oo_ridge_compact import _oo_stock_job
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

        bars = _bars(90, seed=3)
        code = "600123"
        horizon = 1
        # 全量写一次 → L1（sync 仅在物化写入上下文落盘）
        with materialize_writer():
            x0, y0, names0, dates0 = _oo_stock_job((code, bars, horizon, False))
        self.assertGreater(len(dates0), 10)
        fp0 = make_fingerprint(
            bars_fp=bars_fingerprint(bars),
            horizon=horizon,
            ridge_core_only=0,
        )
        hit = load_stock_panel(FAMILY_OO, code, fingerprint=fp0)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertIn("stem_fp", hit.get("_meta") or {})
        # 只追加一日
        bars2 = list(bars)
        last = dict(bars[-1])
        bars2.append(
            {
                "date": "2024-04-20",
                "open": float(last["close"]) * 1.01,
                "high": float(last["close"]) * 1.02,
                "low": float(last["close"]) * 0.99,
                "close": float(last["close"]) * 1.005,
                "volume": 3_000_000,
            }
        )
        with materialize_writer():
            x1, y1, names1, dates1 = _oo_stock_job((code, bars2, horizon, False))
        self.assertEqual(names0, names1)
        self.assertGreaterEqual(len(dates1), len(dates0))
        self.assertTrue(set(dates0).issubset(set(dates1)) or dates1[-1] >= dates0[-1])

class TestLakeReplay(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        from core.research.lake_replay import clear_coverage_cache

        clear_coverage_cache()

    def tearDown(self) -> None:
        from core.research.lake_replay import clear_coverage_cache

        clear_coverage_cache()
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def _seed_l1(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC

        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=["2024-05-10"],
            X=np.asarray([[1.0, 2.0]]),
            names=["z_oo_a", "raw_aoo_b"],
            fingerprint="t",
        )
        write_stock_panel(
            FAMILY_TC,
            "000001",
            dates=["2024-05-10"],
            X=np.asarray([[0.5]]),
            names=["gap_pct"],
            fingerprint="t",
        )
        write_stock_panel(
            FAMILY_CO,
            "000001",
            dates=["2024-05-10"],
            X=np.asarray([[0.1, 0.2]]),
            names=["z_co_a", "raw_aco_b"],
            fingerprint="t",
        )

    def test_try_score_item_from_lake(self) -> None:
        from core.research.lake_replay import try_score_item_from_lake

        self._seed_l1()
        item = try_score_item_from_lake(
            "000001",
            "2024-05-10",
            quote={"price": 10.0, "open": 10.0},
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["_score_source"], "feature_lake")
        self.assertIn("sub_scores", item)
        self.assertIn("features_tau", item)
        self.assertIn("features_co", item)
        self.assertIsNone(try_score_item_from_lake("000001", "2024-05-11"))

    def test_prefetch_serves_day_without_reread(self) -> None:
        from core.research import feature_lake as lake
        from core.research.lake_replay import (
            clear_coverage_cache,
            prefetch_l1_coverage,
            try_score_item_from_lake,
        )

        self._seed_l1()
        prefetch_l1_coverage(["000001"])
        # 删盘后仍能从进程缓存灌行
        lake.invalidate_code("oo", "000001")
        lake.invalidate_code("tc", "000001")
        lake.invalidate_code("co", "000001")
        item = try_score_item_from_lake("000001", "2024-05-10")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["_score_source"], "feature_lake")
        self.assertIn("sub_scores", item)
        clear_coverage_cache()
        self.assertIsNone(try_score_item_from_lake("000001", "2024-05-10"))

    def test_score_open_day_entries_skips_window_on_hit(self) -> None:
        from unittest.mock import MagicMock

        from core.research.lake_replay import prefetch_l1_coverage, score_open_day_entries

        self._seed_l1()
        prefetch_l1_coverage(["000001"])
        dates = ["2024-05-09", "2024-05-10"]
        bars = [
            {
                "date": "2024-05-09",
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.2,
                "volume": 1e6,
            },
            {
                "date": "2024-05-10",
                "open": 10.3,
                "high": 10.8,
                "low": 10.0,
                "close": 10.5,
                "volume": 1e6,
            },
        ]
        date_maps = {"000001": {b["date"]: b for b in bars}}
        stock_bars = {"000001": bars}
        window_fn = MagicMock(return_value=bars[:1])

        def _quote(bar, prev_bar=None, px_field="open"):
            if not bar:
                return {}
            return {
                "price": float(bar.get(px_field) or 0),
                "open": float(bar.get("open") or 0),
                "prev_close": float((prev_bar or {}).get("close") or 0) or None,
            }

        score_window = MagicMock(return_value=None)
        import core.signal.cross_section_batch as csb

        prev = csb.score_window_as_item
        csb.score_window_as_item = score_window
        try:
            entries, quotes, windows, hits, miss = score_open_day_entries(
                stock_bars=stock_bars,
                dates=dates,
                date_maps=date_maps,
                day_i=1,
                max_window=60,
                horizon_days=1,
                cfg={},
                required_factor_keys=None,
                window_for_code=window_fn,
                mock_quote_from_bar=_quote,
            )
        finally:
            csb.score_window_as_item = prev

        self.assertEqual(hits, 1)
        self.assertEqual(miss, 0)
        self.assertEqual(score_window.call_count, 0)
        self.assertEqual(window_fn.call_count, 0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["_score_source"], "feature_lake")
        self.assertEqual(windows.get("000001"), [])

    def test_score_open_day_entries_tc_only_still_scores_window(self) -> None:
        """仅 tc/co 有湖日、无 oo：不得当命中（否则无 sub_scores → ŷ_oo/oo_rank 空）。"""
        from unittest.mock import MagicMock

        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import FAMILY_CO, FAMILY_TC
        from core.research.lake_replay import prefetch_l1_coverage, score_open_day_entries

        write_stock_panel(
            FAMILY_TC,
            "000001",
            dates=["2024-05-10"],
            X=np.asarray([[0.5]]),
            names=["gap_pct"],
            fingerprint="t",
        )
        write_stock_panel(
            FAMILY_CO,
            "000001",
            dates=["2024-05-10"],
            X=np.asarray([[0.1]]),
            names=["z_co_a"],
            fingerprint="t",
        )
        prefetch_l1_coverage(["000001"])
        dates = ["2024-05-09", "2024-05-10"]
        bars = [
            {
                "date": "2024-05-09",
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.2,
                "volume": 1e6,
            },
            {
                "date": "2024-05-10",
                "open": 10.3,
                "high": 10.8,
                "low": 10.0,
                "close": 10.5,
                "volume": 1e6,
            },
        ]
        date_maps = {"000001": {b["date"]: b for b in bars}}
        stock_bars = {"000001": bars}
        window_fn = MagicMock(return_value=bars)

        def _quote(bar, prev_bar=None, px_field="open"):
            if not bar:
                return {}
            return {
                "price": float(bar.get(px_field) or 0),
                "open": float(bar.get("open") or 0),
                "prev_close": float((prev_bar or {}).get("close") or 0) or None,
            }

        scored = {
            "stock_code": "000001",
            "_score_source": "window",
            "sub_scores": {"mom3": 1.0},
        }
        score_window = MagicMock(return_value=scored)
        import core.signal.cross_section_batch as csb

        prev = csb.score_window_as_item
        csb.score_window_as_item = score_window
        try:
            entries, _quotes, _windows, hits, miss = score_open_day_entries(
                stock_bars=stock_bars,
                dates=dates,
                date_maps=date_maps,
                day_i=1,
                max_window=60,
                horizon_days=1,
                cfg={},
                required_factor_keys=None,
                window_for_code=window_fn,
                mock_quote_from_bar=_quote,
            )
        finally:
            csb.score_window_as_item = prev

        self.assertEqual(hits, 0)
        self.assertEqual(miss, 1)
        self.assertEqual(score_window.call_count, 1)
        self.assertEqual(window_fn.call_count, 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].get("_score_source"), "window")

class TestFamilyForTc(unittest.TestCase):
    def test_open_vs_minute_families(self) -> None:
        from core.research.lake_bridge import FAMILY_TC, FAMILY_TC_RANK, family_for_tc

        self.assertEqual(family_for_tc(tau_hm="open"), FAMILY_TC)
        self.assertEqual(family_for_tc(tau_hm="09:30"), FAMILY_TC)
        self.assertEqual(family_for_tc(tau_hm="10:30"), "tc_1030")
        self.assertEqual(family_for_tc(tau_hm="1030", rank=True), "tc_rank_1030")
        self.assertEqual(family_for_tc(tau_hm="open", rank=True), FAMILY_TC_RANK)

    def test_prefix_live_from_lake(self) -> None:
        import tempfile
        from unittest.mock import patch

        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import family_for_tc
        from core.research.lake_replay import (
            clear_coverage_cache,
            prefetch_l1_coverage,
            try_prefix_live_from_lake,
        )

        tmp = tempfile.TemporaryDirectory()
        prev_dir = os.environ.get("QUANTLAB_FEATURE_LAKE_DIR")
        prev_en = os.environ.get("QUANTLAB_FEATURE_LAKE")
        prev_gc = os.environ.get("QUANTLAB_DAY_CUBE_GC")
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = tmp.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        clear_coverage_cache()
        try:
            fam = family_for_tc(tau_hm="09:50")
            write_stock_panel(
                fam,
                "000001",
                dates=["2024-05-10"],
                X=np.asarray([[0.5, 1.2]]),
                names=["gap_pct", "ret_open_to_tau"],
                fingerprint="m0950",
            )
            prefetch_l1_coverage(["000001"], families=[fam])
            def _fake_refresh(item, **_kw):
                item["y_τc"] = 0.42

            with patch(
                "core.t0.score_policy._refresh_tau_oc_from_feats",
                side_effect=_fake_refresh,
            ):
                live = try_prefix_live_from_lake(
                    {"stock_code": "000001", "y_oo": 1.0},
                    code="000001",
                    day="2024-05-10",
                    tau_hm="09:50",
                )
            self.assertIsNotNone(live)
            assert live is not None
            self.assertEqual(live["_score_source"], "prefix_causal")
            self.assertEqual(live["_score_source_detail"], "feature_lake")
            self.assertEqual(live["y_τc"], 0.42)
            self.assertEqual(live["features_tau"]["ret_open_to_tau"], 1.2)
            self.assertIsNone(
                try_prefix_live_from_lake(
                    {}, code="000001", day="2024-05-10", tau_hm="open"
                )
            )
        finally:
            clear_coverage_cache()
            if prev_dir is None:
                os.environ.pop("QUANTLAB_FEATURE_LAKE_DIR", None)
            else:
                os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = prev_dir
            if prev_en is None:
                os.environ.pop("QUANTLAB_FEATURE_LAKE", None)
            else:
                os.environ["QUANTLAB_FEATURE_LAKE"] = prev_en
            if prev_gc is None:
                os.environ.pop("QUANTLAB_DAY_CUBE_GC", None)
            else:
                os.environ["QUANTLAB_DAY_CUBE_GC"] = prev_gc
            tmp.cleanup()

    def test_load_training_matrix_from_l1(self) -> None:
        import tempfile

        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import (
            FAMILY_OO,
            load_training_matrix,
            try_load_l1_as_panel,
        )

        tmp = tempfile.TemporaryDirectory()
        prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(tmp.name, "l1")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        try:
            fp = "fp-test-oo"
            X = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
            y = np.asarray([0.1, 0.2], dtype=np.float64)
            names = ["a", "b"]
            dates = ["2024-05-09", "2024-05-10"]
            write_stock_panel(
                FAMILY_OO,
                "000001",
                dates=dates,
                X=X,
                names=names,
                fingerprint=fp,
                y=y,
            )
            hit = try_load_l1_as_panel(FAMILY_OO, "000001", fp, names=names)
            self.assertIsNotNone(hit)
            assert hit is not None
            np.testing.assert_allclose(hit[0], X)
            self.assertIsNone(try_load_l1_as_panel(FAMILY_OO, "000001", "other-fp"))
            trained = load_training_matrix(FAMILY_OO, "000001", fp)
            self.assertIsNotNone(trained)
            assert trained is not None
            self.assertEqual(trained[4], "l1")
            np.testing.assert_allclose(trained[0], X)
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            tmp.cleanup()

    def test_minute_sync_does_not_overwrite_open(self) -> None:
        import tempfile

        from core.research.feature_lake import load_day_rows, write_stock_panel
        from core.research.lake_bridge import FAMILY_TC, family_for_tc, sync_panel_to_lake
        from core.research.materialize.consumer import materialize_writer

        tmp = tempfile.TemporaryDirectory()
        prev_dir = os.environ.get("QUANTLAB_FEATURE_LAKE_DIR")
        prev_en = os.environ.get("QUANTLAB_FEATURE_LAKE")
        prev_gc = os.environ.get("QUANTLAB_DAY_CUBE_GC")
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = tmp.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        try:
            write_stock_panel(
                FAMILY_TC,
                "000001",
                dates=["2024-05-10"],
                X=np.asarray([[1.0]]),
                names=["gap_pct"],
                fingerprint="open",
            )
            with materialize_writer():
                sync_panel_to_lake(
                    family_for_tc(tau_hm="10:30"),
                    "000001",
                    X=np.asarray([[9.0]]),
                    dates=["2024-05-10"],
                    names=["gap_pct"],
                    fingerprint="m1030",
                )
            open_row = load_day_rows(FAMILY_TC, ["000001"], "2024-05-10")["000001"]
            min_row = load_day_rows("tc_1030", ["000001"], "2024-05-10")["000001"]
            self.assertEqual(open_row["gap_pct"], 1.0)
            self.assertEqual(min_row["gap_pct"], 9.0)
        finally:
            if prev_dir is None:
                os.environ.pop("QUANTLAB_FEATURE_LAKE_DIR", None)
            else:
                os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = prev_dir
            if prev_en is None:
                os.environ.pop("QUANTLAB_FEATURE_LAKE", None)
            else:
                os.environ["QUANTLAB_FEATURE_LAKE"] = prev_en
            if prev_gc is None:
                os.environ.pop("QUANTLAB_DAY_CUBE_GC", None)
            else:
                os.environ["QUANTLAB_DAY_CUBE_GC"] = prev_gc
            tmp.cleanup()

if __name__ == "__main__":
    unittest.main()
