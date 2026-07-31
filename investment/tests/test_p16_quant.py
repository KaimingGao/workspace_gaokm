import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.ops.daily_presets import list_daily_presets, resolve_daily_preset
from services.daily_service import DailyRunService
from services.eval_service import EvalService
from quant.services.quant_service import QuantService


class TestDailyPresets(unittest.TestCase):
    def test_list_presets(self):
        names = {p["name"] for p in list_daily_presets()}
        self.assertEqual(names, {"advisor", "quant", "full", "quant_paper"})

    def test_resolve_quant_preset(self):
        out = resolve_daily_preset("quant")
        flags = out["flags"]
        self.assertTrue(flags["quant_report"])
        self.assertTrue(flags["export_quant_report"])
        self.assertFalse(flags["paper_run"])

    def test_override_wins(self):
        out = resolve_daily_preset("quant", overrides={"paper_run": True})
        self.assertTrue(out["flags"]["paper_run"])

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            resolve_daily_preset("nope")


class TestDailyLastRun(unittest.TestCase):
    def test_eval_mock_writes_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            last_path = os.path.join(tmp, "daily_last_run.json")
            svc = DailyRunService(
                evals=EvalService(last_run_path=os.path.join(tmp, "evals.json")),
                last_run_path=last_path,
            )
            out = svc.run(eval_mock=True)
            self.assertTrue(out["ok"])
            saved = svc.load_last_run()
            self.assertFalse(saved.get("empty"))
            self.assertTrue(saved.get("ok"))
            self.assertEqual(saved["steps"][0]["name"], "eval_mock")


class TestQuantReportExports(unittest.TestCase):
    def test_save_report_exports(self):
        report = {
            "success": True,
            "factor_ic": {"factors": [{"label": "动量", "ic": 0.05, "sample_count": 10}]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch("quant.services.quant_service.QUANT_REPORTS_DIR", tmp):
                out = QuantService().save_report_exports(report)
            self.assertTrue(out.get("success"))
            paths = out.get("paths") or {}
            self.assertIn("markdown", paths)
            self.assertIn("html", paths)
            self.assertTrue(os.path.isfile(paths["markdown"]))
            with open(paths["markdown"], encoding="utf-8") as f:
                body = f.read()
            self.assertIn("量化研究日报", body)


class TestDailyRunCliPreset(unittest.TestCase):
    def test_cli_preset_requires_valid(self):
        from research import daily_run

        with self.assertRaises(SystemExit):
            daily_run.main(["--preset", "nope"])

    def test_cli_eval_mock_still_works(self):
        from research import daily_run

        code = daily_run.main(["--eval-mock"])
        self.assertEqual(code, 0)


class TestDailyWebPresets(unittest.TestCase):
    def test_api_presets(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        client = TestClient(web_app.app)
        res = client.get("/api/daily/presets")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertGreaterEqual(len(data.get("presets") or []), 3)

    def test_api_daily_last_empty(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        with patch.object(deps.daily, "load_last_run", return_value={"success": True, "empty": True}):
            client = TestClient(web_app.app)
            res = client.get("/api/daily/last")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("empty"))

    def test_api_run_with_preset(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock_out = {"ok": True, "preset": "quant", "steps": [{"name": "quant_report", "ok": True}], "failures": []}
        with patch.object(deps.daily, "run", return_value=mock_out) as run_mock:
            client = TestClient(web_app.app)
            res = client.post("/api/daily/run", json={"preset": "quant"})
        self.assertEqual(res.status_code, 200)
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs.get("preset"), "quant")


if __name__ == "__main__":
    unittest.main()
