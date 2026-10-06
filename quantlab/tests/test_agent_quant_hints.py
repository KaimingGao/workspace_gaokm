"""Agent 量化提示 / 路由（合并原 P76/P83/P88）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import (
    MODEL_POLICY_HINT,
    QUANT_TASK_ENUM,
    QUANT_TASK_ROUTES,
    SCORE_STANCE_HINT,
    SYSTEM_PROMPT,
)
from agent.routing import (
    build_user_hints,
    infer_quant_task,
    mentions_model_policy,
    mentions_score,
)
from quant.skill.engine import AVAILABLE_TASKS


class TestAgentQuantHints(unittest.TestCase):
    def test_score_stance_hints(self):
        self.assertIn("signal.score", SYSTEM_PROMPT)
        self.assertIn("advise.stance_label", SYSTEM_PROMPT)
        self.assertIn("不等于买卖指令", SCORE_STANCE_HINT)
        out = build_user_hints("茅台短线分 score 什么意思")
        self.assertIn("score 与 stance", out)
        self.assertTrue(mentions_score("评分多少"))

    def test_model_policy_hints(self):
        self.assertIn("未默认使用", MODEL_POLICY_HINT)
        self.assertIn("stance_label", MODEL_POLICY_HINT)
        out = build_user_hints("为什么不用线性回归模型")
        self.assertIn("拟合模型说明", out)
        self.assertTrue(mentions_model_policy("有没有机器学习预测"))

    def test_factor_ols_routing(self):
        q = "茅台因子面板 OLS 实验对比 config 权重"
        self.assertEqual(infer_quant_task(q), "factor_ols")

        policy_q = "量化为什么不用线性回归或机器学习做决策"
        self.assertNotEqual(infer_quant_task(policy_q), "factor_ols")
        self.assertTrue(mentions_model_policy(policy_q))
        hints = build_user_hints(policy_q)
        self.assertIn("未默认使用", hints)

        self.assertIn("factor_ols", QUANT_TASK_ENUM)
        self.assertIn("factor_ols", AVAILABLE_TASKS)
        tasks = [t for t, _ in QUANT_TASK_ROUTES]
        self.assertIn("factor_ols", tasks)


if __name__ == "__main__":
    unittest.main()
