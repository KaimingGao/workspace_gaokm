"""物化 L1：meta 盖住就跳过工人，不堆矩阵，也不写薄立方。日线只加载一次。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


def _bars() -> list:
    out = []
    for day in range(2, 8):
        out.append(
            {
                "date": f"2024-01-{day:02d}",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
            }
        )
    return out


class MetaCoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("QUANTLAB_FEATURE_LAKE_DIR")
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = self._tmp.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("QUANTLAB_FEATURE_LAKE_DIR", None)
        else:
            os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = self._prev
        self._tmp.cleanup()

    def test_cover_requires_scored_through(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import meta_covers_bars, stem_extra_meta
        from core.research.lake_bridge import FAMILY_OO
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

        bars = _bars()
        fp = make_fingerprint(
            bars_fp=bars_fingerprint(bars),
            horizon=1,
            ridge_core_only=0,
        )
        names = ["a", "b", "c", "d"]
        X = np.ones((4, 4), dtype=np.float64)
        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            X=X,
            names=names,
            fingerprint=fp,
        )
        self.assertIsNone(meta_covers_bars(FAMILY_OO, "000001", fp, bars))
        write_stock_panel(
            FAMILY_OO,
            "000001",
            dates=["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            X=X,
            names=names,
            fingerprint=fp,
            extra_meta=stem_extra_meta(bars),
        )
        hit = meta_covers_bars(FAMILY_OO, "000001", fp, bars)
        self.assertEqual(hit, {"n_rows": 4, "n_cols": 4})
        from core.research.feature_lake import _paths

        os.remove(_paths(FAMILY_OO, "000001")["npz"])
        self.assertIsNone(meta_covers_bars(FAMILY_OO, "000001", fp, bars))

    def test_meta_read_failure_falls_through(self) -> None:
        from core.research.incremental_panel import meta_covers_bars

        with mock.patch(
            "core.research.feature_lake.panel_npz_exists", return_value=True
        ), mock.patch(
            "core.research.feature_lake.read_stock_meta",
            side_effect=OSError("meta unreadable"),
        ):
            hit = meta_covers_bars("oo", "000001", "fp", _bars())
        self.assertIsNone(hit)

    def test_shared_bars_list_is_copied_per_family(self) -> None:
        from core.research.materialize.l1_jobs import _items_from_bars

        source = {"000001": [{"date": "2024-01-02"}]}
        items = _items_from_bars(["000001"], source)
        items[0][1].clear()
        self.assertEqual(len(source["000001"]), 1)


class ReleaseRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_DAY_CUBE_DIR": os.environ.get("QUANTLAB_DAY_CUBE_DIR"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmp.name, "lake")
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = os.path.join(self._tmp.name, "cubes")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        for key, val in self._prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def _write_oo(self, code: str, bars: list) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta
        from core.research.lake_bridge import FAMILY_OO
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

        fp = make_fingerprint(
            bars_fp=bars_fingerprint(bars),
            horizon=1,
            ridge_core_only=0,
        )
        write_stock_panel(
            FAMILY_OO,
            code,
            dates=[b["date"] for b in bars[:4]],
            X=np.arange(8, dtype=np.float64).reshape(4, 2),
            names=["f0", "f1"],
            fingerprint=fp,
            extra_meta=stem_extra_meta(bars),
        )

    def test_oo_hit_skips_worker_and_thin_cubes(self) -> None:
        from core.research.oo_ridge_compact import assemble_oo_panels

        bars = _bars()
        self._write_oo("000001", bars)
        self._write_oo("000002", bars)
        stats: dict = {}
        with mock.patch(
            "core.research.oo_ridge_compact._oo_stock_job",
            side_effect=AssertionError("meta hit should stay in the parent"),
        ) as job, mock.patch(
            "core.research.day_cube.cubes_from_stacked_panel",
            side_effect=AssertionError("release path must not write thin cubes"),
        ) as cubes:
            x, y, _names, dates = assemble_oo_panels(
                [("000001", bars), ("000002", bars)],
                horizon_days=1,
                workers=1,
                release_rows=True,
                stats_out=stats,
            )
        job.assert_not_called()
        cubes.assert_not_called()
        self.assertEqual(x.shape[0], 0)
        self.assertEqual(y.shape[0], 0)
        self.assertEqual(stats.get("hits"), 2)
        self.assertEqual(stats.get("n_rows"), 8)
        self.assertEqual(stats.get("n_stocks"), 2)
        self.assertIn("2024-01-07", dates)

    def test_oo_release_does_not_stack_or_write_cubes(self) -> None:
        from core.research.oo_ridge_compact import assemble_oo_panels

        bars = _bars()

        def _job(job):
            code = job[0]
            x = np.ones((3, 2), dtype=np.float64)
            y = np.ones(3, dtype=np.float64)
            return x, y, ["f0", "f1"], ["2024-01-05", "2024-01-06", "2024-01-07"]

        stats: dict = {}
        with mock.patch(
            "core.research.oo_ridge_compact._oo_stock_job", side_effect=_job
        ), mock.patch(
            "core.research.day_cube.cubes_from_stacked_panel"
        ) as cubes:
            x, _y, names, dates = assemble_oo_panels(
                [("000001", bars), ("000002", bars)],
                horizon_days=1,
                workers=1,
                release_rows=True,
                stats_out=stats,
            )
        cubes.assert_not_called()
        self.assertEqual(x.shape[0], 0)
        self.assertEqual(names, ["f0", "f1"])
        self.assertEqual(stats.get("n_rows"), 6)
        self.assertEqual(stats.get("hits"), 0)
        self.assertEqual(dates, ["2024-01-05", "2024-01-06", "2024-01-07"])

    def test_tau_release_does_not_append_block(self) -> None:
        from core.research.panel_matrix import PanelMatrix, collect_tau_compact

        bars = _bars()
        calls = {"append": 0}
        real = PanelMatrix.append_block

        def _append(self, arr):
            calls["append"] += 1
            return real(self, arr)

        def _job(item, col_names, slim_keys, **_kw):
            code = str(item.get("code") or item.get("stock_code"))
            width = len(col_names)
            arr = np.ones((5, width), dtype=np.float64)
            dates = [b["date"] for b in bars[:5]]
            return code, arr, [0.1] * 5, dates, [{}] * 5, [{}] * 5

        stats: dict = {}
        with mock.patch(
            "core.research.panel_matrix._tau_stock_job", side_effect=_job
        ), mock.patch.object(PanelMatrix, "append_block", _append):
            x, names, ys, dates, _metas, n_stocks = collect_tau_compact(
                [{"code": "000001", "stock_code": "000001", "bars": list(bars)}],
                ["c0", "c1", "c2", "c3"],
                include_alpha_tc=False,
                include_alpha_th=False,
                workers=1,
                release_rows=True,
                stats_out=stats,
            )
        self.assertEqual(calls["append"], 0)
        self.assertEqual(x.shape[0], 0)
        self.assertEqual(ys, [])
        self.assertEqual(stats.get("n_rows"), 5)
        self.assertEqual(stats.get("n_stocks"), 1)
        self.assertEqual(n_stocks, 1)
        self.assertEqual(len(names), 4)
        self.assertEqual(dates[0], "2024-01-02")

    def test_tau_meta_hit_skips_worker(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta
        from core.research.lake_bridge import family_for_tc
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
        from core.research.panel_matrix import _tc_matrix_seed_names, collect_tau_compact

        bars = _bars()
        names = _tc_matrix_seed_names(
            ["c0", "c1", "c2", "c3"],
            include_alpha_tc=False,
            include_alpha_th=False,
        )
        fp = make_fingerprint(
            bars_fp=bars_fingerprint(bars),
            tau_hm="open",
            min_history=12,
            include_alpha_tc=0,
            include_alpha_th=0,
            use_minute=0,
        )
        write_stock_panel(
            family_for_tc(tau_hm="open", rank=False),
            "000001",
            dates=[b["date"] for b in bars[:4]],
            X=np.ones((4, len(names)), dtype=np.float64),
            names=names,
            fingerprint=fp,
            extra_meta=stem_extra_meta(bars),
        )
        stats: dict = {}
        with mock.patch(
            "core.research.panel_matrix._tau_stock_job",
            side_effect=AssertionError("covered stock should not enter a worker"),
        ):
            x, _names, _ys, dates, _metas, n_stocks = collect_tau_compact(
                [{"code": "000001", "stock_code": "000001", "bars": list(bars)}],
                ["c0", "c1", "c2", "c3"],
                include_alpha_tc=False,
                include_alpha_th=False,
                workers=1,
                release_rows=True,
                stats_out=stats,
            )
        self.assertEqual(stats.get("hits"), 1)
        self.assertEqual(stats.get("n_rows"), 4)
        self.assertEqual(n_stocks, 1)
        self.assertEqual(x.shape[0], 0)
        self.assertIn(bars[-1]["date"], dates)

    def test_materialize_loads_daily_bars_once(self) -> None:
        from core.research.materialize import l1_jobs

        bars = {
            "000001": _bars(),
            "000002": _bars(),
        }
        with mock.patch.object(
            l1_jobs,
            "_stock_bar_items",
            return_value=(bars, [], []),
        ) as loaded, mock.patch.object(
            l1_jobs,
            "_materialize_oo",
            return_value={"ok": True, "family": "oo"},
        ) as oo, mock.patch.object(
            l1_jobs,
            "_materialize_tc",
            return_value={"ok": True, "family": "tc"},
        ) as tc, mock.patch.object(
            l1_jobs,
            "_materialize_co",
            return_value={"ok": True, "family": "co"},
        ) as co, mock.patch.object(
            l1_jobs,
            "_materialize_oo_rank",
            return_value={"ok": True, "family": "oo_rank"},
        ) as rank:
            report = l1_jobs._materialize_l1_body(
                ["oo", "tc", "co"],
                ["000001", "000002"],
                lookback=20,
            )
        self.assertTrue(report.get("ok"), report)
        loaded.assert_called_once()
        for fn in (oo, tc, co, rank):
            self.assertIs(fn.call_args.kwargs.get("stock_bars"), bars)
