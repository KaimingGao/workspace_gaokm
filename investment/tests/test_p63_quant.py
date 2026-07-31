import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.services.quant_interpret import build_rule_based_interpret


class TestP63GoldenInterpretNeutral(unittest.TestCase):
    def test_golden_case_exists(self):
        self.assertEqual(len(load_cases()), 22)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_interpret_before_neutral_compare(self):
        q = "解读量化日报里的中性化对照"
        self.assertEqual(infer_quant_task(q), "interpret")
        self.assertEqual(prepare_tool_params("quant", {}, q).get("task"), "interpret")

    def test_offline_interpret_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "interpret")
        self.assertEqual(result.get("source"), "rule_based")
        self.assertIn("中性化", result.get("interpretation") or "")
        nc = result.get("neutral_compare_summary") or {}
        self.assertTrue(nc.get("success"))

    def test_rule_based_interpret_mentions_neutral(self):
        out = build_rule_based_interpret(
            {
                "success": True,
                "portfolio_neutral_compare_summary": {
                    "success": True,
                    "winner": "neutralized",
                    "delta": {"total_return_pct": 1.0, "win_rate_pct": 2.0},
                    "neutralized_total_return_pct": 4.0,
                    "absolute_total_return_pct": 3.0,
                },
            }
        )
        self.assertTrue(out.get("success"))
        self.assertIn("中性化对照", out["interpretation"])

    def test_eval_routing_map_includes_case(self):
        row = next(r for r in build_eval_routing_map()["cases"] if r["id"] == "quant_interpret_neutral")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "interpret")


if __name__ == "__main__":
    unittest.main()
