import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import (
    build_report_executive_summary,
    export_quant_report,
    render_quant_report_html,
    render_quant_report_markdown,
)


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
            },
            "weight_suggest": {"success": True},
        }

    def test_build_summary_bullets(self):
        out = build_report_executive_summary(self._sample_report())
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["bullet_count"], 3)
        self.assertTrue(any("因子 IC" in b for b in out["bullets"]))

    def test_markdown_includes_summary_section(self):
        md = render_quant_report_markdown(self._sample_report())
        self.assertIn("一页摘要", md)
        self.assertIn("## Top-K 回测摘要", md)

    def test_html_includes_summary_section(self):
        html = render_quant_report_html(self._sample_report())
        self.assertIn("一页摘要", html)

    def test_export_includes_executive_summary(self):
        out = export_quant_report(self._sample_report(), fmt="html")
        self.assertTrue(out["success"])
        self.assertIn("executive_summary", out)
        self.assertGreaterEqual(out["executive_summary"]["bullet_count"], 1)


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


if __name__ == "__main__":
    unittest.main()
