import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP62WebNeutralCompareTable(unittest.TestCase):
    def test_index_has_table_container(self):
        path = os.path.join(ROOT, "web", "static", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-neutral-compare-table", html)

    def test_app_js_renders_table_helper(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function renderNeutralCompareTable", js)
        self.assertIn("renderNeutralCompareTable(nc", js)
        self.assertIn("中性化对照专节", js)


if __name__ == "__main__":
    unittest.main()
