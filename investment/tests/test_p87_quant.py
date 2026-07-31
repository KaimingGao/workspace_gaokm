import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases, run_skills, filter_quant_cases


class TestP87GoldenModelPolicyAndOls(unittest.TestCase):
    def test_golden_case_counts(self):
        cases = load_cases()
        self.assertEqual(len(cases), 22)
        quant_cases = filter_quant_cases(cases)
        self.assertEqual(len(quant_cases), 11)

    def test_quant_model_policy_case(self):
        case = next(c for c in load_cases() if c["id"] == "quant_model_policy")
        must = case.get("agent_must_contain") or []
        self.assertIn("规则", must)
        self.assertIn("stance", must)

    def test_quant_factor_ols_mock_skill(self):
        case = next(c for c in load_cases() if c["id"] == "quant_factor_ols")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "factor_ols")
        self.assertIsInstance(result.get("coefficients"), dict)


if __name__ == "__main__":
    unittest.main()
