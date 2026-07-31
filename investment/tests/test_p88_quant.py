import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import QUANT_TASK_ENUM, QUANT_TASK_ROUTES
from agent.routing import build_user_hints, infer_quant_task, mentions_model_policy
from quant.skill.engine import AVAILABLE_TASKS


class TestP88FactorOlsRouting(unittest.TestCase):
    def test_infer_factor_ols_for_research_question(self):
        q = "茅台因子面板 OLS 实验对比 config 权重"
        self.assertEqual(infer_quant_task(q), "factor_ols")

    def test_model_policy_question_not_routed_to_factor_ols(self):
        q = "量化为什么不用线性回归或机器学习做决策"
        self.assertNotEqual(infer_quant_task(q), "factor_ols")
        self.assertTrue(mentions_model_policy(q))
        hints = build_user_hints(q)
        self.assertIn("未默认使用", hints)

    def test_prompts_and_skill_include_factor_ols(self):
        self.assertIn("factor_ols", QUANT_TASK_ENUM)
        self.assertIn("factor_ols", AVAILABLE_TASKS)
        tasks = [t for t, _ in QUANT_TASK_ROUTES]
        self.assertIn("factor_ols", tasks)


if __name__ == "__main__":
    unittest.main()
