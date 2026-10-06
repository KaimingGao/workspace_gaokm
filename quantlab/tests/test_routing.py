import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import BUY_QUESTION_HINT, POSITION_BASE_HINT, POSITION_HINT
from agent.routing import (
    build_user_hints,
    enrich_tool_result,
    is_buy_question,
    prepare_tool_params,
    wants_position_stance,
)


class TestRouting(unittest.TestCase):
    def test_buy_question_pure(self):
        self.assertTrue(is_buy_question("茅台能不能买"))
        self.assertIn(BUY_QUESTION_HINT, build_user_hints("茅台能不能买"))

    def test_position_stance_hint(self):
        q = "我的持仓要不要加仓"
        self.assertTrue(wants_position_stance(q))
        hints = build_user_hints(q)
        self.assertIn(POSITION_HINT, hints)
        self.assertNotIn(BUY_QUESTION_HINT, hints)

    def test_prepare_position_include_stance(self):
        params = prepare_tool_params("position", {}, "持仓还能不能加仓")
        self.assertTrue(params.get("include_stance"))

    def test_position_stance_variants(self):
        cases = [
            "我的股票还能继续买吗",
            "仓位要不要补一点",
            "portfolio 还能加吗",
            "套牢了还能不能加仓",
            "我的票值得买吗",
        ]
        for q in cases:
            with self.subTest(q=q):
                self.assertTrue(wants_position_stance(q), q)
                params = prepare_tool_params("position", {}, q)
                self.assertTrue(params.get("include_stance"), q)

    def test_position_without_stance(self):
        q = "我的持仓怎么看"
        self.assertFalse(wants_position_stance(q))
        self.assertIn(POSITION_BASE_HINT, build_user_hints(q))
        params = prepare_tool_params("position", {}, q)
        self.assertNotIn("include_stance", params)

    def test_enrich_advise_result(self):
        raw = (
            '{"success":true,"stance_label":"建议观望（暂不买入）",'
            '"stance_code":"wait","confidence":"medium","reasons":["x"]}'
        )
        out = enrich_tool_result("advise", raw)
        self.assertIn("【规则引擎结论】", out)
        self.assertIn("建议观望（暂不买入）", out)


if __name__ == "__main__":
    unittest.main()
