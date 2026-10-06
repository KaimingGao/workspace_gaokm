"""量化报告导出 / TOC / 摘要（合并原 P15·html / P16·exports / P17·index / P26·share / P27 / P67 / P75 / P80 / P91）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import QUANT_HINT
from agent.routing import build_user_hints, infer_quant_task, is_quant_question, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.services.quant_report_export import export_quant_report, render_quant_report_html
import json
import tempfile
from unittest.mock import patch
from quant.ops.daily_presets import list_daily_presets, resolve_daily_preset
from services.daily_service import DailyRunService
from services.eval_service import EvalService
from quant.services.quant_service import QuantService
from unittest.mock import MagicMock, patch
from core.watching.health import check_watching_health
from quant.ops.daily_health import build_daily_health
from quant.services.quant_report_index import list_quant_reports, read_quant_report_file
from quant.skill.engine import QuantEngine
from evals.run_checklist import load_cases
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.services.quant_report_index import list_quant_reports
from quant.services.quant_report_export import build_report_executive_summary, export_quant_report, render_quant_report_html, render_quant_report_markdown
from quant.services.quant_report_export import export_quant_report
from quant.services.quant_report_export import _cross_section_ranked_list, build_report_executive_summary
from quant.services.quant_report_export import build_cross_section_export_section, build_report_export_toc, export_quant_report, render_quant_report_markdown
from evals.mock_context import rising_bars
from quant.research.factor_ols import compute_factor_ols_report
from quant.services.quant_report_export import build_factor_ols_export_section, build_report_export_toc, export_quant_report, render_quant_report_markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p15_quant.py::TestHtmlExport ---

class TestHtmlExport(unittest.TestCase):
    def test_render_html(self):
        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 2.0,
                "win_rate_pct": 55,
                "trade_count": 3,
                "loaded_stocks": ["600519"],
            }
        }
        html = render_quant_report_html(report)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("历史回测摘要", html)
        out = export_quant_report(report, fmt="html")
        self.assertTrue(out["success"])
        self.assertEqual(out["format"], "html")

# --- test_p16_quant.py::TestQuantReportExports ---

class TestQuantReportExports(unittest.TestCase):
    def test_save_report_exports(self):
        report = {
            "success": True,
            "factor_ic": {"factors": [{"label": "动量", "ic": 0.05, "sample_count": 10}]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch("quant.services.quant_service_ops.QUANT_REPORTS_DIR", tmp):
                out = QuantService().save_report_exports(report)
            self.assertTrue(out.get("success"))
            paths = out.get("paths") or {}
            self.assertIn("markdown", paths)
            self.assertIn("html", paths)
            self.assertTrue(os.path.isfile(paths["markdown"]))
            with open(paths["markdown"], encoding="utf-8") as f:
                body = f.read()
            self.assertIn("量化研究日报", body)

# --- test_p17_quant.py::TestQuantReportIndex ---

class TestQuantReportIndex(unittest.TestCase):
    def test_list_and_read_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "quant_daily_20260719.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# test")
            listed = list_quant_reports(reports_dir=tmp, limit=10)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["day_count"], 1)
            self.assertEqual(listed["reports"][0]["format"], "markdown")
            self.assertEqual(listed["days"][0]["stamp"], "20260719")
            read = read_quant_report_file("quant_daily_20260719.md", reports_dir=tmp)
            self.assertTrue(read["success"])
            self.assertIn("# test", read["content"])

    def test_reject_invalid_filename(self):
        out = read_quant_report_file("../evil.txt")
        self.assertFalse(out["success"])

    def test_delete_report_day(self):
        from quant.services.quant_report_index import delete_quant_reports

        with tempfile.TemporaryDirectory() as tmp:
            for name in (
                "quant_daily_20260719.md",
                "quant_daily_20260719.html",
                "quant_daily_20260720.md",
            ):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write("x")
            # score_ledger 子目录不应被误删
            ledger_dir = os.path.join(tmp, "score_ledger")
            os.makedirs(ledger_dir)
            with open(os.path.join(ledger_dir, "2026-07-19.json"), "w", encoding="utf-8") as f:
                f.write("{}")

            out = delete_quant_reports(date="2026-07-19", reports_dir=tmp)
            self.assertTrue(out["success"])
            self.assertEqual(out["deleted_count"], 2)
            self.assertFalse(os.path.isfile(os.path.join(tmp, "quant_daily_20260719.md")))
            self.assertFalse(os.path.isfile(os.path.join(tmp, "quant_daily_20260719.html")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "quant_daily_20260720.md")))
            self.assertTrue(os.path.isfile(os.path.join(ledger_dir, "2026-07-19.json")))

            listed = list_quant_reports(reports_dir=tmp, limit=10)
            self.assertEqual(listed["day_count"], 1)
            self.assertEqual(listed["days"][0]["stamp"], "20260720")

    def test_delete_rejects_empty(self):
        from quant.services.quant_report_index import delete_quant_reports

        out = delete_quant_reports()
        self.assertFalse(out["success"])

# --- test_p26_quant.py::TestP26ReportShareUrl ---

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

# --- test_p26_quant.py::TestP26Api ---

class TestP26Api(unittest.TestCase):
    def test_quant_reports_share_url(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
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

    def test_quant_reports_delete_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "deleted": ["quant_daily_20260719.md", "quant_daily_20260719.html"],
            "deleted_count": 2,
        }
        with unittest.mock.patch.object(
            deps.quant, "delete_report_archive", return_value=mock
        ) as m:
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/reports/delete",
                json={"date": "2026-07-19"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["deleted_count"], 2)
        m.assert_called_once()
        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs.get("date"), "2026-07-19")

# --- test_p27_quant.py::TestP27ExecutiveSummary ---

class TestP27ExecutiveSummary(unittest.TestCase):
    def _sample_report(self):
        return {
            "factor_ic": {
                "sample_count": 40,
                "factors": [{"factor": "momentum", "label": "动量", "ic": 0.12, "sample_count": 40}],
            },
            "cross_section": {"success": True, "ranked": [{"stock_code": "600519"}, {"stock_code": "600036"}]},
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 3.2,
                "win_rate_pct": 58,
                "trade_count": 5,
                "cost_model": "simple_cn",
                "params": {
                    "top_k": 3,
                    "horizon_days": 3,
                    "yhat_horizon_days": 1,
                    "paper_horizon_days": 3,
                    "paper_max_positions": 20,
                    "min_predicted_score": 0.4,
                    "apply_costs": True,
                    "stock_count": 99,
                    "lookback": 120,
                },
            },
            "weight_suggest": {"success": True},
        }
    def test_build_summary_bullets(self):
        out = build_report_executive_summary(self._sample_report())
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["bullet_count"], 3)
        self.assertTrue(any("附录·因子 IC" in b or "因子 IC" in b for b in out["bullets"]))
        self.assertTrue(any("选股真源" in b for b in out["bullets"]))
        self.assertTrue(any(b.startswith("配置：") and "K=3" in b for b in out["bullets"]))
        cfg = next(b for b in out["bullets"] if b.startswith("配置："))
        self.assertIn("≠纸面 20", cfg)
        self.assertIn("持有 h=3日", cfg)
        self.assertIn("ŷ标签=1日", cfg)
        self.assertIn("ŷ≥0.4%", cfg)
    def test_markdown_includes_summary_section(self):
        md = render_quant_report_markdown(self._sample_report())
        self.assertIn("一页摘要", md)
        self.assertIn("## 历史回测摘要", md)
    def test_html_includes_summary_section(self):
        html = render_quant_report_html(self._sample_report())
        self.assertIn("一页摘要", html)
    def test_export_includes_executive_summary(self):
        out = export_quant_report(self._sample_report(), fmt="html")
        self.assertTrue(out["success"])
        self.assertIn("executive_summary", out)
        self.assertGreaterEqual(out["executive_summary"]["bullet_count"], 1)

# --- test_p27_quant.py::TestP27ExportSummaryApi ---

class TestP27ExportSummaryApi(unittest.TestCase):
    def test_api_quant_export_summary(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "bullet_count": 2,
            "bullets": ["a", "b"],
            "note": "test",
        }
        with unittest.mock.patch.object(
            deps.quant, "export_executive_summary", return_value=mock
        ):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/export/summary")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["bullet_count"], 2)

# --- test_p67_quant.py::TestP67ExportPreviewToc ---

class TestP67ExportPreviewToc(unittest.TestCase):
    def _sample_report(self):
        return {
            "cross_section": {
                "success": True,
                "ranking": [
                    {"stock_code": "600519", "stock_name": "贵州茅台", "score": 1.25},
                ],
            }
        }
    def test_export_includes_toc(self):
        out = export_quant_report(self._sample_report(), fmt="markdown")
        self.assertTrue(out.get("success"))
        toc = out.get("export_toc") or {}
        anchors = [e["anchor"] for e in toc.get("entries") or []]
        self.assertIn("cross-section", anchors)
        self.assertNotIn("neutral-compare", anchors)
    def test_export_api_returns_toc(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "format": "html",
            "filename": "quant_daily.html",
            "content": "<html></html>",
            "export_toc": {
                "success": True,
                "entries": [{"title": "横截面 ŷ", "anchor": "cross-section"}],
            },
        }
        with unittest.mock.patch.object(deps.quant, "export_report", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/export?format=html")
        self.assertEqual(res.status_code, 200)
        self.assertIn("cross-section", [e["anchor"] for e in res.json()["export_toc"]["entries"]])

# --- test_p75_quant.py::TestP75ExecutiveSummaryScoreStats ---

class TestP75ExecutiveSummaryScoreStats(unittest.TestCase):
    def test_cross_section_ranking_key(self):
        cs = {
            "success": True,
            "ranking": [
                {"stock_code": "600519", "score": 1.25},
                {"stock_code": "600036", "score": 0.61},
            ],
        }
        self.assertEqual(len(_cross_section_ranked_list(cs)), 2)
    def test_summary_includes_score_stats(self):
        report = {
            "cross_section": {
                "success": True,
                "ranking": [
                    {"stock_code": "600519", "score": 1.25},
                    {"stock_code": "600036", "score": 0.61},
                ],
            }
        }
        out = build_report_executive_summary(report)
        self.assertTrue(any("横截面 ŷ" in b for b in out["bullets"]))
        self.assertTrue(any("1.250%" in b or "1.25" in b for b in out["bullets"]))

# --- test_p80_quant.py::TestP80CrossSectionExportSection ---

class TestP80CrossSectionExportSection(unittest.TestCase):
    def _cs(self):
        return {
            "success": True,
            "ranking": [
                {"stock_code": "600519", "stock_name": "贵州茅台", "score": 1.25, "score_raw": 1.10},
                {"stock_code": "600036", "stock_name": "招商银行", "score": 0.61, "score_raw": 0.55},
            ],
            "note": "横截面排序基于 score_bars",
        }
    def test_build_cross_section_export_section(self):
        sec = build_cross_section_export_section(self._cs())
        self.assertIsNotNone(sec)
        self.assertEqual(sec["anchor"], "cross-section")
        self.assertTrue(
            any("ŷ 1.250%" in line or "ŷ 1.25" in line for line in sec["markdown_lines"])
        )
    def test_toc_includes_cross_section(self):
        toc = build_report_export_toc({"cross_section": self._cs()})
        anchors = [e["anchor"] for e in toc["entries"]]
        self.assertIn("cross-section", anchors)
    def test_markdown_export_includes_section(self):
        md = render_quant_report_markdown({"cross_section": self._cs()})
        self.assertIn("横截面 ŷ", md)
        self.assertIn("贵州茅台", md)
    def test_html_export_includes_section(self):
        out = export_quant_report({"cross_section": self._cs()}, fmt="html")
        self.assertIn("cross-section", out["content"])

# --- test_p91_quant.py::TestP91FactorOlsExport ---

class TestP91FactorOlsExport(unittest.TestCase):
    def _report(self):
        bars = rising_bars(100)
        ols = compute_factor_ols_report(bars, horizon_days=3, min_history=12)
        return {"success": True, "factor_ols": ols}
    def test_build_factor_ols_export_section(self):
        sec = build_factor_ols_export_section(self._report()["factor_ols"])
        self.assertIsNotNone(sec)
        self.assertEqual(sec["anchor"], "factor-ols")
        self.assertTrue(any("OLS" in line for line in sec["markdown_lines"]))
    def test_toc_includes_factor_ols(self):
        toc = build_report_export_toc(self._report())
        anchors = [e["anchor"] for e in toc["entries"]]
        self.assertIn("factor-ols", anchors)
    def test_markdown_and_html_export(self):
        report = self._report()
        md = render_quant_report_markdown(report)
        self.assertIn("因子 OLS 实验", md)
        html = export_quant_report(report, fmt="html")
        self.assertIn("factor-ols", html["content"])


if __name__ == "__main__":
    unittest.main()
