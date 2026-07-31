import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.ops.shim_audit import audit_quant_shim_imports, main as shim_audit_main


class TestP39ShimImportAudit(unittest.TestCase):
    def test_audit_passes_on_repo(self):
        out = audit_quant_shim_imports()
        self.assertTrue(out["success"])
        self.assertTrue(out["ok"], out.get("offenders"))
        self.assertGreater(out["scanned_files"], 50)

    def test_audit_cli_ok(self):
        self.assertEqual(shim_audit_main([]), 0)


class TestP39QuantPanelCiShortcut(unittest.TestCase):
    def test_post_quant_ci_eval_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/evals/run",
            json={
                "use_mock": True,
                "with_agent": False,
                "with_presets": True,
                "quant_only": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["total"], 9)

    def test_summary_lists_import_audit_command(self):
        from services.eval_service import EvalService

        cmds = EvalService().summary()["ci_commands"]
        self.assertIn("check_quant_imports", cmds)


if __name__ == "__main__":
    unittest.main()
