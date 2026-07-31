import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases, run_skills


class TestP78GoldenCrossSectionScore(unittest.TestCase):
    def test_case_exists_with_agent_must_contain(self):
        case = next(c for c in load_cases() if c["id"] == "quant_cross_section_score")
        must = case.get("agent_must_contain") or []
        self.assertIn("score", must)
        self.assertIn("横截面", must)

    def test_mock_cross_section_returns_scores(self):
        case = next(c for c in load_cases() if c["id"] == "quant_cross_section_score")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        ranking = result.get("ranking") or []
        self.assertGreaterEqual(len(ranking), 1)
        self.assertIsNotNone(ranking[0].get("score"))

    def test_quant_only_includes_case(self):
        from evals.run_checklist import filter_quant_cases

        ids = {c["id"] for c in filter_quant_cases(load_cases())}
        self.assertIn("quant_cross_section_score", ids)


if __name__ == "__main__":
    unittest.main()
