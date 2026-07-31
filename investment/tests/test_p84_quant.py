import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP84ExportPreviewCrossSectionScroll(unittest.TestCase):
    def test_app_js_cross_section_toc_scroll(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn('"cross-section": "横截面 score"', js)
        self.assertIn("markers", js)

    def test_export_toc_includes_cross_section_anchor(self):
        from quant.services.quant_report_export import build_report_export_toc

        toc = build_report_export_toc(
            {
                "cross_section": {
                    "success": True,
                    "ranking": [{"stock_code": "600519", "score": 70}],
                }
            }
        )
        anchors = [e["anchor"] for e in toc["entries"]]
        self.assertIn("cross-section", anchors)


if __name__ == "__main__":
    unittest.main()
