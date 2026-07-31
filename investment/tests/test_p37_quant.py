import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_agent_check import main as run_agent_check_main
from evals.run_checklist import filter_quant_cases, load_cases, main as checklist_main
from services.eval_service import EvalService


class TestP37QuantOnlyFilter(unittest.TestCase):
    def test_filter_quant_cases(self):
        quant = filter_quant_cases(load_cases())
        ids = {c["id"] for c in quant}
        self.assertEqual(
            ids,
            {
                "quant_portfolio_backtest",
                "quant_portfolio_neutral_compare",
                "quant_daily_neutral_section",
                "quant_interpret_neutral",
                "quant_cross_section_score",
                "quant_health",
                "quant_weight_diff",
                "quant_portfolio_bridge",
                "quant_package_info",
                "quant_model_policy",
                "quant_factor_ols",
            },
        )

    def test_checklist_quant_only_mock(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            code = checklist_main(["--mock", "--quant-only"])
        self.assertEqual(code, 0)
        self.assertIn("Quant-only: ON（11 quant_* case(s)）", buf.getvalue())

    def test_checklist_quant_only_unknown_case_fails(self):
        code = checklist_main(["--mock", "--quant-only", "--case", "buy_kuaishou"])
        self.assertEqual(code, 2)

    def test_agent_check_requires_api_key(self):
        with patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "", "DOUBAO_API_KEY": ""}, clear=False
        ):
            code = run_agent_check_main(["--quant-only"])
        self.assertEqual(code, 2)


class TestP37EvalSummaryCommands(unittest.TestCase):
    def test_summary_includes_quant_regression_commands(self):
        summary = EvalService().summary()
        cmds = summary["ci_commands"]
        self.assertIn("checklist_quant", cmds)
        self.assertIn("agent_weekend_quant", cmds)
        self.assertIn("quant_package_info", summary["quant_case_ids"])


if __name__ == "__main__":
    unittest.main()
