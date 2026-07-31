import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP92DocsSync(unittest.TestCase):
    def test_quant_upgrade_documents_p89_p92(self):
        path = os.path.join(ROOT, "docs", "quant-upgrade.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for label in ("P89", "P90", "P91", "P92"):
            self.assertIn(label, text)
        self.assertIn("factor-ols", text)

    def test_quant_summary_lists_p89_p92(self):
        path = os.path.join(ROOT, "docs", "quant-summary.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("P89", text)
        self.assertIn("P92", text)

    def test_app_js_export_toc_factor_ols(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"factor-ols"', text)


if __name__ == "__main__":
    unittest.main()
