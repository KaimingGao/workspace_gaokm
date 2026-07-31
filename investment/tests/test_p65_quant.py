import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_report_export import (
    build_report_export_toc,
    render_quant_report_html,
    render_quant_report_markdown,
)


class TestP65ExportTocNeutralAnchor(unittest.TestCase):
    def _sample_nc(self):
        return {
            "success": True,
            "winner": "neutralized",
            "delta": {"total_return_pct": 1.0, "win_rate_pct": 0.0, "trade_count": 0},
            "neutralized_total_return_pct": 4.0,
            "absolute_total_return_pct": 3.0,
            "neutralized_win_rate_pct": 55.0,
            "absolute_win_rate_pct": 50.0,
            "loaded_stocks": ["600519"],
            "interpretation": "中性化更优",
        }

    def test_build_toc_includes_neutral_compare(self):
        toc = build_report_export_toc(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        anchors = [e["anchor"] for e in toc.get("entries") or []]
        self.assertIn("neutral-compare", anchors)
        joined = "\n".join(toc.get("markdown_lines") or [])
        self.assertIn("neutral-compare", joined)

    def test_markdown_has_toc_section(self):
        md = render_quant_report_markdown(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("## 目录", md)
        self.assertIn("neutral-compare", md)

    def test_html_has_toc_nav_and_anchor(self):
        html = render_quant_report_html(
            {"portfolio_neutral_compare_summary": self._sample_nc()}
        )
        self.assertIn("report-toc", html)
        self.assertIn('href="#neutral-compare"', html)
        self.assertIn('id="neutral-compare"', html)


if __name__ == "__main__":
    unittest.main()
