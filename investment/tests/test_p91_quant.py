import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.mock_context import rising_bars
from quant.research.factor_ols import compute_factor_ols_report
from quant.services.quant_report_export import (
    build_factor_ols_export_section,
    build_report_export_toc,
    export_quant_report,
    render_quant_report_markdown,
)


class TestP91FactorOlsExport(unittest.TestCase):
    def _report(self):
        bars = rising_bars(45)
        ols = compute_factor_ols_report(bars, horizon_days=3, min_history=12)
        return {"success": True, "factor_ols": ols}

    def test_build_factor_ols_export_section(self):
        sec = build_factor_ols_export_section(self._report()["factor_ols"])
        self.assertIsNotNone(sec)
        self.assertEqual(sec["anchor"], "factor-ols")
        self.assertTrue(any("OLS" in line for line in sec["markdown_lines"]))

    def test_toc_includes_factor_ols(self):
        toc = build_report_export_toc(self._report())
        anchors = [e["anchor"] for e in toc["entries"]]
        self.assertIn("factor-ols", anchors)

    def test_markdown_and_html_export(self):
        report = self._report()
        md = render_quant_report_markdown(report)
        self.assertIn("因子 OLS 实验", md)
        html = export_quant_report(report, fmt="html")
        self.assertIn("factor-ols", html["content"])


if __name__ == "__main__":
    unittest.main()
