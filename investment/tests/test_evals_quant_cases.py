"""量化 golden / mock checklist 用例（合并原 P66/P78/P87）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import filter_quant_cases, load_cases, run_skills


class TestEvalsQuantCases(unittest.TestCase):
    def test_golden_case_counts(self):
        cases = load_cases()
        self.assertEqual(len(cases), 21)
        quant_cases = filter_quant_cases(cases)
        self.assertEqual(len(quant_cases), 10)

    def test_interpret_neutral_agent_phrases(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        must = case.get("agent_must_contain") or []
        self.assertIn("不保证收益", must)
        self.assertNotIn("中性化", must)
        run = run_skills(case, use_mock=True)
        body = run["skill_runs"][0]["result"].get("interpretation") or ""
        self.assertIn("不保证收益", run["skill_runs"][0]["result"].get("note") or body)
        self.assertNotIn("中性化对照", body)
        ids = {c["id"] for c in filter_quant_cases(load_cases())}
        self.assertIn("quant_interpret_neutral", ids)

    def test_cross_section_score_case(self):
        case = next(c for c in load_cases() if c["id"] == "quant_cross_section_score")
        must = case.get("agent_must_contain") or []
        self.assertIn("score", must)
        self.assertIn("横截面", must)
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertGreaterEqual(int(result.get("candidate_count") or 0), 1)
        note = result.get("note") or ""
        self.assertIn("横截面", note)
        # mock 日线可能被质量门拒绝；有 ranking 或 rejected_sample 即契约成立
        ranking = result.get("ranking") or []
        rejected = result.get("rejected_sample") or []
        self.assertTrue(ranking or rejected, result)
        if ranking:
            self.assertIsNotNone(ranking[0].get("score"))
        ids = {c["id"] for c in filter_quant_cases(load_cases())}
        self.assertIn("quant_cross_section_score", ids)

    def test_model_policy_and_factor_ols_cases(self):
        case = next(c for c in load_cases() if c["id"] == "quant_model_policy")
        must = case.get("agent_must_contain") or []
        self.assertIn("规则", must)
        self.assertIn("stance", must)

        ols_case = next(c for c in load_cases() if c["id"] == "quant_factor_ols")
        run = run_skills(ols_case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "factor_ols")
        self.assertIsInstance(result.get("coefficients"), dict)


if __name__ == "__main__":
    unittest.main()
