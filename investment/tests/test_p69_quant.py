import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP69DailyAutoExportPreview(unittest.TestCase):
    def test_app_js_auto_preview_after_quant_daily(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("QUANT_EXPORT_PRESETS", js)
        self.assertIn('previewQuantExport("markdown")', js)
        self.assertIn("QUANT_EXPORT_PRESETS.has(preset)", js)

    def test_quant_presets_include_export_flag(self):
        from quant.ops.daily_presets import DAILY_PRESETS

        for name in ("quant", "quant_paper", "full"):
            self.assertTrue(DAILY_PRESETS[name].get("export_quant_report"))
        self.assertFalse(DAILY_PRESETS["advisor"].get("export_quant_report"))


if __name__ == "__main__":
    unittest.main()
