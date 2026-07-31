import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP31EvalRouting(unittest.TestCase):
    def test_build_eval_routing_map_from_quant_ops(self):
        from quant.ops.eval_routing_map import build_eval_routing_map

        out = build_eval_routing_map()
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["with_expect"], 5)

    def test_eval_service_uses_quant_ops(self):
        from services.eval_service import EvalService

        out = EvalService().list_routing()
        self.assertTrue(out["success"])


class TestP32QuantPackageInfo(unittest.TestCase):
    def test_build_quant_package_info(self):
        from quant.ops.package_info import build_quant_package_info

        out = build_quant_package_info()
        self.assertTrue(out["success"])
        self.assertEqual(out["package"], "quant")
        self.assertIn("services", out["modules"])
        self.assertIn("eval_routing_map", out["modules"]["ops"])
        self.assertTrue(out.get("shims_removed"))
        self.assertEqual(out["shim_paths"], [])
        self.assertIn("services/quant_service.py", out["removed_shim_paths"])

    def test_quant_service_wrapper(self):
        from quant.services.quant_service import QuantService

        out = QuantService().build_package_info()
        self.assertGreaterEqual(out["module_count"], 10)

    def test_quant_package_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/quant/package")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertIn("skill", body["subpackages"])


class TestP33CanonicalCliImports(unittest.TestCase):
    def test_research_cli_imports_quant_service(self):
        import research.quant_export_run as mod

        self.assertIn("quant.services.quant_service", mod.QuantService.__module__)

    def test_preset_check_imports_quant_ops(self):
        import evals.preset_check as mod

        self.assertIs(
            mod.resolve_daily_preset,
            importlib.import_module("quant.ops.daily_presets").resolve_daily_preset,
        )

    def test_daily_run_imports_quant_presets(self):
        import research.daily_run as mod
        from quant.ops.daily_presets import DAILY_PRESETS as canonical

        self.assertIs(mod.DAILY_PRESETS, canonical)


if __name__ == "__main__":
    unittest.main()
