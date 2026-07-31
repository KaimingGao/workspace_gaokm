import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases, run_skills
from research.daily_check import check_daily_last_run, main as daily_check_main
from quant.services.quant_service import QuantService


class TestWatchingFileApi(unittest.TestCase):
    def test_read_watching_file_missing(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "data")) as tmp:
            path = os.path.join(tmp, "watching.json")
            with patch("core.watching_store.WATCHING_PATH", path):
                out = QuantService().read_watching_file()
        self.assertFalse(out["exists"])

    def test_api_watching_file(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock = {
            "ok": True,
            "exists": True,
            "watching": {"name": "default", "sources": [], "watchlist": ["600519"]},
        }
        with patch.object(deps.quant, "read_watching_file", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/watching/file")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["exists"])


class TestDailyCheck(unittest.TestCase):
    def test_missing_file_ok_by_default(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "data")) as tmp:
            out = check_daily_last_run(os.path.join(tmp, "missing.json"))
        self.assertTrue(out["ok"])

    def test_failed_run(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "data")) as tmp:
            path = os.path.join(tmp, "daily_last_run.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ok": False, "failures": ["quant_report: boom"]}, f)
            out = check_daily_last_run(path)
            self.assertFalse(out["ok"])
            code = daily_check_main(["--path", path])
        self.assertEqual(code, 1)


class TestQuantHealthGolden(unittest.TestCase):
    def test_quant_health_case_offline(self):
        case = next(c for c in load_cases() if c["id"] == "quant_health")
        out = run_skills(case, use_mock=True)
        quant = out["bundled"]["quant"]
        self.assertTrue(quant.get("success"))
        self.assertTrue(quant.get("ok"))
        self.assertEqual(quant.get("task"), "health")


if __name__ == "__main__":
    unittest.main()
