import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map


class TestP53GoldenNeutralCompare(unittest.TestCase):
    def test_golden_case_exists(self):
        ids = {c["id"] for c in load_cases()}
        self.assertIn("quant_portfolio_neutral_compare", ids)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_neutral_compare")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_and_prepare_routing(self):
        q = "观察池组合中性化和绝对分回测差多少"
        self.assertEqual(infer_quant_task(q), "portfolio_neutral_compare")
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "portfolio_neutral_compare")

    def test_offline_mock_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_neutral_compare")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "portfolio_neutral_compare")
        self.assertIn("delta", result)
        self.assertIn("winner", result)
        self.assertTrue(result.get("neutralized", {}).get("success"))
        self.assertTrue(result.get("absolute", {}).get("success"))

    def test_eval_routing_map_includes_case(self):
        out = build_eval_routing_map()
        row = next(r for r in out["cases"] if r["id"] == "quant_portfolio_neutral_compare")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "portfolio_neutral_compare")


if __name__ == "__main__":
    unittest.main()
