import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import SCORE_STANCE_HINT, SYSTEM_PROMPT
from agent.routing import build_user_hints, mentions_score


class TestP76ScoreStanceAgentHints(unittest.TestCase):
    def test_system_prompt_distinguishes_score_and_stance(self):
        self.assertIn("signal.score", SYSTEM_PROMPT)
        self.assertIn("advise.stance_label", SYSTEM_PROMPT)

    def test_score_stance_hint_constant(self):
        self.assertIn("不等于买卖指令", SCORE_STANCE_HINT)

    def test_routing_injects_score_hint(self):
        out = build_user_hints("茅台短线分 score 什么意思")
        self.assertIn("score 与 stance", out)
        self.assertTrue(mentions_score("评分多少"))

    def test_quant_upgrade_documents_p73_p76(self):
        path = os.path.join(ROOT, "docs", "quant-upgrade.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for tag in ("P73", "P74", "P75", "P76"):
            self.assertIn(tag, text)


if __name__ == "__main__":
    unittest.main()
