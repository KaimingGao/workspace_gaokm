"""R2 · signal_config 草稿校验 / 保存 / promote（不静默写生产）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestSignalConfigDraft(unittest.TestCase):
    def test_validate_rejects_unknown_keys(self):
        from core.signal_config_draft import validate_config_payload

        ok, _, errors = validate_config_payload(
            {"weights": {"momentum": 1.0}, "evil": 1}
        )
        self.assertFalse(ok)
        self.assertTrue(any("不允许" in e for e in errors))

    def test_validate_weights_sum(self):
        from core.signal_config_draft import validate_config_payload

        ok, cfg, errors = validate_config_payload(
            {"weights": {"momentum": 0.5, "volume_price": 0.5}}
        )
        self.assertTrue(ok, errors)
        self.assertEqual(cfg["weights"]["momentum"], 0.5)

        bad, _, errs = validate_config_payload(
            {"weights": {"momentum": 0.9, "volume_price": 0.9}}
        )
        self.assertFalse(bad)
        self.assertTrue(any("之和" in e for e in errs))

    def test_save_and_promote_with_backup(self):
        from core.signal_config_draft import (
            diff_against_production,
            promote_draft,
            save_draft,
            validate_config_payload,
        )

        with tempfile.TemporaryDirectory() as td:
            prod = os.path.join(td, "signal_config.json")
            draft_path = os.path.join(td, "signal_config_draft.json")
            backup_dir = os.path.join(td, "config_backups")
            base = {
                "version": 1,
                "weights": {"momentum": 0.4, "volume_price": 0.6},
                "rank": {"min_score": 55},
            }
            with open(prod, "w", encoding="utf-8") as f:
                json.dump(base, f)

            draft = {
                "weights": {"momentum": 0.45, "volume_price": 0.55},
                "rank": {"min_score": 60},
            }
            ok, normalized, errors = validate_config_payload(draft)
            self.assertTrue(ok, errors)

            with patch("core.signal_config_draft.BACKUP_DIR", backup_dir), patch(
                "core.signal_config_draft.load_signal_config",
                return_value=dict(base),
            ), patch(
                "core.north_star.append_ttm_event", return_value=None
            ):
                saved = save_draft(draft, note="test", path=draft_path)
                self.assertTrue(saved.get("ok"), saved)
                self.assertTrue(os.path.isfile(draft_path))

                with open(prod, encoding="utf-8") as f:
                    before = json.load(f)
                self.assertEqual(before["rank"]["min_score"], 55)

                out = promote_draft(
                    draft=normalized,
                    note="test promote",
                    config_path=prod,
                    draft_path=draft_path,
                )
                self.assertTrue(out.get("ok"), out)
                self.assertTrue(out.get("backup"))
                self.assertTrue(os.path.isfile(out["backup"]))

                with open(prod, encoding="utf-8") as f:
                    after = json.load(f)
                self.assertEqual(after["rank"]["min_score"], 60)
                self.assertAlmostEqual(after["weights"]["momentum"], 0.45)

            diff = diff_against_production(
                draft,
                production={"weights": base["weights"], "rank": base["rank"]},
            )
            self.assertTrue(diff.get("ok"))
            self.assertGreaterEqual(diff.get("change_count", 0), 1)


if __name__ == "__main__":
    unittest.main()
