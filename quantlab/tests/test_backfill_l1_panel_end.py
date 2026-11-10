"""scripts/backfill_l1_panel_end：只读 npz 回填 panel_end。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import numpy as np


class BackfillL1PanelEndTests(unittest.TestCase):
    def test_dry_run_and_apply(self) -> None:
        from scripts.backfill_l1_panel_end import _backfill_one

        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "oo", "000001")
            os.makedirs(os.path.dirname(base), exist_ok=True)
            npz = base + ".npz"
            meta_path = base + ".meta.json"
            np.savez_compressed(
                npz,
                X=np.asarray([[1.0], [2.0], [3.0]]),
                dates=np.asarray(["2024-01-02", "2024-01-03", "2024-01-04"], dtype="U10"),
            )
            with open(meta_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "schema": 1,
                        "fingerprint": "fp",
                        "end_date": "2024-01-05",
                        "bars_fp": "bars",
                        "n_rows": 3,
                    },
                    fh,
                )
            dry = _backfill_one(meta_path, apply=False)
            self.assertTrue(dry.startswith("would_write"), dry)
            with open(meta_path, encoding="utf-8") as fh:
                before = json.load(fh)
            self.assertNotIn("panel_end", before)

            wrote = _backfill_one(meta_path, apply=True)
            self.assertTrue(wrote.startswith("wrote"), wrote)
            with open(meta_path, encoding="utf-8") as fh:
                after = json.load(fh)
            self.assertEqual(after.get("panel_end"), "2024-01-04")
            self.assertEqual(after.get("scored_through"), "2024-01-05")

            again = _backfill_one(meta_path, apply=True)
            self.assertEqual(again, "skip_has_panel_end")


if __name__ == "__main__":
    unittest.main()
