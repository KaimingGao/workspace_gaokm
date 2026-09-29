"""中性化对照：日报 preset / 导出 / 解读（合并原 P54·daily / P56 / P58–P61 / P63–P65）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch
from quant.research.portfolio_neutral_compare import summarize_portfolio_neutral_compare
from quant.services.quant_report_export import build_report_executive_summary
from tests.test_p10_quant import _aligned_bars
import tempfile
from evals.preset_check import check_daily_presets
from quant.ops.daily_presets import resolve_daily_preset
from services.daily_service import DailyRunService
from quant.services.quant_report_export import build_neutral_compare_export_section, render_quant_report_html, render_quant_report_markdown
from quant.ops.daily_presets import list_daily_presets
from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.services.quant_report_export import build_neutral_compare_export_section, render_quant_report_markdown
from unittest.mock import MagicMock
from quant.services.quant_interpret import QUANT_INTERPRET_SYSTEM, compact_quant_report, interpret_quant_report
from quant.services.quant_interpret import build_rule_based_interpret
from quant.services.quant_interpret import format_neutral_compare_brief, interpret_quant_report
from quant.services.quant_report_export import build_report_export_toc, render_quant_report_html, render_quant_report_markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- p54_daily.py::TestDailyNeutralCompareReport ---
class TestDailyNeutralCompareReport(unittest.TestCase):
    def test_build_daily_report_skips_neutral_summary_by_default(self):
        import quant.services.quant_service as qs_mod

        svc = qs_mod.QuantService()
        with patch.object(svc, "config_summary", return_value={"success": True}), patch.object(
            svc, "portfolio_daily_summary", return_value={"success": True, "total_return_pct": 4.0}
        ), patch.object(
            svc, "portfolio_neutral_compare_summary", return_value={"success": True}
        ) as nc_mock, patch.object(
            svc, "list_strategies", return_value={"success": True, "strategies": []}
        ), patch(
            "core.signal.y_state.ledger_y_check_daily_summary",
            return_value={"success": True},
        ):
            report = svc.build_daily_report(
                include_portfolio_backtest=True,
                include_cross_section=False,
            )

        self.assertNotIn("portfolio_neutral_compare_summary", report)
        nc_mock.assert_not_called()

    def test_build_daily_report_includes_neutral_summary_when_opted_in(self):
        import quant.services.quant_service as qs_mod

        mock_summary = {
            "success": True,
            "winner": "neutralized",
            "delta": {"total_return_pct": 1.5},
            "neutralized_total_return_pct": 5.0,
            "absolute_total_return_pct": 3.5,
            "interpretation": "中性化更优",
        }
        svc = qs_mod.QuantService()
        with patch.object(svc, "config_summary", return_value={"success": True}), patch.object(
            svc, "run_factor_report", return_value={"success": True, "factors": []}
        ), patch.object(
            svc, "run_factor_experiment", return_value={"success": True, "factors": []}
        ), patch.object(
            svc, "suggest_thresholds", return_value={"success": False}
        ), patch.object(
            svc, "portfolio_daily_summary", return_value={"success": True, "total_return_pct": 4.0}
        ), patch.object(
            svc, "portfolio_neutral_compare_summary", return_value=mock_summary
        ), patch.object(
            svc, "list_strategies", return_value={"success": True, "strategies": []}
        ), patch(
            "core.signal.y_state.ledger_y_check_daily_summary",
            return_value={"success": True},
        ):
            report = svc.build_daily_report(
                include_portfolio_backtest=True,
                include_portfolio_neutral_compare=True,
                include_cross_section=False,
            )

        self.assertIn("portfolio_neutral_compare_summary", report)
        self.assertTrue(report["portfolio_neutral_compare_summary"]["success"])
    def test_executive_summary_mentions_neutral_compare(self):
        summary = build_report_executive_summary(
            {
                "portfolio_neutral_compare_summary": {
                    "success": True,
                    "interpretation": "中性化更优",
                    "delta": {"total_return_pct": 1.2},
                }
            }
        )
        self.assertTrue(summary["success"])
        joined = " ".join(summary["bullets"])
        self.assertIn("中性化对照", joined)

# --- test_p56_quant.py::TestP56DailyPresetNeutralCompare ---
class TestP56DailyPresetNeutralCompare(unittest.TestCase):
    def test_quant_presets_disable_neutral_compare(self):
        for name in ("quant", "full", "quant_paper", "advisor"):
            flags = resolve_daily_preset(name)["flags"]
            self.assertFalse(
                flags["portfolio_neutral_compare"],
                msg=f"{name} should not enable portfolio_neutral_compare",
            )

    def test_preset_check_expectations(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"], msg="; ".join(out.get("failures") or []))

    def test_daily_service_passes_flag_to_build_daily_report(self):
        mock_report = {
            "factor_ic": {"sample_count": 3},
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "neutralized",
            },
        }
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
                out = svc.run(preset="quant")

            self.assertTrue(out["ok"])
            build_mock.assert_called_once()
            kwargs = build_mock.call_args.kwargs
            self.assertFalse(kwargs.get("include_portfolio_neutral_compare"))
            self.assertIsNone(kwargs.get("top_k"))
            self.assertIsNone(kwargs.get("horizon_days"))
            self.assertIsNone(kwargs.get("lookback"))
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertFalse(step.get("portfolio_neutral_compare"))

    def test_daily_service_passes_bt_overrides_to_build(self):
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
                out = svc.run(
                    preset="quant",
                    top_k=3,
                    horizon_days=1,
                    lookback=120,
                )

            self.assertTrue(out["ok"])
            kwargs = build_mock.call_args.kwargs
            self.assertEqual(kwargs.get("top_k"), 3)
            self.assertEqual(kwargs.get("horizon_days"), 1)
            self.assertEqual(kwargs.get("lookback"), 120)
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertEqual(step.get("top_k"), 3)
            self.assertEqual(step.get("horizon_days"), 1)
            self.assertEqual(step.get("lookback"), 120)

    def test_daily_service_can_opt_in_neutral_compare(self):
        mock_report = {
            "factor_ic": {"sample_count": 1},
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "neutralized",
            },
        }
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
                "core.watching.health.check_watching_health",
                return_value={"success": True, "warnings": [], "issues": []},
            ):
                out = svc.run(preset="quant", portfolio_neutral_compare=True)

            kwargs = build_mock.call_args.kwargs
            self.assertTrue(kwargs.get("include_portfolio_neutral_compare"))
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertTrue(step.get("portfolio_neutral_compare"))
            self.assertTrue(step.get("neutral_compare_ok"))
            self.assertEqual(step.get("neutral_compare_winner"), "neutralized")

# --- test_p58_quant.py::TestP58NeutralCompareExportSection ---
class TestP58NeutralCompareExportSection(unittest.TestCase):
    def _sample_nc(self):
        return {
            "success": True,
            "winner": "neutralized",
            "delta": {
                "total_return_pct": 1.5,
                "win_rate_pct": 3.0,
                "trade_count": 0,
            },
            "neutralized_total_return_pct": 5.0,
            "absolute_total_return_pct": 3.5,
            "neutralized_win_rate_pct": 58.0,
            "absolute_win_rate_pct": 55.0,
            "loaded_stocks": ["600519", "600036"],
            "interpretation": "中性化更优",
            "fundamentals_count": 2,
            "note": "快照基本面 + 截面中性化对照；非 point-in-time，仅供研究。",
        }

    def test_build_section(self):
        section = build_neutral_compare_export_section(self._sample_nc())
        self.assertIsNotNone(section)
        self.assertEqual(section["title"], "中性化对照专节")
        self.assertEqual(section["anchor"], "neutral-compare")
        joined = "\n".join(section["markdown_lines"])
        self.assertIn("Δ胜率", joined)
        self.assertIn("600519", joined)

    def test_markdown_includes_dedicated_section(self):
        md = render_quant_report_markdown(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("## 中性化对照专节", md)
        self.assertIn("Δ胜率", md)

    def test_html_includes_anchor_and_table(self):
        html = render_quant_report_html(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn('id="neutral-compare"', html)
        self.assertIn("中性化对照专节", html)
        self.assertIn("<table>", html)

    def test_empty_summary_skips_section(self):
        self.assertIsNone(build_neutral_compare_export_section({"success": False}))

# --- test_p59_quant.py::TestP59WebPresetFlags ---
class TestP59WebPresetFlags(unittest.TestCase):
    def test_list_presets_exposes_portfolio_neutral_compare(self):
        presets = list_daily_presets()
        by_name = {p["name"]: p for p in presets}
        self.assertIn("portfolio_neutral_compare", by_name["quant"]["flags"])
        self.assertFalse(by_name["quant"]["flags"]["portfolio_neutral_compare"])
        self.assertFalse(by_name["advisor"]["flags"]["portfolio_neutral_compare"])

    def test_daily_presets_api_includes_flag(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/daily/presets")
        self.assertEqual(res.status_code, 200)
        quant = next(p for p in res.json()["presets"] if p["name"] == "quant")
        self.assertFalse(quant["flags"]["portfolio_neutral_compare"])

    def test_index_html_has_preset_flags_container(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-ops-preset-flags", html)

    def test_app_js_renders_preset_flags(self):
        export_js = os.path.join(ROOT, "web", "static", "js", "quant", "domain_export.js")
        quant_js = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(export_js, encoding="utf-8") as f:
            self.assertIn("renderPresetFlags", f.read())
        with open(quant_js, encoding="utf-8") as f:
            self.assertIn("portfolio_neutral_compare", f.read())

# --- p60_exp.py::TestNeutralExportFromSkill ---
class TestNeutralExportFromSkill(unittest.TestCase):
    def test_export_section_from_skill_result(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        run = run_skills(case, use_mock=True)
        nc = run["skill_runs"][0]["result"].get("portfolio_neutral_compare_summary") or {}
        section = build_neutral_compare_export_section(nc)
        self.assertIsNotNone(section)
        md = render_quant_report_markdown({"portfolio_neutral_compare_summary": nc})
        self.assertIn("中性化对照专节", md)

# --- test_p61_quant.py::TestP61InterpretNeutralCompare ---
class TestP61InterpretNeutralCompare(unittest.TestCase):
    def _report_with_neutral(self):
        return {
            "success": True,
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "neutralized",
                "delta": {"total_return_pct": 1.2, "win_rate_pct": 2.0},
                "neutralized_total_return_pct": 4.5,
                "absolute_total_return_pct": 3.3,
                "neutralized_win_rate_pct": 58.0,
                "absolute_win_rate_pct": 56.0,
                "interpretation": "中性化更优",
                "note": "快照基本面 + 截面中性化对照",
            },
        }

    def test_system_prompt_mentions_neutral_section(self):
        self.assertIn("中性化", QUANT_INTERPRET_SYSTEM)
        self.assertIn("portfolio_neutral_compare_summary", QUANT_INTERPRET_SYSTEM)

    def test_compact_includes_win_rates(self):
        compact = compact_quant_report(self._report_with_neutral())
        nc = compact["portfolio_neutral_compare_summary"]
        self.assertEqual(nc["neutralized_win_rate_pct"], 58.0)
        self.assertEqual(nc["absolute_win_rate_pct"], 56.0)
        self.assertIn("note", nc)

    def test_interpret_payload_includes_neutral_summary(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {
            "choices": [{"message": {"content": "1. 中性化对照：neutralized 更优，Δ累计 1.2%"}}],
        }
        llm.get_response_content.return_value = "1. 中性化对照：neutralized 更优，Δ累计 1.2%"

        interpret_quant_report(self._report_with_neutral(), llm_client=llm)
        user_msg = llm.chat.call_args[0][0][1]["content"]
        self.assertIn("portfolio_neutral_compare_summary", user_msg)
        self.assertIn("neutralized_total_return_pct", user_msg)

# --- test_p63_quant.py::TestP63GoldenInterpretNeutral ---
class TestP63GoldenInterpretNeutral(unittest.TestCase):
    def test_golden_case_exists(self):
        self.assertEqual(len(load_cases()), 21)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_interpret_before_neutral_compare(self):
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
        self.assertIn("历史回测", body)
        self.assertNotIn("中性化对照", body)
        self.assertFalse((result.get("neutral_compare_summary") or {}).get("success"))

    def test_rule_based_interpret_mentions_neutral(self):
        out = build_rule_based_interpret(
            {
                "success": True,
                "portfolio_neutral_compare_summary": {
                    "success": True,
                    "winner": "neutralized",
                    "delta": {"total_return_pct": 1.0, "win_rate_pct": 2.0},
                    "neutralized_total_return_pct": 4.0,
                    "absolute_total_return_pct": 3.0,
                },
            }
        )
        self.assertTrue(out.get("success"))
        self.assertIn("中性化对照", out["interpretation"])

    def test_eval_routing_map_includes_case(self):
        row = next(r for r in build_eval_routing_map()["cases"] if r["id"] == "quant_interpret_neutral")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "interpret")

# --- test_p64_quant.py::TestP64InterpretNeutralCompareWebFields ---
class TestP64InterpretNeutralCompareWebFields(unittest.TestCase):
    def test_format_neutral_compare_brief(self):
        brief = format_neutral_compare_brief(
            {
                "success": True,
                "winner": "neutralized",
                "delta": {"total_return_pct": 1.2},
                "neutralized_total_return_pct": 5.0,
                "absolute_total_return_pct": 3.8,
            }
        )
        self.assertIn("neutralized", brief)
        self.assertIn("Δ累计", brief)

    def test_llm_interpret_attaches_neutral_fields(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {"choices": [{"message": {"content": "解读"}}]}
        llm.get_response_content.return_value = "解读"

        report = {
            "success": True,
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "absolute",
                "delta": {"total_return_pct": -0.5},
                "neutralized_total_return_pct": 2.0,
                "absolute_total_return_pct": 2.5,
            },
        }
        out = interpret_quant_report(report, llm_client=llm)
        self.assertTrue(out.get("success"))
        self.assertIn("neutral_compare_summary", out)
        self.assertIn("neutral_compare_brief", out)

# --- test_p65_quant.py::TestP65ExportTocNeutralAnchor ---
class TestP65ExportTocNeutralAnchor(unittest.TestCase):
    def _sample_nc(self):
        return {
            "success": True,
            "winner": "neutralized",
            "delta": {"total_return_pct": 1.0, "win_rate_pct": 0.0, "trade_count": 0},
            "neutralized_total_return_pct": 4.0,
            "absolute_total_return_pct": 3.0,
            "neutralized_win_rate_pct": 55.0,
            "absolute_win_rate_pct": 50.0,
            "loaded_stocks": ["600519"],
            "interpretation": "中性化更优",
        }

    def test_build_toc_includes_neutral_compare(self):
        toc = build_report_export_toc(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        anchors = [e["anchor"] for e in toc.get("entries") or []]
        self.assertIn("neutral-compare", anchors)
        joined = "\n".join(toc.get("markdown_lines") or [])
        self.assertIn("neutral-compare", joined)

    def test_markdown_has_toc_section(self):
        md = render_quant_report_markdown(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("## 目录", md)
        self.assertIn("neutral-compare", md)

    def test_html_has_toc_nav_and_anchor(self):
        html = render_quant_report_html(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("report-toc", html)
        self.assertIn('href="#neutral-compare"', html)
        self.assertIn('id="neutral-compare"', html)


if __name__ == "__main__":
    unittest.main()
