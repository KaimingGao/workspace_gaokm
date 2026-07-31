import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.watching_health import check_watching_health
from quant.ops.daily_health import build_daily_health
from quant.services.quant_report_index import list_quant_reports, read_quant_report_file
from quant.services.quant_service import QuantService
from quant.skill.engine import QuantEngine


class TestWatchingHealth(unittest.TestCase):
    def test_missing_watching(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = check_watching_health(path=os.path.join(tmp, "missing.json"))
        self.assertFalse(out["success"])
        self.assertFalse(out["exists"])

    def test_empty_watchlist_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "sources": [{"type": "static", "codes": ["茅台"]}],
                        "watchlist": [],
                        "updated_at": "2026-07-19T10:00:00",
                    },
                    f,
                )
            out = check_watching_health(path=path)
        self.assertFalse(out["success"])
        self.assertIn("watchlist", out["issues"][0])

    def test_manual_mode_empty_sources_ok(self):
        """sources 为空但有 watchlist = 手动模式，不应判失败。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "sources": [],
                        "watchlist": ["600519", "000001"],
                        "updated_at": "2026-07-28T10:00:00",
                    },
                    f,
                )
            out = check_watching_health(path=path)
        self.assertTrue(out["success"])
        self.assertEqual(out["issues"], [])
        self.assertTrue(any("手动模式" in w for w in out["warnings"]))


class TestQuantReportIndex(unittest.TestCase):
    def test_list_and_read_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "quant_daily_20260719.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# test")
            listed = list_quant_reports(reports_dir=tmp, limit=10)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["reports"][0]["format"], "markdown")
            read = read_quant_report_file("quant_daily_20260719.md", reports_dir=tmp)
            self.assertTrue(read["success"])
            self.assertIn("# test", read["content"])

    def test_reject_invalid_filename(self):
        out = read_quant_report_file("../evil.txt")
        self.assertFalse(out["success"])


class TestDailyHealth(unittest.TestCase):
    def test_build_health_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily = MagicMock()
            daily.load_last_run.return_value = {"success": True, "empty": True}
            with patch(
                "quant.ops.daily_health.check_watching_health",
                return_value={"success": True, "exists": True, "issues": [], "warnings": []},
            ), patch(
                "quant.ops.daily_health.list_quant_reports",
                return_value={"success": True, "reports": [], "count": 0, "reports_dir": tmp},
            ):
                out = build_daily_health(daily=daily)
        self.assertTrue(out["success"])


class TestQuantHealthTask(unittest.TestCase):
    def test_health_task(self):
        engine = QuantEngine()
        mock_svc = MagicMock()
        mock_svc.build_health_summary.return_value = {"success": True, "ok": True, "task": "health"}
        engine._svc = mock_svc
        out = engine.run({"task": "health"})
        self.assertTrue(out["ok"])


class TestQuantHealthApi(unittest.TestCase):
    def test_api_daily_health(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock = {"success": True, "ok": True, "issues": [], "warnings": []}
        with patch.object(deps.quant, "build_health_summary", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/daily/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_api_quant_reports(self):
        from fastapi.testclient import TestClient

        import web.app as web_app

        with patch.object(
            deps.quant,
            "list_report_archive",
            return_value={"success": True, "count": 0, "reports": []},
        ):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/reports")
        self.assertEqual(res.status_code, 200)


class TestInferQuantHealthRouting(unittest.TestCase):
    def test_infer_health_task(self):
        from agent.routing import infer_quant_task

        self.assertEqual(infer_quant_task("量化系统状态怎么样"), "health")


if __name__ == "__main__":
    unittest.main()
