import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.preset_check import check_daily_presets
from quant.services.signal_config_preview import build_config_diff_preview, export_config_diff_bundle


class TestDiffBundleExport(unittest.TestCase):
    def test_export_bundle_from_preview(self):
        preview = build_config_diff_preview(
            weight_suggest={
                "success": True,
                "config_diff": {
                    "success": True,
                    "changes": {"momentum": {"from": 0.4, "to": 0.43, "delta": 0.03}},
                    "patch": {"weights": {"momentum": 0.43}},
                },
            }
        )
        out = export_config_diff_bundle(preview)
        self.assertTrue(out["success"])
        self.assertIn("weights", out["merged_patch"])

    def test_api_diff_export(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock = {
            "success": True,
            "filename": "signal_config_diff_bundle.json",
            "merged_patch": {"weights": {"momentum": 0.43}},
        }
        with patch.object(deps.quant, "export_config_diff_bundle", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/signal/config/diff-export")
        self.assertEqual(res.status_code, 200)
        self.assertIn("merged_patch", res.json())


class TestPresetCheck(unittest.TestCase):
    def test_quant_paper_preset_flags(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"], msg="; ".join(out.get("failures") or []))
        self.assertIn("quant_paper", out.get("checked") or [])


class TestSignalDiffExportCli(unittest.TestCase):
    def test_cli_mocked(self):
        from research import signal_diff_export_run

        mock = {
            "success": True,
            "merged_patch": {"weights": {"momentum": 0.43}},
        }
        with patch("research.signal_diff_export_run.QuantService") as mock_cls:
            mock_cls.return_value.export_config_diff_bundle.return_value = mock
            code = signal_diff_export_run.main(["--fresh"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
