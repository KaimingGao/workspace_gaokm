import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.eval_service import EvalService


class TestP38EvalServiceQuantOnly(unittest.TestCase):
    def test_run_quant_only_mock(self):
        report = EvalService().run(
            use_mock=True,
            with_agent=False,
            with_presets=True,
            quant_only=True,
            save=False,
        )
        self.assertTrue(report.get("quant_only"))
        self.assertEqual(report["total"], 9)
        self.assertTrue(report["ok"])
        ids = {c["id"] for c in report["cases"]}
        self.assertTrue(ids.issubset({c for c in ids if c.startswith("quant_")}))
        self.assertIn("quant_daily_neutral_section", ids)

    def test_run_quant_only_unknown_case(self):
        report = EvalService().run(
            case_id="buy_kuaishou",
            use_mock=True,
            quant_only=True,
            save=False,
        )
        self.assertFalse(report.get("ok"))
        self.assertIn("quant_*", report.get("error", ""))


class TestP38EvalsApiQuantOnly(unittest.TestCase):
    def test_evals_run_quant_only_api(self):
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
        self.assertTrue(data.get("quant_only"))
        self.assertEqual(data["total"], 9)
        self.assertTrue(data["ok"])

    def test_summary_web_quant_ci_command(self):
        summary = EvalService().summary()
        self.assertIn("web_quant_ci", summary["ci_commands"])


if __name__ == "__main__":
    unittest.main()
