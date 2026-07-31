import os
import sys
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_interpret import (
    QUANT_INTERPRET_SYSTEM,
    compact_quant_report,
    interpret_quant_report,
)


class TestP61InterpretNeutralCompare(unittest.TestCase):
    def _report_with_neutral(self):
        return {
            "success": True,
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "neutralized",
                "delta": {"total_return_pct": 1.2, "win_rate_pct": 2.0},
                "neutralized_total_return_pct": 4.5,
                "absolute_total_return_pct": 3.3,
                "neutralized_win_rate_pct": 58.0,
                "absolute_win_rate_pct": 56.0,
                "interpretation": "中性化更优",
                "note": "快照基本面 + 截面中性化对照",
            },
        }

    def test_system_prompt_mentions_neutral_section(self):
        self.assertIn("中性化", QUANT_INTERPRET_SYSTEM)
        self.assertIn("portfolio_neutral_compare_summary", QUANT_INTERPRET_SYSTEM)

    def test_compact_includes_win_rates(self):
        compact = compact_quant_report(self._report_with_neutral())
        nc = compact["portfolio_neutral_compare_summary"]
        self.assertEqual(nc["neutralized_win_rate_pct"], 58.0)
        self.assertEqual(nc["absolute_win_rate_pct"], 56.0)
        self.assertIn("note", nc)

    def test_interpret_payload_includes_neutral_summary(self):
        llm = MagicMock()
        llm.api_key = "test"
        llm.is_available.return_value = True
        llm.chat.return_value = {
            "choices": [{"message": {"content": "1. 中性化对照：neutralized 更优，Δ累计 1.2%"}}],
        }
        llm.get_response_content.return_value = "1. 中性化对照：neutralized 更优，Δ累计 1.2%"

        interpret_quant_report(self._report_with_neutral(), llm_client=llm)
        user_msg = llm.chat.call_args[0][0][1]["content"]
        self.assertIn("portfolio_neutral_compare_summary", user_msg)
        self.assertIn("neutralized_total_return_pct", user_msg)


if __name__ == "__main__":
    unittest.main()
