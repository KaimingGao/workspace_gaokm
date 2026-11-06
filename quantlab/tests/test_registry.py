import unittest

from agent.contracts import BaseSkillHandler, SkillHandler
from agent.registry import (
    SKILL_SPECS,
    TOOL_NAMES,
    create_handlers,
    load_tool_definitions,
    validate_registry,
)


class TestRegistry(unittest.TestCase):
    def test_tool_names_match_specs(self):
        self.assertEqual(TOOL_NAMES, tuple(n for n, _ in SKILL_SPECS))
        self.assertEqual(len(TOOL_NAMES), 14)
        self.assertIn("quant", TOOL_NAMES)
        self.assertIn("ops", TOOL_NAMES)

    def test_handlers_are_skill_handlers(self):
        handlers = create_handlers()
        # 全量 execute({}) 会打行情/资讯网；仅校验契约。网络路径由各 Skill 单测覆盖。
        offline_smoke = {"quant", "ops", "position", "advise", "backtest"}
        for name, handler in handlers.items():
            self.assertIsInstance(handler, BaseSkillHandler)
            self.assertIsInstance(handler, SkillHandler)
            if name in offline_smoke:
                out = handler.execute({"name": name, "parameters": {}})
                self.assertIsInstance(out, str)

    def test_validate_registry_ok(self):
        handlers = create_handlers()
        tools = load_tool_definitions()
        validate_registry(handlers, tools)
        self.assertEqual(len(tools), len(TOOL_NAMES))

if __name__ == "__main__":
    unittest.main()
