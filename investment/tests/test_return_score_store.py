"""收益打分模型草稿 / promote（不写 weights）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.return_score import ReturnScoreModel
from core.signal import return_score_store as store


class TestReturnScoreStore(unittest.TestCase):
    def test_draft_promote_roundtrip(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5, "value": -0.2},
            standardized=False,
            sample_count=40,
            horizon_days=3,
        )
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            active = os.path.join(td, "active.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                saved = store.save_return_model_draft(model, meta={"lookback": 60})
                self.assertTrue(saved["success"])
                self.assertTrue(os.path.isfile(draft))
                loaded, meta = store.load_return_model(prefer_active=True)
                self.assertIsNotNone(loaded)
                self.assertEqual(meta["role"], "draft")
                self.assertAlmostEqual(loaded.coefficients["momentum"], 0.5)
                prom = store.promote_return_model_draft(note="test")
                self.assertTrue(prom["success"])
                self.assertTrue(os.path.isfile(active))
                loaded2, meta2 = store.load_return_model(prefer_active=True)
                self.assertEqual(meta2["role"], "active")
                self.assertAlmostEqual(loaded2.intercept, 0.1)


if __name__ == "__main__":
    unittest.main()
