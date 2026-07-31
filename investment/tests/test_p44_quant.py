import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.readme_check import check_readme_coverage
from services.eval_service import EvalService


class TestP44ReadmeCheck(unittest.TestCase):
    def test_check_readme_coverage_ok(self):
        out = check_readme_coverage()
        self.assertTrue(out["ok"], out.get("failures"))
        self.assertEqual(out["present_count"], out["total_dirs"])
        self.assertGreaterEqual(len(out.get("entries") or []), 30)

    def test_run_readme_check_cli(self):
        import evals.run_readme_check as mod

        self.assertEqual(mod.main(), 0)

    def test_eval_service_summary_includes_readme(self):
        summary = EvalService().summary()
        self.assertIn("readme", summary)
        self.assertTrue(summary["readme"].get("ok"))

    def test_eval_service_run_with_presets_includes_readme(self):
        report = EvalService().run(use_mock=True, with_presets=True, save=False)
        self.assertIn("readme", report)
        self.assertTrue(report["readme"].get("ok"))


class TestP44ReadmeEvalsApi(unittest.TestCase):
    def test_evals_readme_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/readme")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(data["present_count"], 30)

    def test_evals_summary_readme_field(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/summary")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["readme"]["ok"])


if __name__ == "__main__":
    unittest.main()
