"""Golden checklist 全量离线 mock 回归。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import check_routing_expect, load_cases, run_skills


class TestEvalsMock(unittest.TestCase):
    def test_all_cases_offline_mock(self):
        failures = []
        for case in load_cases():
            cid = case["id"]
            routing_failures = check_routing_expect(case)
            if routing_failures:
                failures.extend(routing_failures)
                continue
            if not case.get("mock"):
                failures.append(f"{cid}: missing mock config")
                continue
            run = run_skills(case, use_mock=True)
            for step in run["skill_runs"]:
                result = step["result"]
                if not result.get("success"):
                    failures.append(f"{cid}/{step['name']}: {result.get('error')}")
        self.assertEqual(failures, [])

    def test_index_relative_offline_mock(self):
        case = next(c for c in load_cases() if c["id"] == "index_relative")
        run = run_skills(case, use_mock=True)
        self.assertTrue(run["skill_runs"][0]["result"].get("success"))

    def test_backtest_offline_mock(self):
        case = next(c for c in load_cases() if c["id"] == "backtest_signal_moutai")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertIn("results", result)


if __name__ == "__main__":
    unittest.main()
