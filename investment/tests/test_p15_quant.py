import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import QUANT_HINT
from agent.routing import build_user_hints, infer_quant_task, is_quant_question, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.services.quant_report_export import export_quant_report, render_quant_report_html


class TestQuantRouting(unittest.TestCase):
    def test_is_quant_question(self):
        self.assertTrue(is_quant_question("观察池组合 historically 表现如何"))
        self.assertFalse(is_quant_question("茅台现价"))

    def test_infer_quant_task(self):
        self.assertEqual(infer_quant_task("观察池组合 historically 表现如何"), "portfolio_backtest")
        self.assertEqual(infer_quant_task("量化报告摘要"), "daily_summary")

    def test_prepare_tool_params_quant(self):
        q = "观察池组合 historically 表现如何"
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "portfolio_backtest")

    def test_build_user_hints_quant(self):
        hinted = build_user_hints("量化报告怎么说")
        self.assertIn(QUANT_HINT, hinted)


class TestGoldenQuantCase(unittest.TestCase):
    def test_quant_portfolio_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_backtest")
        self.assertEqual(check_routing_expect(case), [])

    def test_quant_portfolio_offline_mock(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_backtest")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertIn("metrics", result)


class TestHtmlExport(unittest.TestCase):
    def test_render_html(self):
        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 2.0,
                "win_rate_pct": 55,
                "trade_count": 3,
                "loaded_stocks": ["600519"],
            }
        }
        html = render_quant_report_html(report)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Top-K 回测摘要", html)
        out = export_quant_report(report, fmt="html")
        self.assertTrue(out["success"])
        self.assertEqual(out["format"], "html")


if __name__ == "__main__":
    unittest.main()
