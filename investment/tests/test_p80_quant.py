import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import (
    build_cross_section_export_section,
    build_report_export_toc,
    export_quant_report,
    render_quant_report_markdown,
)


class TestP80CrossSectionExportSection(unittest.TestCase):
    def _cs(self):
        return {
            "success": True,
            "ranking": [
                {"stock_code": "600519", "stock_name": "贵州茅台", "score": 72.5, "score_raw": 68.0},
                {"stock_code": "600036", "stock_name": "招商银行", "score": 61.0, "score_raw": 59.0},
            ],
            "note": "横截面排序基于 score_bars",
        }

    def test_build_cross_section_export_section(self):
        sec = build_cross_section_export_section(self._cs())
        self.assertIsNotNone(sec)
        self.assertEqual(sec["anchor"], "cross-section")
        self.assertTrue(any("score 72.5" in line or "score 72" in line for line in sec["markdown_lines"]))

    def test_toc_includes_cross_section(self):
        toc = build_report_export_toc({"cross_section": self._cs()})
        anchors = [e["anchor"] for e in toc["entries"]]
        self.assertIn("cross-section", anchors)

    def test_markdown_export_includes_section(self):
        md = render_quant_report_markdown({"cross_section": self._cs()})
        self.assertIn("横截面 score", md)
        self.assertIn("贵州茅台", md)

    def test_html_export_includes_section(self):
        out = export_quant_report({"cross_section": self._cs()}, fmt="html")
        self.assertIn("cross-section", out["content"])


if __name__ == "__main__":
    unittest.main()
