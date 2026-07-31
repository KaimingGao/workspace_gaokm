import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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
        self.assertEqual(len(TOOL_NAMES), 13)
        self.assertIn("quant", TOOL_NAMES)

    def test_handlers_are_skill_handlers(self):
        handlers = create_handlers()
        for name, handler in handlers.items():
            self.assertIsInstance(handler, BaseSkillHandler)
            self.assertIsInstance(handler, SkillHandler)
            out = handler.execute({"name": name, "parameters": {}})
            self.assertIsInstance(out, str)

    def test_validate_registry_ok(self):
        handlers = create_handlers()
        tools = load_tool_definitions()
        validate_registry(handlers, tools)
        self.assertEqual(len(tools), len(TOOL_NAMES))


if __name__ == "__main__":
    unittest.main()
