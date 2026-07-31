import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases
from quant.ops.eval_routing_map import build_eval_routing_map
from services.eval_service import EvalService
from quant.services.quant_report_index import list_quant_reports


class TestP26EvalRoutingMap(unittest.TestCase):
    def test_all_routing_expect_ok(self):
        out = build_eval_routing_map()
        self.assertTrue(out["success"])
        self.assertEqual(out["count"], len(load_cases()))
        self.assertEqual(out["ok_count"], out["with_expect"])
        for row in out["cases"]:
            if row["has_routing_expect"]:
                self.assertTrue(row["ok"], row)

    def test_eval_service_list_routing(self):
        out = EvalService().list_routing()
        self.assertGreaterEqual(out["with_expect"], 5)


class TestP26ReportShareUrl(unittest.TestCase):
    def test_share_url_in_list(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "quant_daily_20260719.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            listed = list_quant_reports(reports_dir=tmp, limit=5)
        self.assertEqual(
            listed["reports"][0]["share_url"],
            "/api/quant/reports/quant_daily_20260719.html",
        )


class TestP26Api(unittest.TestCase):
    def test_evals_routing_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/routing")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ok_count"], data["with_expect"])

    def test_quant_reports_share_url(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "count": 1,
            "reports": [
                {
                    "filename": "quant_daily_20260719.md",
                    "share_url": "/api/quant/reports/quant_daily_20260719.md",
                }
            ],
        }
        with unittest.mock.patch.object(
            deps.quant, "list_report_archive", return_value=mock
        ):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/reports")
        self.assertEqual(res.status_code, 200)
        self.assertIn("share_url", res.json()["reports"][0])


if __name__ == "__main__":
    unittest.main()
