import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases, run_skills
from quant.services.signal_config_preview import build_config_diff_preview


class TestConfigDiffPreview(unittest.TestCase):
    def test_preview_from_report(self):
        report = {
            "weight_suggest": {
                "success": True,
                "config_diff": {
                    "success": True,
                    "changes": {"momentum": {"from": 0.4, "to": 0.43, "delta": 0.03}},
                    "patch": {"weights": {"momentum": 0.43}},
                },
            },
            "threshold_suggest": {
                "success": True,
                "config_diff": {
                    "success": True,
                    "changes": {"wait": {"from": 55, "to": 57, "delta": 2}},
                    "patch": {"stance_thresholds": {"wait": 57}},
                },
            },
        }
        out = build_config_diff_preview(report)
        self.assertTrue(out["ok"])
        self.assertTrue(out["weights"]["success"])
        self.assertTrue(out["thresholds"]["success"])

    def test_api_diff_preview(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock = {
            "success": True,
            "ok": True,
            "readonly": True,
            "weights": {"success": True, "changes": {}},
            "thresholds": {"success": False},
        }
        with patch.object(deps.quant, "build_config_diff_preview", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/signal/config/diff-preview")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["readonly"])


class TestQuantWeightDiffGolden(unittest.TestCase):
    def test_quant_weight_diff_offline(self):
        case = next(c for c in load_cases() if c["id"] == "quant_weight_diff")
        out = run_skills(case, use_mock=True)
        quant = out["bundled"]["quant"]
        self.assertTrue(quant.get("success"))
        self.assertTrue((quant.get("config_diff") or {}).get("success"))


if __name__ == "__main__":
    unittest.main()
