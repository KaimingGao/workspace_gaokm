import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import (
    build_neutral_compare_export_section,
    render_quant_report_html,
    render_quant_report_markdown,
)


class TestP58NeutralCompareExportSection(unittest.TestCase):
    def _sample_nc(self):
        return {
            "success": True,
            "winner": "neutralized",
            "delta": {
                "total_return_pct": 1.5,
                "win_rate_pct": 3.0,
                "trade_count": 0,
            },
            "neutralized_total_return_pct": 5.0,
            "absolute_total_return_pct": 3.5,
            "neutralized_win_rate_pct": 58.0,
            "absolute_win_rate_pct": 55.0,
            "loaded_stocks": ["600519", "600036"],
            "interpretation": "中性化更优",
            "fundamentals_count": 2,
            "note": "快照基本面 + 截面中性化对照；非 point-in-time，仅供研究。",
        }

    def test_build_section(self):
        section = build_neutral_compare_export_section(self._sample_nc())
        self.assertIsNotNone(section)
        self.assertEqual(section["title"], "中性化对照专节")
        self.assertEqual(section["anchor"], "neutral-compare")
        joined = "\n".join(section["markdown_lines"])
        self.assertIn("Δ胜率", joined)
        self.assertIn("600519", joined)

    def test_markdown_includes_dedicated_section(self):
        md = render_quant_report_markdown(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("## 中性化对照专节", md)
        self.assertIn("Δ胜率", md)

    def test_html_includes_anchor_and_table(self):
        html = render_quant_report_html(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn('id="neutral-compare"', html)
        self.assertIn("中性化对照专节", html)
        self.assertIn("<table>", html)

    def test_empty_summary_skips_section(self):
        self.assertIsNone(build_neutral_compare_export_section({"success": False}))


if __name__ == "__main__":
    unittest.main()
