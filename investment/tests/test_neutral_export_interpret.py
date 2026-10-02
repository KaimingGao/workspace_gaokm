"""日报不再嵌入中性化对照；解读 golden 仍走 interpret，正文不含对照专节。"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.preset_check import check_daily_presets
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.daily_presets import list_daily_presets
from quant.ops.eval_routing_map import build_eval_routing_map
from services.daily_service import DailyRunService


class TestDailyReportOmitsNeutralCompare(unittest.TestCase):
    def test_build_daily_report_omits_neutral_summary(self):
        import quant.services.quant_service as qs_mod

        svc = qs_mod.QuantService()
        with patch.object(svc, "config_summary", return_value={"success": True}), patch.object(
            svc, "portfolio_daily_summary", return_value={"success": True, "total_return_pct": 4.0}
        ), patch.object(
            svc, "list_strategies", return_value={"success": True, "strategies": []}
        ):
            report = svc.build_daily_report(
                include_portfolio_backtest=True,
                include_cross_section=False,
            )

        self.assertNotIn("portfolio_neutral_compare_summary", report)
        self.assertFalse(hasattr(svc, "portfolio_neutral_compare_summary"))

    def test_preset_check_expectations(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"], msg="; ".join(out.get("failures") or []))

    def test_presets_do_not_expose_neutral_flag(self):
        for preset in list_daily_presets():
            self.assertNotIn("portfolio_neutral_compare", preset["flags"])

    def test_daily_service_passes_lookback(self):
        mock_report = {"factor_ic": {"sample_count": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            svc = DailyRunService(last_run_path=os.path.join(tmp, "daily.json"))
            with patch(
                "quant.services.quant_service.QuantService.build_daily_report",
                return_value=mock_report,
            ) as build_mock, patch.object(
                svc,
                "_save_last_run",
            ), patch(
                "quant.services.quant_service.QuantService.save_daily_report",
                return_value="/tmp/quant_daily.json",
            ), patch(
                "quant.services.quant_service.QuantService.save_report_exports",
                return_value={"success": True, "paths": {}},
            ), patch(
                "quant.services.quant_service.QuantService.refresh_watching",
                return_value={"refresh": {"count": 2}},
            ), patch(
                "quant.services.quant_service.QuantService.run_cross_section",
                return_value={"success": True, "ranked_count": 2},
            ), patch(
                "core.watching.health.check_watching_health",
                return_value={"success": True, "warnings": [], "issues": []},
            ):
                out = svc.run(preset="quant", lookback=120)

            self.assertTrue(out["ok"])
            kwargs = build_mock.call_args.kwargs
            self.assertNotIn("include_portfolio_neutral_compare", kwargs)
            self.assertNotIn("top_k", kwargs)
            self.assertNotIn("horizon_days", kwargs)
            self.assertEqual(kwargs.get("lookback"), 120)
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertNotIn("portfolio_neutral_compare", step)
            self.assertEqual(step.get("lookback"), 120)


class TestP59WebPresetFlags(unittest.TestCase):
    def test_daily_presets_api_omits_flag(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/daily/presets")
        self.assertEqual(res.status_code, 200)
        quant = next(p for p in res.json()["presets"] if p["name"] == "quant")
        self.assertNotIn("portfolio_neutral_compare", quant["flags"])

    def test_index_html_has_preset_flags_container(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("quant-ops-preset-flags", html)
        self.assertNotIn("quant-daily-fold", html)

    def test_app_js_renders_preset_flags(self):
        export_js = os.path.join(ROOT, "web", "static", "js", "quant", "domain_export.js")
        quant_js = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(export_js, encoding="utf-8") as f:
            src = f.read()
            self.assertIn("renderPresetFlags", src)
            self.assertIn("deprecated: true", src)
        with open(quant_js, encoding="utf-8") as f:
            qsrc = f.read()
            self.assertNotIn("PRESET_FLAG_LABELS", qsrc)
            self.assertNotIn("QUANT_EXPORT_PRESETS", qsrc)
            self.assertIn("openReadmeViewer", qsrc)


class TestP63GoldenInterpretNeutral(unittest.TestCase):
    def test_golden_case_count(self):
        self.assertEqual(len(load_cases()), 20)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_interpret(self):
        q = "解读量化日报里的中性化对照"
        self.assertEqual(infer_quant_task(q), "interpret")
        self.assertEqual(prepare_tool_params("quant", {}, q).get("task"), "interpret")

    def test_offline_interpret_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "interpret")
        self.assertEqual(result.get("source"), "rule_based")
        body = result.get("interpretation") or ""
        note = result.get("note") or ""
        self.assertIn("不保证收益", note or body)
        self.assertNotIn("中性化对照", body)
        self.assertNotIn("neutral_compare_summary", result)
        self.assertNotIn("neutral_compare_brief", result)

    def test_eval_routing_map_includes_case(self):
        row = next(r for r in build_eval_routing_map()["cases"] if r["id"] == "quant_interpret_neutral")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "interpret")


if __name__ == "__main__":
    unittest.main()
