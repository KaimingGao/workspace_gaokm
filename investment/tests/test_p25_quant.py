import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import check_routing_expect, load_cases, run_skills
from services.eval_service import EvalService


class TestP25GoldenPortfolioBridge(unittest.TestCase):
    def test_case_exists_and_routing(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_bridge")
        self.assertEqual(check_routing_expect(case), [])

    def test_offline_mock_skills(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_bridge")
        run = run_skills(case, use_mock=True)
        self.assertTrue(run["skill_runs"][0]["result"].get("success"))


class TestP25EvalService(unittest.TestCase):
    def test_summary_includes_case_count(self):
        summary = EvalService().summary()
        self.assertTrue(summary["success"])
        self.assertGreaterEqual(summary["case_count"], 16)
        self.assertIn("quant_portfolio_bridge", summary["quant_case_ids"])
        self.assertTrue(summary["presets"]["ok"])

    def test_run_with_presets(self):
        report = EvalService().run(use_mock=True, with_agent=False, with_presets=True, save=False)
        self.assertIn("presets", report)
        self.assertTrue(report["presets"]["ok"])

    def test_list_cases_count(self):
        self.assertGreaterEqual(len(EvalService().list_cases()), 15)


class TestP25EvalsApi(unittest.TestCase):
    def test_evals_summary_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(data["case_count"], 16)

    def test_evals_presets_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/presets")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_evals_run_with_presets(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/evals/run",
            json={
                "case_id": "quant_portfolio_bridge",
                "use_mock": True,
                "with_agent": False,
                "with_presets": False,
            },
        )
        self.assertIn(res.status_code, (200, 422))
        data = res.json()
        self.assertEqual(data["total"], 1)


if __name__ == "__main__":
    unittest.main()
