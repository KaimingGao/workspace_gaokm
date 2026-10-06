"""Golden cases 与 routing 对齐（离线）。"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import prepare_tool_params, wants_position_stance
from evals.run_checklist import check_routing_expect, load_cases


class TestGoldenRouting(unittest.TestCase):
    def test_position_add_stance_routing(self):
        cases = load_cases()
        case = next(c for c in cases if c["id"] == "position_add_stance")
        self.assertEqual(check_routing_expect(case), [])
        self.assertTrue(wants_position_stance(case["question"]))
        params = prepare_tool_params("position", {}, case["question"])
        self.assertTrue(params.get("include_stance"))

    def test_all_routing_expect_cases(self):
        for case in load_cases():
            if not case.get("routing_expect"):
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(check_routing_expect(case), [])


if __name__ == "__main__":
    unittest.main()
