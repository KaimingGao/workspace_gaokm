"""OO 单遍：DayCube μ/σ 与 cross_section_zscore_dicts 对齐。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestBatchScoreCubeOo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_DAY_CUBE_DIR": os.environ.get("QUANTLAB_DAY_CUBE_DIR"),
            "QUANTLAB_DAY_CUBE": os.environ.get("QUANTLAB_DAY_CUBE"),
            "QUANTLAB_BATCH_SCORE": os.environ.get("QUANTLAB_BATCH_SCORE"),
        }
        os.environ["QUANTLAB_DAY_CUBE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_DAY_CUBE"] = "1"
        os.environ["QUANTLAB_BATCH_SCORE"] = "1"
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

    def test_oo_cube_z_matches_standardize(self) -> None:
        from core.research.batch_score import ensure_day_cube_for_items
        from core.signal.return_score import ReturnScoreModel, _batch_feature_rows

        items = [
            {"stock_code": "a", "as_of": "2024-10-01", "sub_scores": {"f1": 1.0, "f2": 10.0}},
            {"stock_code": "b", "as_of": "2024-10-01", "sub_scores": {"f1": 3.0, "f2": 30.0}},
            {"stock_code": "c", "as_of": "2024-10-01", "sub_scores": {"f1": 5.0, "f2": 50.0}},
        ]
        cube = ensure_day_cube_for_items(items, date="2024-10-01", clock="open")
        self.assertIsNotNone(cube)
        model = ReturnScoreModel(
            intercept=0.0,
            coefficients={"f1": 1.0, "f2": 0.5},
            feature_zscore=True,
            zscore_scope="cross_section",
        )
        z_cube = _batch_feature_rows(items, model, day_cube=cube)
        z_ref = _batch_feature_rows(items, model, day_cube=None)
        self.assertIsNotNone(z_cube)
        self.assertIsNotNone(z_ref)
        assert z_cube is not None and z_ref is not None
        for a, b in zip(z_cube, z_ref):
            for k in ("f1", "f2"):
                self.assertAlmostEqual(float(a[k]), float(b[k]), places=6)

    def test_sub_scores_enter_cube(self) -> None:
        from core.research.batch_score import features_by_code_from_items

        feats = features_by_code_from_items(
            [{"stock_code": "x", "sub_scores": {"z_oo_mom": 1.2, "raw_aoo_gap": 0.3}}]
        )
        self.assertIn("x", feats)
        self.assertIn("z_oo_mom", feats["x"])


if __name__ == "__main__":
    unittest.main()
