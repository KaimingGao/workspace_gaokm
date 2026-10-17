"""L1 Feature Lake：写读、fingerprint miss、尾窗 merge。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestFeatureLake(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"

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

    def test_upsert_tail_keeps_prefix(self) -> None:
        from core.research.feature_lake import load_stock_panel, upsert_tail, write_stock_panel

        write_stock_panel(
            "co",
            "300001",
            dates=["2024-03-01", "2024-03-02", "2024-03-03"],
            X=np.asarray([[1.0], [2.0], [3.0]]),
            names=["x"],
            fingerprint="old",
        )
        ok = upsert_tail(
            "co",
            "300001",
            dates=["2024-03-03", "2024-03-04"],
            X=np.asarray([[30.0], [40.0]]),
            names=["x"],
            fingerprint="new",
            keep_from="2024-03-03",
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
