"""L2 日期作废与冷宇宙回收。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


class TestDayCubeGc(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_DAY_CUBE_DIR": os.environ.get("QUANTLAB_DAY_CUBE_DIR"),
            "QUANTLAB_DAY_CUBE": os.environ.get("QUANTLAB_DAY_CUBE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
        }
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = os.path.join(self._tmpdir.name, "cubes")
        os.environ["QUANTLAB_DAY_CUBE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "1"
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmpdir.name, "lake")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        from core.research.day_cube import clear_memory

        clear_memory()

    def tearDown(self) -> None:
        from core.research.day_cube import clear_memory

        clear_memory()
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmpdir.cleanup()

    def _save(self, day: str, codes: list, clock: str, value: float = 1.0):
        from core.research.day_cube import build_day_cube, save_day_cube

        feats = {code: {"f": value + i} for i, code in enumerate(codes)}
        cube = build_day_cube(day, feats, names=["f"], clock=clock)
        self.assertIsNotNone(cube)
        self.assertTrue(save_day_cube(cube))
        return cube

    def _on_disk(self, day: str, codes: list, clock: str) -> bool:
        from core.research.day_cube import _disk_paths
        from core.research.feature_registry import universe_hash

        path = _disk_paths(universe_hash(codes), clock, day)["npz"]
        return os.path.isfile(path)

    def test_prune_keeps_named_hash_and_drops_cold_schema(self) -> None:
        from core.research.day_cube import _root
        from core.research.day_cube_gc import prune_cold
        from core.research.feature_registry import universe_hash

        keep_codes = ["a", "b"]
        cold_codes = ["a", "c"]
        self._save("2024-06-01", keep_codes, "open")
        self._save("2024-06-01", cold_codes, "open")
        stale_schema = os.path.join(_root(), "v0", "dead")
        os.makedirs(stale_schema)
        with open(os.path.join(stale_schema, "x.txt"), "w", encoding="utf-8") as fh:
            fh.write("old")
        keep = universe_hash(keep_codes)
        out = prune_cold(keep_hashes={keep})
        self.assertFalse(out["skipped"])
        self.assertEqual(out["hashes_pruned"], 1)
        self.assertEqual(out["schemas_pruned"], 1)
        self.assertTrue(self._on_disk("2024-06-01", keep_codes, "open"))
        self.assertFalse(self._on_disk("2024-06-01", cold_codes, "open"))
        self.assertFalse(os.path.isdir(os.path.join(_root(), "v0")))

    def test_unresolved_keep_does_not_prune(self) -> None:
        from core.research.day_cube_gc import prune_cold

        self._save("2024-06-02", ["a", "c"], "open")
        out = prune_cold(keep_hashes=set())
        self.assertTrue(out["skipped"])
        self.assertEqual(out["hashes_pruned"], 0)
        self.assertTrue(self._on_disk("2024-06-02", ["a", "c"], "open"))

    def test_invalidate_drops_l1_dates_on_every_cube(self) -> None:
        from core.research.day_cube import load_day_cube
        from core.research.feature_lake import write_stock_panel
        from core.research.lake_bridge import invalidate_l1_for_code

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-07-01", "2024-07-02"],
            X=np.asarray([[1.0], [2.0]]),
            names=["f"],
            fingerprint="fp-1",
            extra_meta={"bars_fp": "bars-1"},
        )
        keep = ["000001", "000002"]
        other = ["000001", "000003"]
        for day in ("2024-07-01", "2024-07-02", "2024-07-03"):
            self._save(day, keep, "open")
            self._save(day, other, "tau_1030")
        load_day_cube("2024-07-01", keep, clock="open")
        invalidate_l1_for_code("000001", families=["oo"])
        for day in ("2024-07-01", "2024-07-02"):
            self.assertFalse(self._on_disk(day, keep, "open"))
            self.assertFalse(self._on_disk(day, other, "tau_1030"))
        self.assertTrue(self._on_disk("2024-07-03", keep, "open"))
        self.assertTrue(self._on_disk("2024-07-03", other, "tau_1030"))
        self.assertIsNone(load_day_cube("2024-07-01", keep, clock="open"))
        self.assertIsNotNone(load_day_cube("2024-07-03", keep, clock="open"))

    def test_missing_l1_meta_leaves_cubes(self) -> None:
        from core.research.lake_bridge import invalidate_l1_for_code

        self._save("2024-07-04", ["000001", "000002"], "open")
        invalidate_l1_for_code("000001", families=["oo"])
        self.assertTrue(self._on_disk("2024-07-04", ["000001", "000002"], "open"))

    def test_unreadable_l1_dates_drop_all_current_schema(self) -> None:
        from core.research.feature_lake import _paths, write_stock_panel
        from core.research.lake_bridge import invalidate_l1_for_code

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-07-01"],
            X=np.asarray([[1.0]]),
            names=["f"],
            fingerprint="fp",
        )
        os.unlink(_paths("oo", "000001")["npz"])
        self._save("2024-08-01", ["000001", "000002"], "open")
        self._save("2024-08-02", ["000009", "000010"], "tau_1400")
        invalidate_l1_for_code("000001", families=["oo"])
        self.assertFalse(self._on_disk("2024-08-01", ["000001", "000002"], "open"))
        self.assertFalse(self._on_disk("2024-08-02", ["000009", "000010"], "tau_1400"))

    def test_tail_rewrite_drops_only_changed_dates(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import merge_matrix_rows
        from core.research.materialize.consumer import materialize_writer

        codes = ["000001", "000002"]
        write_stock_panel(
            "co",
            "000001",
            dates=["2024-03-01", "2024-03-02", "2024-03-03"],
            X=np.asarray([[1.0], [2.0], [3.0]]),
            names=["x"],
            fingerprint="old",
            y=np.asarray([0.1, 0.2, 0.3]),
            extra_meta={"bars_fp": "b0"},
        )
        for day in ("2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"):
            self._save(day, codes, "open")
        X_m, y_m, names_m, dates_m = merge_matrix_rows(
            np.asarray([[1.0], [2.0], [3.0]]),
            np.asarray([0.1, 0.2, 0.3]),
            ["2024-03-01", "2024-03-02", "2024-03-03"],
            ["x"],
            np.asarray([[30.0], [40.0]]),
            np.asarray([3.0, 4.0]),
            ["2024-03-03", "2024-03-04"],
            ["x"],
            keep_from="2024-03-03",
        )
        with materialize_writer():
            ok = write_stock_panel(
                "co",
                "000001",
                dates=dates_m,
                X=X_m,
                names=names_m,
                fingerprint="new",
                y=y_m,
                extra_meta={"bars_fp": "b1"},
            )
        self.assertTrue(ok)
        self.assertTrue(self._on_disk("2024-03-01", codes, "open"))
        self.assertTrue(self._on_disk("2024-03-02", codes, "open"))
        self.assertFalse(self._on_disk("2024-03-03", codes, "open"))
        self.assertFalse(self._on_disk("2024-03-04", codes, "open"))

    def test_unchanged_fingerprint_keeps_cube(self) -> None:
        from core.research.feature_lake import write_stock_panel

        codes = ["000001", "000002"]
        kwargs = dict(
            dates=["2024-05-01"],
            X=np.asarray([[1.0]]),
            names=["x"],
            fingerprint="same",
            extra_meta={"bars_fp": "bars"},
        )
        write_stock_panel("oo", "000001", **kwargs)
        self._save("2024-05-01", codes, "open")
        write_stock_panel("oo", "000001", **kwargs)
        self.assertTrue(self._on_disk("2024-05-01", codes, "open"))

    def test_training_write_does_not_drop_cube(self) -> None:
        from core.research.feature_lake import write_stock_panel

        codes = ["000001", "000002"]
        self._save("2024-05-02", codes, "open")
        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-05-02"],
            X=np.asarray([[9.0]]),
            names=["x"],
            fingerprint="fit",
            extra_meta={"bars_fp": "bars-2"},
        )
        self.assertTrue(self._on_disk("2024-05-02", codes, "open"))

    def test_fit_sync_keeps_existing_panel_and_cube(self) -> None:
        from core.research.feature_lake import load_stock_panel, write_stock_panel
        from core.research.lake_bridge import sync_panel_to_lake
        from core.research.materialize.consumer import materialize_writer

        codes = ["000001", "000002"]
        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-05-03"],
            X=np.asarray([[1.0]]),
            names=["x"],
            fingerprint="lake",
            extra_meta={"bars_fp": "bars"},
        )
        self._save("2024-05-03", codes, "open")
        kept = sync_panel_to_lake(
            "oo",
            "000001",
            X=np.asarray([[9.0]]),
            dates=["2024-05-03"],
            names=["x"],
            fingerprint="fit",
            extra_meta={"bars_fp": "bars-fit"},
        )
        self.assertFalse(kept)
        pack = load_stock_panel("oo", "000001", fingerprint=None)
        self.assertIsNotNone(pack)
        assert pack is not None
        self.assertAlmostEqual(float(pack["X"][0, 0]), 1.0)
        self.assertTrue(self._on_disk("2024-05-03", codes, "open"))
        empty = sync_panel_to_lake(
            "tc",
            "000001",
            X=np.asarray([[3.0]]),
            dates=["2024-05-03"],
            names=["x"],
            fingerprint="fit",
        )
        self.assertFalse(empty)
        self.assertIsNone(load_stock_panel("tc", "000001", fingerprint=None))
        with materialize_writer():
            wrote = sync_panel_to_lake(
                "oo",
                "000001",
                X=np.asarray([[9.0]]),
                dates=["2024-05-03"],
                names=["x"],
                fingerprint="fit",
                extra_meta={"bars_fp": "bars-fit"},
            )
        self.assertTrue(wrote)
        self.assertFalse(self._on_disk("2024-05-03", codes, "open"))

    def test_batch_unlinks_each_date_once(self) -> None:
        from core.research.day_cube_gc import l2_invalidation_batch, note_stale_dates

        self._save("2024-09-01", ["a", "b"], "open")
        with mock.patch("core.research.day_cube_gc.os.unlink", wraps=os.unlink) as unlink:
            with l2_invalidation_batch():
                for _ in range(800):
                    note_stale_dates(["2024-09-01"])
        self.assertEqual(unlink.call_count, 2)
        self.assertFalse(self._on_disk("2024-09-01", ["a", "b"], "open"))

    def test_batch_visible_to_thread_pool_workers(self) -> None:
        """ThreadPool 看不到 contextvar；_ACTIVE_BATCH 须让工作线程只攒不删。"""
        from concurrent.futures import ThreadPoolExecutor

        from core.research.day_cube_gc import l2_invalidation_batch, note_stale_dates

        self._save("2024-09-11", ["a", "b"], "open")
        results = []

        def _note(_i: int) -> None:
            results.append(note_stale_dates(["2024-09-11"]))

        with mock.patch("core.research.day_cube_gc.os.unlink", wraps=os.unlink) as unlink:
            with l2_invalidation_batch():
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(_note, range(40)))
            self.assertEqual(unlink.call_count, 2)
        self.assertTrue(results)
        self.assertTrue(all(r.get("batched") for r in results))
        self.assertFalse(self._on_disk("2024-09-11", ["a", "b"], "open"))

    def test_drop_dates_listdir_skips_missing(self) -> None:
        from core.research.day_cube_gc import drop_dates

        self._save("2024-09-20", ["a", "b"], "open")
        # 大批日期走 listdir 索引；绝大多数不存在，不应炸
        days = [f"2020-01-{i:02d}" for i in range(1, 32)] + ["2024-09-20"]
        out = drop_dates(days)
        self.assertGreaterEqual(out["files_removed"], 1)
        self.assertFalse(self._on_disk("2024-09-20", ["a", "b"], "open"))

    def test_gc_off_keeps_files(self) -> None:
        from core.research.day_cube_gc import drop_dates

        self._save("2024-09-02", ["a", "b"], "open")
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        drop_dates(["2024-09-02"])
        self.assertTrue(self._on_disk("2024-09-02", ["a", "b"], "open"))

    def test_rejects_dates_that_leave_the_cube_root(self) -> None:
        from core.research.day_cube import _root
        from core.research.day_cube_gc import drop_dates

        outside = os.path.join(self._tmpdir.name, "secret.txt")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("keep")
        self._save("2024-11-01", ["a", "b"], "open")
        drop_dates(["../secret", "2024-11-01/../secret", "not-a-date"])
        self.assertTrue(os.path.isfile(outside))
        with open(outside, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "keep")
        self.assertTrue(self._on_disk("2024-11-01", ["a", "b"], "open"))
        self.assertTrue(os.path.isdir(_root()))

    def test_prune_unlinks_symlink_and_keeps_target(self) -> None:
        from core.research.day_cube import _root
        from core.research.day_cube_gc import prune_cold
        from core.research.feature_registry import FEATURE_SCHEMA

        outside = os.path.join(self._tmpdir.name, "outside")
        os.makedirs(outside)
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("keep")
        link = os.path.join(_root(), f"v{FEATURE_SCHEMA}", "linked-hash")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(outside, link)
        out = prune_cold(keep_hashes={"abc123abc123"})
        self.assertEqual(out["hashes_pruned"], 1)
        self.assertFalse(os.path.lexists(link))
        with open(secret, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "keep")

    def test_partial_unreadable_keeps_other_dates(self) -> None:
        from core.research.feature_lake import _paths, write_stock_panel
        from core.research.lake_bridge import invalidate_l1_for_code

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-12-01"],
            X=np.asarray([[1.0]]),
            names=["f"],
            fingerprint="oo",
            extra_meta={"bars_fp": "b"},
        )
        write_stock_panel(
            "tc",
            "000001",
            dates=["2024-12-02"],
            X=np.asarray([[2.0]]),
            names=["f"],
            fingerprint="tc",
            extra_meta={"bars_fp": "b"},
        )
        os.unlink(_paths("tc", "000001")["npz"])
        codes = ["000001", "000002"]
        self._save("2024-12-01", codes, "open")
        self._save("2024-12-03", codes, "open")
        invalidate_l1_for_code("000001", families=["oo", "tc"])
        self.assertFalse(self._on_disk("2024-12-01", codes, "open"))
        self.assertTrue(self._on_disk("2024-12-03", codes, "open"))

    def test_drop_one_day_keeps_other_day_in_memory(self) -> None:
        from core.research.day_cube import load_day_cube
        from core.research.day_cube_gc import drop_dates

        codes = ["a", "b"]
        self._save("2024-06-11", codes, "open")
        self._save("2024-06-12", codes, "open")
        first = load_day_cube("2024-06-11", codes, clock="open")
        second = load_day_cube("2024-06-12", codes, clock="open")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        drop_dates(["2024-06-11"])
        self.assertIsNone(load_day_cube("2024-06-11", codes, clock="open"))
        again = load_day_cube("2024-06-12", codes, clock="open")
        self.assertIs(again, second)

    def test_keep_source_error_skips_prune(self) -> None:
        from core.research.day_cube_gc import prune_cold

        self._save("2024-06-13", ["a", "c"], "open")
        with mock.patch(
            "core.t0.score_policy._watching_codes_for_z",
            side_effect=RuntimeError("watching down"),
        ), mock.patch(
            "core.research_universe.resolve_model_fit_codes",
            return_value={"codes": ["a", "b"]},
        ):
            out = prune_cold()
        self.assertTrue(out["skipped"])
        self.assertEqual(out["hashes_pruned"], 0)
        self.assertTrue(self._on_disk("2024-06-13", ["a", "c"], "open"))

    def test_unreadable_panel_still_drops_old_dates(self) -> None:
        import json

        from core.research.feature_lake import _paths, write_stock_panel

        codes = ["000001", "000002"]
        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-04-01", "2024-04-02"],
            X=np.asarray([[1.0], [2.0]]),
            names=["x"],
            fingerprint="old",
            extra_meta={"bars_fp": "b0"},
        )
        meta_path = _paths("oo", "000001")["meta"]
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["names"] = ["x", "y"]
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        self._save("2024-04-01", codes, "open")
        self._save("2024-04-02", codes, "open")
        self._save("2024-04-03", codes, "open")
        from core.research.materialize.consumer import materialize_writer

        with materialize_writer():
            ok = write_stock_panel(
                "oo",
                "000001",
                dates=["2024-04-02"],
                X=np.asarray([[9.0]]),
                names=["x"],
                fingerprint="new",
                extra_meta={"bars_fp": "b1"},
            )
        self.assertTrue(ok)
        self.assertFalse(self._on_disk("2024-04-01", codes, "open"))
        self.assertFalse(self._on_disk("2024-04-02", codes, "open"))
        self.assertTrue(self._on_disk("2024-04-03", codes, "open"))

    def test_schema_mismatch_still_drops_npz_dates(self) -> None:
        import json

        from core.research.feature_lake import _paths, write_stock_panel
        from core.research.lake_bridge import invalidate_l1_for_code

        codes = ["000001", "000002"]
        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-08-01"],
            X=np.asarray([[1.0]]),
            names=["f"],
            fingerprint="fp",
            extra_meta={"bars_fp": "b"},
        )
        meta_path = _paths("oo", "000001")["meta"]
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["schema"] = 0
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        self._save("2024-08-01", codes, "open")
        self._save("2024-08-02", codes, "open")
        invalidate_l1_for_code("000001", families=["oo"])
        self.assertFalse(self._on_disk("2024-08-01", codes, "open"))
        self.assertTrue(self._on_disk("2024-08-02", codes, "open"))

    def test_old_tmp_removed_fresh_tmp_kept(self) -> None:
        import time

        from core.research.day_cube import _disk_paths
        from core.research.day_cube_gc import drop_dates
        from core.research.feature_registry import universe_hash

        codes = ["a", "b"]
        self._save("2024-08-11", codes, "open")
        clock_dir = _disk_paths(universe_hash(codes), "open", "2024-08-11")["dir"]
        old = os.path.join(clock_dir, ".cube_old.tmp")
        fresh = os.path.join(clock_dir, ".cube_fresh.tmp")
        for path in (old, fresh):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("tmp")
        ago = time.time() - 7200
        os.utime(old, (ago, ago))
        drop_dates(["2024-08-11"])
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(fresh))

    def test_invalidate_many_prunes_cold_hash(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.feature_registry import universe_hash
        from core.research.lake_bridge import invalidate_l1_many

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-10-01"],
            X=np.asarray([[1.0]]),
            names=["f"],
            fingerprint="fp",
            extra_meta={"bars_fp": "bars"},
        )
        keep_codes = ["000001", "000002"]
        cold_codes = ["000008", "000009"]
        self._save("2024-10-01", keep_codes, "open")
        self._save("2024-10-02", keep_codes, "open")
        self._save("2024-10-02", cold_codes, "open")
        with mock.patch(
            "core.research.day_cube_gc.resolve_keep_hashes",
            return_value={universe_hash(keep_codes)},
        ):
            out = invalidate_l1_many(["000001"])
        self.assertGreaterEqual(out["l2_dates_dropped"], 1)
        self.assertEqual(out["l2_hashes_pruned"], 1)
        self.assertFalse(self._on_disk("2024-10-01", keep_codes, "open"))
        self.assertTrue(self._on_disk("2024-10-02", keep_codes, "open"))
        self.assertFalse(self._on_disk("2024-10-02", cold_codes, "open"))
