import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.skill.engine import QuantEngine


class TestP68QuantToolConfigParams(unittest.TestCase):
    def test_tool_config_has_offline_and_neutral_flags(self):
        path = os.path.join(ROOT, "skills", "quant", "tool_config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        props = cfg["parameters"]["properties"]
        self.assertIn("offline", props)
        self.assertFalse(props["offline"].get("default"))
        self.assertIn("include_portfolio_neutral_compare", props)
        self.assertTrue(props["include_portfolio_neutral_compare"].get("default"))

    def test_engine_honors_offline_interpret(self):
        out = QuantEngine().run(
            {"task": "interpret", "use_saved": False, "offline": True, "stock_code": "茅台"}
        )
        self.assertEqual(out.get("task"), "interpret")
        self.assertEqual(out.get("source"), "rule_based")

    def test_readme_documents_offline(self):
        path = os.path.join(ROOT, "skills", "quant", "README.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("offline", text)
        self.assertIn("include_portfolio_neutral_compare", text)


if __name__ == "__main__":
    unittest.main()
