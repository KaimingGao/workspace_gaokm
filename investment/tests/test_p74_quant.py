import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP74WebRuleInterpretButton(unittest.TestCase):
    def test_html_has_offline_interpret_button(self):
        path = os.path.join(ROOT, "web", "static", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-interpret-offline", html)
        self.assertIn("规则解读", html)

    def test_app_js_run_quant_interpret(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("runQuantInterpret", js)
        self.assertIn("forceOffline: true", js)
        self.assertIn("quant-interpret-offline", js)


if __name__ == "__main__":
    unittest.main()
