"""日报 preset / health / 信号配置 / CI preset（合并原 P16–P22 / P69）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import json
import tempfile
from unittest.mock import patch
from quant.ops.daily_presets import list_daily_presets, resolve_daily_preset
from services.daily_service import DailyRunService
from services.eval_service import EvalService
from quant.services.quant_service import QuantService
from unittest.mock import MagicMock, patch
from core.watching_health import check_watching_health
from quant.ops.daily_health import build_daily_health
from quant.services.quant_report_index import list_quant_reports, read_quant_report_file
from quant.skill.engine import QuantEngine
from evals.run_checklist import load_cases, run_skills
from research.daily_check import check_daily_last_run, main as daily_check_main
from core.signal.config import read_signal_config_file
from quant.services.signal_config_preview import build_config_diff_preview
from evals.preset_check import check_daily_presets
from quant.services.signal_config_preview import build_config_diff_preview, export_config_diff_bundle
from evals.run_preset_check import main as run_preset_check_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p16_quant.py::TestDailyPresets ---
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

# --- test_p16_quant.py::TestDailyLastRun ---
class TestDailyLastRun(unittest.TestCase):
    def test_eval_mock_writes_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            last_path = os.path.join(tmp, "daily_last_run.json")
            svc = DailyRunService(
                evals=EvalService(last_run_path=os.path.join(tmp, "evals.json")),
                last_run_path=last_path,
            )
            out = svc.run(eval_mock=True)
            # mock 日线不足时部分 golden 可能 fail；仍须落盘 last_run
            self.assertIn("ok", out)
            saved = svc.load_last_run()
            self.assertFalse(saved.get("empty"))
            self.assertEqual(saved.get("ok"), out["ok"])
            self.assertEqual(saved["steps"][0]["name"], "eval_mock")

# --- test_p16_quant.py::TestDailyRunCliPreset ---
class TestDailyRunCliPreset(unittest.TestCase):
    def test_cli_preset_requires_valid(self):
        from research import daily_run

        with self.assertRaises(SystemExit):
            daily_run.main(["--preset", "nope"])

# --- test_p16_quant.py::TestDailyWebPresets ---
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

# --- test_p17_quant.py::TestWatchingHealth ---
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

# --- test_p17_quant.py::TestDailyHealth ---
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

# --- test_p17_quant.py::TestQuantHealthTask ---
class TestQuantHealthTask(unittest.TestCase):
    def test_health_task(self):
        engine = QuantEngine()
        mock_svc = MagicMock()
        mock_svc.build_health_summary.return_value = {"success": True, "ok": True, "task": "health"}
        engine._svc = mock_svc
        out = engine.run({"task": "health"})
        self.assertTrue(out["ok"])

# --- test_p17_quant.py::TestQuantHealthApi ---
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
        import web.deps as deps

        with patch.object(
            deps.quant,
            "list_report_archive",
            return_value={"success": True, "count": 0, "reports": []},
        ):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/reports")
        self.assertEqual(res.status_code, 200)

# --- test_p18_quant.py::TestWatchingFileApi ---
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

# --- test_p18_quant.py::TestDailyCheck ---
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

# --- test_p19_quant.py::TestSignalConfigRead ---
class TestSignalConfigRead(unittest.TestCase):
    def test_read_signal_config_file(self):
        out = read_signal_config_file(reload=True)
        self.assertTrue(out["success"])
        self.assertIn("weights", out["config"])
        self.assertTrue(out["readonly"])
    def test_quant_service_read(self):
        out = QuantService().read_signal_config_file()
        self.assertTrue(out["success"])
        self.assertIn("stance_thresholds", out["config"])

# --- test_p19_quant.py::TestQuantPaperPreset ---
class TestQuantPaperPreset(unittest.TestCase):
    def test_quant_paper_preset(self):
        names = {p["name"] for p in list_daily_presets()}
        self.assertIn("quant_paper", names)
        out = resolve_daily_preset("quant_paper")
        flags = out["flags"]
        self.assertTrue(flags["quant_report"])
        self.assertTrue(flags["paper_rebalance"])
        self.assertTrue(flags["export_quant_report"])

# --- test_p19_quant.py::TestSignalConfigApi ---
class TestSignalConfigApi(unittest.TestCase):
    def test_api_signal_config(self):
        from fastapi.testclient import TestClient

        import web.app as web_app

        client = TestClient(web_app.app)
        res = client.get("/api/signal/config/file")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("readonly"))
        self.assertIn("config", data)

# --- test_p19_quant.py::TestQuantPaperCli ---
class TestQuantPaperCli(unittest.TestCase):
    def test_cli_preset_quant_paper_resolves(self):
        from quant.ops.daily_presets import resolve_daily_preset

        out = resolve_daily_preset("quant_paper")
        self.assertEqual(out["preset"], "quant_paper")
        self.assertTrue(out["flags"]["paper_rebalance"])

# --- test_p20_quant.py::TestConfigDiffPreview ---
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

# --- test_p21_quant.py::TestDiffBundleExport ---
class TestDiffBundleExport(unittest.TestCase):
    def test_export_bundle_from_preview(self):
        preview = build_config_diff_preview(
            weight_suggest={
                "success": True,
                "config_diff": {
                    "success": True,
                    "changes": {"momentum": {"from": 0.4, "to": 0.43, "delta": 0.03}},
                    "patch": {"weights": {"momentum": 0.43}},
                },
            }
        )
        out = export_config_diff_bundle(preview)
        self.assertTrue(out["success"])
        self.assertIn("weights", out["merged_patch"])
    def test_api_diff_export(self):
        from fastapi.testclient import TestClient

        import web.app as web_app
        import web.deps as deps

        mock = {
            "success": True,
            "filename": "signal_config_diff_bundle.json",
            "merged_patch": {"weights": {"momentum": 0.43}},
        }
        with patch.object(deps.quant, "export_config_diff_bundle", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/signal/config/diff-export")
        self.assertEqual(res.status_code, 200)
        self.assertIn("merged_patch", res.json())

# --- test_p21_quant.py::TestPresetCheck ---
class TestPresetCheck(unittest.TestCase):
    def test_quant_paper_preset_flags(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"], msg="; ".join(out.get("failures") or []))
        self.assertIn("quant_paper", out.get("checked") or [])

# --- test_p21_quant.py::TestSignalDiffExportCli ---
class TestSignalDiffExportCli(unittest.TestCase):
    def test_cli_mocked(self):
        from research import signal_diff_export_run

        mock = {
            "success": True,
            "merged_patch": {"weights": {"momentum": 0.43}},
        }
        with patch("research.signal_diff_export_run.QuantService") as mock_cls:
            mock_cls.return_value.export_config_diff_bundle.return_value = mock
            code = signal_diff_export_run.main(["--fresh"])
        self.assertEqual(code, 0)

# --- test_p22_quant.py::TestQuantPaperDailyIntegration ---
class TestQuantPaperDailyIntegration(unittest.TestCase):
    @patch("core.watching_health.check_watching_health")
    def test_quant_paper_preset_runs_rebalance(self, mock_health):
        mock_health.return_value = {
            "success": True,
            "issues": [],
            "warnings": [],
            "watchlist_count": 3,
        }
        paper = MagicMock()
        paper.exists.return_value = True
        paper.rebalance.return_value = {
            "success": True,
            "sell_trades": [],
            "buy_trades": [{"stock_code": "600519"}],
            "summary": {"equity": 100000},
        }

        with patch("quant.services.quant_service.QuantService") as qcls:
            inst = qcls.return_value
            inst.refresh_watching.return_value = {
                "success": True,
                "refresh": {"count": 3},
            }
            inst.run_cross_section.return_value = {
                "success": True,
                "ranked_count": 3,
            }
            inst.build_daily_report.return_value = {
                "success": True,
                "factor_ic": {"sample_count": 10},
            }
            inst.save_daily_report.return_value = os.path.join(ROOT, "data", "quant_daily.json")
            inst.save_report_exports.return_value = {
                "success": True,
                "paths": {"markdown": "/tmp/x.md"},
            }

            out = DailyRunService(paper=paper).run(preset="quant_paper")

        names = [s.get("name") for s in out.get("steps") or []]
        self.assertIn("paper_rebalance", names)
        reb = next(s for s in out["steps"] if s["name"] == "paper_rebalance")
        self.assertTrue(reb.get("ok"))
        paper.rebalance.assert_called_once()

# --- test_p22_quant.py::TestCiPresetCheck ---
class TestCiPresetCheck(unittest.TestCase):
    def test_preset_check_cli(self):
        code = run_preset_check_main([])
        self.assertEqual(code, 0)
    def test_quant_paper_in_expectations(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"])
        self.assertIn("quant_paper", out["checked"])

# --- test_p69_quant.py::TestP69DailyAutoExportPreview ---
class TestP69DailyAutoExportPreview(unittest.TestCase):
    def test_quant_presets_include_export_flag(self):
        from quant.ops.daily_presets import DAILY_PRESETS

        for name in ("quant", "quant_paper", "full"):
            self.assertTrue(DAILY_PRESETS[name].get("export_quant_report"))
        self.assertFalse(DAILY_PRESETS["advisor"].get("export_quant_report"))


if __name__ == "__main__":
    unittest.main()
