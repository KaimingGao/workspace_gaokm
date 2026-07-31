import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import MODEL_POLICY_HINT, SYSTEM_PROMPT
from agent.routing import build_user_hints, mentions_model_policy


class TestP83ModelPolicyAgentHints(unittest.TestCase):
    def test_model_policy_hint_constant(self):
        self.assertIn("未默认使用", MODEL_POLICY_HINT)
        self.assertIn("stance_label", MODEL_POLICY_HINT)

    def test_routing_injects_model_hint(self):
        out = build_user_hints("为什么不用线性回归模型")
        self.assertIn("拟合模型说明", out)
        self.assertTrue(mentions_model_policy("有没有机器学习预测"))

    def test_quant_upgrade_documents_p81_p84(self):
        path = os.path.join(ROOT, "docs", "quant-upgrade.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for tag in ("P81", "P82", "P83", "P84"):
            self.assertIn(tag, text)


if __name__ == "__main__":
    unittest.main()
