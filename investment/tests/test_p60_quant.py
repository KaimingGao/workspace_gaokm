import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.services.quant_report_export import (
    build_neutral_compare_export_section,
    render_quant_report_markdown,
)


class TestP60GoldenDailyNeutralSection(unittest.TestCase):
    def test_golden_case_exists(self):
        ids = {c["id"] for c in load_cases()}
        self.assertIn("quant_daily_neutral_section", ids)
        self.assertEqual(len(load_cases()), 22)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_daily_summary_for_section_question(self):
        q = "量化日报中性化对照专节包含什么"
        self.assertEqual(infer_quant_task(q), "daily_summary")
        self.assertEqual(prepare_tool_params("quant", {}, q).get("task"), "daily_summary")

    def test_offline_mock_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "daily_summary")
        nc = result.get("portfolio_neutral_compare_summary") or {}
        self.assertTrue(nc.get("success"))
        self.assertIn(nc.get("winner"), ("neutralized", "absolute", "tie"))

    def test_export_section_from_skill_result(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        run = run_skills(case, use_mock=True)
        nc = run["skill_runs"][0]["result"].get("portfolio_neutral_compare_summary") or {}
        section = build_neutral_compare_export_section(nc)
        self.assertIsNotNone(section)
        md = render_quant_report_markdown({"portfolio_neutral_compare_summary": nc})
        self.assertIn("中性化对照专节", md)

    def test_eval_routing_map_includes_case(self):
        row = next(r for r in build_eval_routing_map()["cases"] if r["id"] == "quant_daily_neutral_section")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "daily_summary")


if __name__ == "__main__":
    unittest.main()
