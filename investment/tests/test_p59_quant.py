import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.ops.daily_presets import list_daily_presets


class TestP59WebPresetFlags(unittest.TestCase):
    def test_list_presets_exposes_portfolio_neutral_compare(self):
        presets = list_daily_presets()
        by_name = {p["name"]: p for p in presets}
        self.assertIn("portfolio_neutral_compare", by_name["quant"]["flags"])
        self.assertTrue(by_name["quant"]["flags"]["portfolio_neutral_compare"])
        self.assertFalse(by_name["advisor"]["flags"]["portfolio_neutral_compare"])

    def test_daily_presets_api_includes_flag(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/daily/presets")
        self.assertEqual(res.status_code, 200)
        quant = next(p for p in res.json()["presets"] if p["name"] == "quant")
        self.assertTrue(quant["flags"]["portfolio_neutral_compare"])

    def test_index_html_has_preset_flags_container(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-ops-preset-flags", html)

    def test_app_js_renders_preset_flags(self):
        path = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("renderPresetFlags", js)
        self.assertIn("portfolio_neutral_compare", js)


if __name__ == "__main__":
    unittest.main()
