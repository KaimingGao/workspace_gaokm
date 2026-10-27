"""L2 临时解开：一轮解压、块内读回、整窗拒绝、目录一定删掉。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


def _bars_days(n: int) -> list:
    return [f"2024-01-{day:02d}" for day in range(2, 2 + n)]


class CubeSpillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_DAY_CUBE_DIR": os.environ.get("QUANTLAB_DAY_CUBE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE": os.environ.get("QUANTLAB_DAY_CUBE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmp.name, "lake")
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = os.path.join(self._tmp.name, "cubes")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        try:
            from core.research.day_cube import clear_memory

            clear_memory()
        except Exception:
            pass
        for key, val in self._prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def test_refuse_full_window_keeps_chunk(self) -> None:
        from core.research.materialize.cube_spill import refuse_resident_block

        widths = [226, 247, 220]
        with self.assertRaises(MemoryError):
            refuse_resident_block(1040, 800, widths)
        refuse_resident_block(80, 800, widths)

    def test_roundtrip_matches_slice_and_dir_is_removed(self) -> None:
        from core.research.feature_lake import slice_stock_panels, write_stock_panel
        from core.research.lake_bridge import FAMILY_OO
        from core.research.materialize.cube_spill import open_lake_spill

        codes = ["000001", "000002", "000003"]
        days = _bars_days(6)
        names = ["gap_pct", "ret_oc"]
        for i, code in enumerate(codes):
            rows = np.arange(len(days) * 2, dtype=np.float64).reshape(len(days), 2)
            rows[i, 1] = np.nan
            write_stock_panel(
                FAMILY_OO,
                code,
                dates=days,
                X=rows,
                names=names,
                fingerprint="spill-test",
                y=np.zeros(len(days), dtype=np.float64),
            )
        root = ""
        with open_lake_spill() as spill:
            root = spill.root
            self.assertTrue(os.path.isdir(root))
            self.assertTrue(spill.write("oo", FAMILY_OO, codes, days[1:4]))
            got = spill.read("oo", codes, days[1:4])
        self.assertFalse(os.path.isdir(root))
        sliced = slice_stock_panels(FAMILY_OO, codes, days[1:4])
        self.assertEqual(set(got), set(sliced or {}))
        for code in codes:
            self.assertEqual(list(got[code][0]), list(sliced[code][0]))
            for day in days[1:4]:
                np.testing.assert_allclose(
                    got[code][1][day],
                    sliced[code][1][day],
                    equal_nan=True,
                )

    def test_dir_removed_when_body_raises(self) -> None:
        from core.research.materialize.cube_spill import open_lake_spill

        root = ""
        with self.assertRaises(RuntimeError):
            with open_lake_spill() as spill:
                root = spill.root
                raise RuntimeError("boom")
        self.assertTrue(root)
        self.assertFalse(os.path.isdir(root))

    def test_cancel_drops_spill_dir(self) -> None:
        import tempfile as tf

        from core.research.materialize.cube_spill import warmup_clock_from_spill

        roots: list = []
        real = tf.mkdtemp
        checks = {"n": 0}

        def _cancel() -> bool:
            checks["n"] += 1
            # 状态扫描通过之后，临时目录已经建好，再取消。
            return checks["n"] >= 3

        def _capture(*args, **kwargs):
            path = real(*args, **kwargs)
            prefix = kwargs.get("prefix")
            if prefix is None and len(args) >= 2:
                prefix = args[1]
            if str(prefix or "").startswith("ql-cube-spill"):
                roots.append(path)
            return path

        with mock.patch(
            "core.research.day_cube.day_cube_status", return_value="miss"
        ), mock.patch(
            "core.research.materialize.flags.require_day_cube", return_value=False
        ), mock.patch(
            "core.research.materialize.consumer.consumer_read_only", return_value=False
        ), mock.patch("tempfile.mkdtemp", side_effect=_capture):
            warmup_clock_from_spill(
                ["000001", "000002"],
                ["2024-01-02"],
                clock="open",
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                min_items=2,
                require_oo_item=True,
                require_tc_day=False,
                cancel_cb=_cancel,
            )
        self.assertTrue(roots)
        for path in roots:
            self.assertFalse(os.path.isdir(path), path)

    def test_vectorized_cubes_match_item_path(self) -> None:
        from core.research.day_cube import clear_memory, load_day_cube
        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
        from core.research.materialize.cube_batch import build_day_cubes_from_lake
        from core.research.materialize.cube_spill import open_lake_spill
        from core.signal.factors.z_co import Z_CO_LEGACY_KEYS
        from core.signal.factors.z_oo import z_oo_legacy_keys
        from core.signal.factors.z_tc import Z_TC_LEGACY_OPEN

        codes = [f"00000{i}" for i in range(1, 6)]
        days = _bars_days(30)
        oo_names = list(z_oo_legacy_keys())[:4]
        tc_names = list(Z_TC_LEGACY_OPEN)[:3]
        co_names = list(Z_CO_LEGACY_KEYS)[:3]
        self.assertGreaterEqual(len(oo_names), 2)
        rng = np.random.default_rng(7)
        families = (
            (FAMILY_OO, oo_names),
            (FAMILY_TC, tc_names),
            (FAMILY_CO, co_names),
        )
        for fam, names in families:
            width = len(names)
            for i, code in enumerate(codes):
                block = rng.normal(size=(len(days), width))
                block[i % len(days), 0] = np.nan
                write_stock_panel(
                    fam,
                    code,
                    dates=days,
                    X=block,
                    names=names,
                    fingerprint=f"cube-{fam}",
                )

        def _build(*, on_items, shared):
            return build_day_cubes_from_lake(
                codes,
                days,
                family_oo=FAMILY_OO,
                family_tc=FAMILY_TC,
                family_co=FAMILY_CO,
                clock="open",
                universe_codes=codes,
                min_items=2,
                require_oo_item=True,
                require_tc_day=False,
                on_items=on_items,
                replace_existing=True,
                shared_oo=None if shared is None else shared[0],
                shared_tc=None if shared is None else shared[1],
                shared_co=None if shared is None else shared[2],
            )

        item = _build(on_items=lambda _day, _items: None, shared=None)
        self.assertEqual(int(item.get("cubes_built") or 0), len(days), item)
        first = {}
        for day in days:
            cube = load_day_cube(day, codes, clock="open", remember=False)
            self.assertIsNotNone(cube, day)
            first[day] = {
                name: (
                    float(cube.z_mean[i]),
                    float(cube.z_std[i]),
                )
                for i, name in enumerate(cube.names)
            }
        clear_memory()
        with open_lake_spill() as spill:
            for key, fam in (("oo", FAMILY_OO), ("tc", FAMILY_TC), ("co", FAMILY_CO)):
                self.assertTrue(spill.write(key, fam, codes, days))
            shared = (
                spill.read("oo", codes, days),
                spill.read("tc", codes, days),
                spill.read("co", codes, days),
            )
            vec = _build(on_items=None, shared=shared)
        self.assertEqual(int(vec.get("cubes_built") or 0), len(days), vec)
        # 向量化写会按当日截面重算广度列。其余列的名字、μ、σ 与逐票路径一致。
        rewritten = set()
        for bare in (
            "sector_gap_breadth",
            "theme_day",
            "gap_vs_sector",
            "sector_ret_to_tau",
            "ret_vs_sector",
        ):
            for prefix in ("", "z_τh_", "z_τc_", "z_co_"):
                rewritten.add(prefix + bare)
        spilled = {}
        for day in days:
            cube = load_day_cube(day, codes, clock="open", remember=False)
            self.assertIsNotNone(cube, day)
            got = {
                name: (float(cube.z_mean[i]), float(cube.z_std[i]))
                for i, name in enumerate(cube.names)
            }
            spilled[day] = got
            self.assertTrue(set(first[day]) <= set(got), day)
            for name, (mu, sd) in first[day].items():
                if name in rewritten:
                    continue
                np.testing.assert_allclose(got[name][0], mu, equal_nan=True)
                np.testing.assert_allclose(got[name][1], sd, equal_nan=True)
        clear_memory()
        direct = _build(on_items=None, shared=None)
        self.assertEqual(int(direct.get("cubes_built") or 0), len(days), direct)
        for day in days:
            cube = load_day_cube(day, codes, clock="open", remember=False)
            self.assertIsNotNone(cube, day)
            got = {
                name: (float(cube.z_mean[i]), float(cube.z_std[i]))
                for i, name in enumerate(cube.names)
            }
            self.assertEqual(list(got), list(spilled[day]))
            for name, (mu, sd) in spilled[day].items():
                np.testing.assert_allclose(got[name][0], mu, equal_nan=True)
                np.testing.assert_allclose(got[name][1], sd, equal_nan=True)


class SpillWarmupFlagTests(unittest.TestCase):
    def test_seed_pool_false_does_not_ensure_when_require_cube(self) -> None:
        from core.t0 import score_policy as sp

        old = os.environ.get("QUANTLAB_MATERIALIZE_REQUIRE_CUBE")
        try:
            os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = "1"
            with mock.patch(
                "core.research.lake_replay.prefetch_l1_coverage", return_value={"oo": 2}
            ), mock.patch(
                "core.research.day_cube.enabled", return_value=True
            ), mock.patch(
                "core.research.feature_lake.enabled", return_value=True
            ), mock.patch(
                "core.research.day_cube.day_cube_status", return_value="miss"
            ), mock.patch(
                "core.research.batch_score.ensure_day_cube_for_items"
            ) as ensure:
                out = sp.warmup_watching_day_cubes(
                    ["000001", "000002"],
                    ["2026-01-02"],
                    min_lake_frac=0.5,
                    seed_pool=False,
                )
            ensure.assert_not_called()
            self.assertEqual(out.get("cubes_built"), 0)
        finally:
            if old is None:
                os.environ.pop("QUANTLAB_MATERIALIZE_REQUIRE_CUBE", None)
            else:
                os.environ["QUANTLAB_MATERIALIZE_REQUIRE_CUBE"] = old

    def test_spill_error_fails_the_open_clock(self) -> None:
        from core.research.materialize.l2_jobs import _warmup_open_clock

        report: dict = {"ok": True, "by_clock": {}}
        with mock.patch(
            "core.t0.score_policy.warmup_watching_day_cubes",
            return_value={
                "cubes_hit": 3,
                "cubes_built": 1,
                "days_skipped": 2,
                "error": "spill broke",
            },
        ):
            _warmup_open_clock(
                report,
                ["000001", "000002"],
                ["2024-01-02", "2024-01-03"],
                "open",
                lake_frac=0.5,
                progress_cb=None,
                cancel_cb=None,
            )
        row = report["by_clock"]["open"]
        self.assertFalse(row["ok"])
        self.assertEqual(row["error"], "spill broke")
        self.assertEqual(row["cubes_hit"], 3)
        self.assertFalse(report["ok"])

    def test_build_error_is_reported_and_dir_removed(self) -> None:
        import tempfile as tf

        from core.research.materialize.cube_spill import warmup_clock_from_spill

        roots: list = []
        real = tf.mkdtemp

        def _capture(*args, **kwargs):
            path = real(*args, **kwargs)
            prefix = kwargs.get("prefix")
            if prefix is None and len(args) >= 2:
                prefix = args[1]
            if str(prefix or "").startswith("ql-cube-spill"):
                roots.append(path)
            return path

        with mock.patch(
            "core.research.day_cube.day_cube_status", return_value="miss"
        ), mock.patch(
            "core.research.materialize.flags.require_day_cube", return_value=False
        ), mock.patch(
            "core.research.materialize.consumer.consumer_read_only", return_value=False
        ), mock.patch(
            "core.research.materialize.cube_batch.build_day_cubes_from_lake",
            side_effect=RuntimeError("cube write broke"),
        ), mock.patch("tempfile.mkdtemp", side_effect=_capture):
            stats = warmup_clock_from_spill(
                ["000001", "000002"],
                ["2024-01-02"],
                clock="open",
                family_oo="oo",
                family_tc="tc",
                family_co="co",
                min_items=2,
                require_oo_item=True,
                require_tc_day=False,
            )
        self.assertEqual(stats.get("error"), "cube write broke")
        self.assertTrue(roots)
        for path in roots:
            self.assertFalse(os.path.isdir(path), path)

    def test_open_clock_warmup_uses_spill(self) -> None:
        from core.research.materialize.l2_jobs import _warmup_open_clock

        with mock.patch(
            "core.t0.score_policy.warmup_watching_day_cubes",
            return_value={"cubes_hit": 0, "cubes_built": 0},
        ) as warm:
            _warmup_open_clock(
                {"ok": True, "by_clock": {}},
                ["000001", "000002"],
                ["2024-01-02"],
                "open",
                lake_frac=0.5,
                progress_cb=None,
                cancel_cb=None,
            )
        self.assertFalse(warm.call_args.kwargs.get("seed_pool"))
        self.assertFalse(warm.call_args.kwargs.get("prefetch_l1"))
