"""Agent / golden 计数与提示（原 P55）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import QUANT_HINT, SYSTEM_PROMPT
from evals.run_checklist import load_cases
from services.eval_service import EvalService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p55_quant.py::TestP55AgentGoldenRegression ---
class TestP55AgentGoldenRegression(unittest.TestCase):
    def test_golden_case_count(self):
        cases = load_cases()
        self.assertEqual(len(cases), 21)
        quant_ids = [c["id"] for c in cases if str(c["id"]).startswith("quant_")]
        self.assertEqual(len(quant_ids), 10)

    def test_neutral_compare_case_removed(self):
        ids = {c["id"] for c in load_cases()}
        self.assertNotIn("quant_portfolio_neutral_compare", ids)

    def test_eval_summary_case_count(self):
        summary = EvalService().summary()
        self.assertEqual(summary["case_count"], 21)
        self.assertEqual(len(summary["quant_case_ids"]), 10)
        self.assertIn("quant_interpret_neutral", summary["quant_case_ids"])
        self.assertIn("quant_cross_section_score", summary["quant_case_ids"])
        self.assertIn("quant_factor_ols", summary["quant_case_ids"])
        self.assertIn("quant_model_policy", summary["quant_case_ids"])
        self.assertNotIn("quant_portfolio_neutral_compare", summary["quant_case_ids"])

    def test_prompts_do_not_mention_portfolio_neutral_compare(self):
        self.assertNotIn("portfolio_neutral_compare", SYSTEM_PROMPT)
        self.assertNotIn("portfolio_neutral_compare", QUANT_HINT)

    def test_agent_regression_scripts_document_21_and_10(self):
        scripts_dir = os.path.join(ROOT, "scripts")
        with open(os.path.join(scripts_dir, "agent_regression.sh"), encoding="utf-8") as f:
            self.assertIn("21 cases", f.read())
        with open(os.path.join(scripts_dir, "agent_regression_quant.sh"), encoding="utf-8") as f:
            text = f.read()
            self.assertIn("10 quant_* cases", text)


if __name__ == "__main__":
    unittest.main()
