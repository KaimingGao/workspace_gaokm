import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import export_quant_report


class TestP67ExportPreviewToc(unittest.TestCase):
    def _sample_report(self):
        return {
            "portfolio_neutral_compare_summary": {
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
        }

    def test_export_includes_toc(self):
        out = export_quant_report(self._sample_report(), fmt="markdown")
        self.assertTrue(out.get("success"))
        toc = out.get("export_toc") or {}
        anchors = [e["anchor"] for e in toc.get("entries") or []]
        self.assertIn("neutral-compare", anchors)

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
                "entries": [{"title": "中性化对照专节", "anchor": "neutral-compare"}],
            },
        }
        with unittest.mock.patch.object(deps.quant, "export_report", return_value=mock):
            client = TestClient(web_app.app)
            res = client.get("/api/quant/export?format=html")
        self.assertEqual(res.status_code, 200)
        self.assertIn("neutral-compare", [e["anchor"] for e in res.json()["export_toc"]["entries"]])

    def test_web_has_export_preview_ui(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-export-preview-toc", html)
        self.assertIn("quant-export-preview-html", html)
        self.assertIn("quant-export-md", html)

    def test_app_js_preview_helpers(self):
        path = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("previewQuantExport", js)
        self.assertIn("renderExportPreviewToc", js)


if __name__ == "__main__":
    unittest.main()
