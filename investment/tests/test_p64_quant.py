import os
import sys
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_interpret import format_neutral_compare_brief, interpret_quant_report


class TestP64InterpretNeutralCompareWebFields(unittest.TestCase):
    def test_format_neutral_compare_brief(self):
        brief = format_neutral_compare_brief(
            {
                "success": True,
                "winner": "neutralized",
                "delta": {"total_return_pct": 1.2},
                "neutralized_total_return_pct": 5.0,
                "absolute_total_return_pct": 3.8,
            }
        )
        self.assertIn("neutralized", brief)
        self.assertIn("Δ累计", brief)

    def test_llm_interpret_attaches_neutral_fields(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {"choices": [{"message": {"content": "解读"}}]}
        llm.get_response_content.return_value = "解读"

        report = {
            "success": True,
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "absolute",
                "delta": {"total_return_pct": -0.5},
                "neutralized_total_return_pct": 2.0,
                "absolute_total_return_pct": 2.5,
            },
        }
        out = interpret_quant_report(report, llm_client=llm)
        self.assertTrue(out.get("success"))
        self.assertIn("neutral_compare_summary", out)
        self.assertIn("neutral_compare_brief", out)

    def test_web_has_interpret_neutral_container(self):
        path = os.path.join(ROOT, "web", "static", "index.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-interpret-neutral", html)

    def test_app_js_renders_interpret_neutral(self):
        path = os.path.join(ROOT, "web", "static", "app.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("quantInterpretNeutral", js)
        self.assertIn("neutral_compare_summary", js)


if __name__ == "__main__":
    unittest.main()
