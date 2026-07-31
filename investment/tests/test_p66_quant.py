import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import load_cases


class TestP66InterpretNeutralAgentGolden(unittest.TestCase):
    def test_interpret_neutral_has_agent_must_contain(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        must = case.get("agent_must_contain") or []
        self.assertIn("中性化", must)
        self.assertIn("对照", must)
        self.assertIn("不保证收益", must)

    def test_agent_regression_covers_interpret_neutral_phrases(self):
        case = next(c for c in load_cases() if c["id"] == "quant_interpret_neutral")
        run = __import__("evals.run_checklist", fromlist=["run_skills"]).run_skills(
            case, use_mock=True
        )
        body = run["skill_runs"][0]["result"].get("interpretation") or ""
        for phrase in ("中性化", "对照"):
            self.assertIn(phrase, body)

    def test_quant_only_includes_interpret_neutral(self):
        from evals.run_checklist import filter_quant_cases

        ids = {c["id"] for c in filter_quant_cases(load_cases())}
        self.assertIn("quant_interpret_neutral", ids)


if __name__ == "__main__":
    unittest.main()
