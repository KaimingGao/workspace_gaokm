import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP71WebInterpretOfflineFallback(unittest.TestCase):
    def test_app_js_offline_fallback(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("llmAvailable", js)
        self.assertIn("offline: useOffline", js)
        self.assertIn('data.source === "rule_based"', js)

    def test_interpret_request_model_has_offline(self):
        try:
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")
        fields = web_app.QuantInterpretRequest.model_fields
        self.assertIn("offline", fields)


if __name__ == "__main__":
    unittest.main()
