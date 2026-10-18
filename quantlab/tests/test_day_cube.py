"""L2 DayCube：构建、z、落盘、tau slim。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestDayCube(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_DAY_CUBE_DIR": os.environ.get("QUANTLAB_DAY_CUBE_DIR"),
            "QUANTLAB_DAY_CUBE": os.environ.get("QUANTLAB_DAY_CUBE"),
        }
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_DAY_CUBE"] = "1"
        from core.research.day_cube import clear_memory

        clear_memory()

    def tearDown(self) -> None:
        from core.research.day_cube import clear_memory

        clear_memory()
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_build_and_z(self) -> None:
        from core.research.day_cube import build_day_cube
        from core.research.feature_standardize import cross_section_zscore_matrix

        feats = {
            "a": {"f1": 1.0, "f2": 10.0},
            "b": {"f1": 3.0, "f2": 30.0},
            "c": {"f1": 5.0, "f2": 50.0},
        }
        cube = build_day_cube("2024-05-01", feats, names=["f1", "f2"], clock="open")
        self.assertIsNotNone(cube)
        assert cube is not None
        self.assertEqual(sorted(cube.codes), ["a", "b", "c"])
        z = cube.z_matrix()
        # 与 feature_standardize 单日组对齐
        ref = cross_section_zscore_matrix(cube.X_raw, ["2024-05-01"] * 3)
        np.testing.assert_allclose(z, ref, equal_nan=True)

    def test_save_load_roundtrip(self) -> None:
        from core.research.day_cube import build_day_cube, load_day_cube, save_day_cube

        feats = {"x": {"g": 1.0}, "y": {"g": 2.0}}
        cube = build_day_cube("2024-06-01", feats, clock="open")
        self.assertTrue(save_day_cube(cube))
        from core.research.day_cube import clear_memory

        clear_memory()
        hit = load_day_cube("2024-06-01", ["x", "y"], clock="open")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.date, "2024-06-01")
        np.testing.assert_allclose(hit.X_raw, cube.X_raw)

    def test_universe_mismatch_miss(self) -> None:
        from core.research.day_cube import build_day_cube, clear_memory, load_day_cube, save_day_cube

        cube = build_day_cube(
            "2024-07-01",
            {"a": {"v": 1.0}, "b": {"v": 2.0}},
            clock="open",
        )
        save_day_cube(cube)
        clear_memory()
        # 宇宙变了 → miss
        self.assertIsNone(load_day_cube("2024-07-01", ["a", "b", "c"], clock="open"))

    def test_persist_tau_pool_slim(self) -> None:
        from core.research.day_cube import load_tau_pool_day, persist_tau_pool_as_cubes

        codes = ["600000", "600001"]
        pool = {
            "2024-08-01": {
                "pool_gaps": [1.0, 3.0],
                "sector_gap_breadth": 0.5,
                "gaps_by_code": {"600000": 1.0, "600001": 3.0},
                "ref_by_code": {"600000": 2.0, "600001": 2.0},
            }
        }
        n = persist_tau_pool_as_cubes(pool, codes, clock="open")
        self.assertEqual(n, 1)
        from core.research.day_cube import clear_memory

        clear_memory()
        hit = load_tau_pool_day("2024-08-01", codes, clock="open")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(float(hit["sector_gap_breadth"]), 0.5)

    def test_batch_score_builds_cube(self) -> None:
        from core.research.batch_score import ensure_day_cube_for_items

        items = [
            {
                "stock_code": "1",
                "as_of": "2024-09-01",
                "features_tau": {"gap_pct": 1.0, "z": 0.2},
            },
            {
                "stock_code": "2",
                "as_of": "2024-09-01",
                "features_tau": {"gap_pct": 2.0, "z": 0.4},
            },
        ]
        cube = ensure_day_cube_for_items(items, date="2024-09-01", clock="open")
        self.assertIsNotNone(cube)
        assert cube is not None
        self.assertEqual(cube.date, "2024-09-01")
        self.assertGreaterEqual(len(cube.names), 1)

    def test_rescore_single_item_with_day_cube(self) -> None:
        """有 DayCube 时允许单票 rescore（μ/σ 信 cube）。"""
        from unittest.mock import patch

        from core.research.day_cube import build_day_cube
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        feats = {
            "000001": {"gap_pct": 1.0, "x": 0.1},
            "000002": {"gap_pct": 3.0, "x": 0.3},
            "000003": {"gap_pct": 5.0, "x": 0.5},
        }
        cube = build_day_cube("2024-10-01", feats, clock="open")
        self.assertIsNotNone(cube)
        item = {
            "stock_code": "000001",
            "sub_scores": {"gap_pct": 1.0, "x": 0.1},
            "features_tau": {"gap_pct": 1.0, "x": 0.1},
            "features_co": {"gap_pct": 1.0},
        }
        # 无模型时仍应走完单票路径（不因 len<2 提前 return）
        with patch(
            "core.signal.cross_section_rescore._rescore_oo_ridge"
        ) as oo, patch(
            "core.signal.cross_section_rescore._rescore_tau"
        ) as tau, patch(
            "core.signal.cross_section_rescore._rescore_co"
        ) as co, patch(
            "core.signal.cross_section_rescore._rescore_horizons"
        ) as hz:
            out = rescore_fitted_cross_section(
                [item], score_codes={"000001"}, day_cube=cube
            )
        self.assertEqual(len(out), 1)
        oo.assert_called_once()
        tau.assert_called_once()
        co.assert_called_once()
        hz.assert_called_once()
        # 无 cube 时单票应直接跳过
        with patch(
            "core.signal.cross_section_rescore._rescore_oo_ridge"
        ) as oo2:
            rescore_fitted_cross_section([item], score_codes={"000001"}, day_cube=None)
        oo2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
