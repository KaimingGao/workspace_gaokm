import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP79WebCrossSectionScoreTable(unittest.TestCase):
    def test_app_js_renders_score_table(self):
        path = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("researchGridHtml", js)
        self.assertIn("score_raw", js)
        self.assertIn("quant-cross-list", js)

    def test_html_cross_section_wrap(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-cross-list", html)
        self.assertIn("quant-weight-table-wrap", html)
        self.assertIn("quant-cross-run", html)


if __name__ == "__main__":
    unittest.main()
