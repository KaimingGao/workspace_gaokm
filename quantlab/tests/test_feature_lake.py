"""L1 Feature Lake：写读、fingerprint miss、尾窗 merge。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


class TestFeatureLake(unittest.TestCase):
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

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_write_load_roundtrip(self) -> None:
        from core.research.feature_lake import load_stock_panel, write_stock_panel

        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        names = ["f_a", "f_b"]
        X = np.asarray([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float64)
        y = np.asarray([0.1, 0.2, 0.3], dtype=np.float64)
        ok = write_stock_panel(
            "oo",
            "600000",
            dates=dates,
            X=X,
            names=names,
            fingerprint="fp1",
            y=y,
        )
        self.assertTrue(ok)
        hit = load_stock_panel("oo", "600000", fingerprint="fp1")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["dates"], dates)
        self.assertEqual(hit["names"], names)
        np.testing.assert_allclose(hit["X"], X)
        np.testing.assert_allclose(hit["y"], y)

    def test_fingerprint_miss(self) -> None:
        from core.research.feature_lake import load_stock_panel, write_stock_panel

        X = np.ones((2, 1), dtype=np.float64)
        write_stock_panel(
            "oo",
            "600001",
            dates=["2024-01-02", "2024-01-03"],
            X=X,
            names=["f"],
            fingerprint="aaa",
        )
        self.assertIsNone(load_stock_panel("oo", "600001", fingerprint="bbb"))
        self.assertIsNotNone(load_stock_panel("oo", "600001", fingerprint="aaa"))

    def test_refuse_shorter_suffix_overwrite(self) -> None:
        from core.research.feature_lake import load_stock_panel, write_stock_panel

        long_dates = [f"2024-01-{d:02d}" for d in range(1, 11)]
        write_stock_panel(
            "tc",
            "000010",
            dates=long_dates,
            X=np.asarray([[float(i)] for i in range(10)]),
            names=["f"],
            fingerprint="long-fp",
            y=np.asarray([0.1 * i for i in range(10)]),
            extra_meta={"bars_fp": "long-bars", "end_date": long_dates[-1]},
        )
        short_dates = long_dates[-4:]
        ok = write_stock_panel(
            "tc",
            "000010",
            dates=short_dates,
            X=np.asarray([[99.0], [99.0], [99.0], [99.0]]),
            names=["f"],
            fingerprint="short-fp",
            y=np.asarray([9.0, 9.0, 9.0, 9.0]),
            extra_meta={"bars_fp": "short-bars", "end_date": short_dates[-1]},
        )
        self.assertTrue(ok)
        hit = load_stock_panel("tc", "000010", fingerprint=None)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["dates"], long_dates)
        self.assertEqual(hit.get("_meta", {}).get("fingerprint"), "long-fp")
        np.testing.assert_allclose(hit["X"].ravel()[0], 0.0)

    def test_load_day_rows(self) -> None:
        from core.research.feature_lake import load_day_rows, write_stock_panel

        write_stock_panel(
            "tc",
            "000001",
            dates=["2024-02-01", "2024-02-02"],
            X=np.asarray([[1.5, 2.5], [3.5, 4.5]]),
            names=["g", "h"],
            fingerprint="z",
        )
        write_stock_panel(
            "tc",
            "000002",
            dates=["2024-02-02"],
            X=np.asarray([[9.0, 8.0]]),
            names=["g", "h"],
            fingerprint="z",
        )
        rows = load_day_rows("tc", ["000001", "000002", "999999"], "2024-02-02")
        self.assertIn("000001", rows)
        self.assertIn("000002", rows)
        self.assertNotIn("999999", rows)
        self.assertAlmostEqual(rows["000001"]["g"], 3.5)

    def test_slice_stock_panels_opens_each_code_once(self) -> None:
        from core.research import feature_lake
        from core.research.feature_lake import slice_stock_panels, write_stock_panel

        write_stock_panel(
            "tc",
            "000001",
            dates=["2024-02-01", "2024-02-02", "2024-02-03", "2024-02-04"],
            X=np.asarray([[1.0], [2.0], [3.0], [4.0]]),
            names=["g"],
            fingerprint="z",
        )
        real = feature_lake.load_stock_panel
        calls = []

        def _wrapped(family, code, fingerprint=None):
            calls.append((family, code))
            return real(family, code, fingerprint=fingerprint)

        with mock.patch.object(feature_lake, "load_stock_panel", side_effect=_wrapped):
            sliced = slice_stock_panels(
                "tc",
                ["000001", "999999"],
                ["2024-02-01", "2024-02-03", "2024-02-04"],
            )
        self.assertEqual(calls, [("tc", "000001"), ("tc", "999999")])
        names, by_day = sliced["000001"]
        self.assertEqual(names, ["g"])
        self.assertEqual(set(by_day), {"2024-02-01", "2024-02-03", "2024-02-04"})
        self.assertAlmostEqual(float(by_day["2024-02-03"][0]), 3.0)
        self.assertNotIn("2024-02-02", by_day)

    def test_slice_skips_bad_code(self) -> None:
        from core.research import feature_lake
        from core.research.feature_lake import slice_stock_panels, write_stock_panel

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-02-01"],
            X=np.asarray([[1.5]]),
            names=["g"],
            fingerprint="z",
        )
        real = feature_lake.load_stock_panel

        def _wrapped(family, code, fingerprint=None):
            if code == "000002":
                raise RuntimeError("bad npz")
            return real(family, code, fingerprint=fingerprint)

        with mock.patch.object(feature_lake, "load_stock_panel", side_effect=_wrapped):
            sliced = slice_stock_panels("oo", ["000001", "000002"], ["2024-02-01"])
        self.assertIsNotNone(sliced)
        assert sliced is not None
        self.assertIn("000001", sliced)
        self.assertNotIn("000002", sliced)

    def test_slice_cancel_returns_none(self) -> None:
        from core.research.feature_lake import slice_stock_panels, write_stock_panel

        write_stock_panel(
            "oo",
            "000001",
            dates=["2024-02-01"],
            X=np.asarray([[1.0]]),
            names=["g"],
            fingerprint="z",
        )
        sliced = slice_stock_panels(
            "oo",
            ["000001"],
            ["2024-02-01"],
            cancel_cb=lambda: True,
        )
        self.assertIsNone(sliced)

    def test_finite_feature_dict_skips_bad_cell(self) -> None:
        from core.research.feature_lake import finite_feature_dict

        feats = finite_feature_dict(["a", "", "c"], [1.0, "nope", 3.0])
        self.assertEqual(feats, {"a": 1.0, "c": 3.0})
        self.assertEqual(finite_feature_dict(["a"], None), {})

    def test_merge_then_write_keeps_prefix(self) -> None:
        from core.research.feature_lake import load_stock_panel, write_stock_panel
        from core.research.incremental_panel import merge_matrix_rows

        write_stock_panel(
            "co",
            "300001",
            dates=["2024-03-01", "2024-03-02", "2024-03-03"],
            X=np.asarray([[1.0], [2.0], [3.0]]),
            names=["x"],
            fingerprint="old",
            y=np.asarray([0.1, 0.2, 0.3]),
        )
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
        ok = write_stock_panel(
            "co",
            "300001",
            dates=dates_m,
            X=X_m,
            names=names_m,
            fingerprint="new",
            y=y_m,
        )
        self.assertTrue(ok)
        hit = load_stock_panel("co", "300001", fingerprint="new")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["dates"], ["2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"])
        np.testing.assert_allclose(hit["X"].ravel(), [1.0, 2.0, 30.0, 40.0])

    def test_disable_env(self) -> None:
        from core.research import feature_lake as lake

        os.environ["QUANTLAB_FEATURE_LAKE"] = "0"
        self.assertFalse(lake.enabled())
        self.assertFalse(
            lake.write_stock_panel(
                "oo",
                "1",
                dates=["2024-01-01"],
                X=np.ones((1, 1)),
                names=["a"],
                fingerprint="x",
            )
        )

class TestFeatureRegistry(unittest.TestCase):
    def test_universe_hash_order_invariant(self) -> None:
        from core.research.feature_registry import universe_hash

        self.assertEqual(universe_hash(["b", "a"]), universe_hash(["a", "b"]))
        self.assertNotEqual(universe_hash(["a"]), universe_hash(["a", "b"]))

if __name__ == "__main__":
    unittest.main()
