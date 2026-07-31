import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP28QuantPackage(unittest.TestCase):
    def test_quant_package_exports(self):
        import quant

        self.assertTrue(hasattr(quant, "QuantService"))

    def test_skills_quant_init_reexports(self):
        from skills.quant import QuantEngine
        from quant.skill.engine import QuantEngine as CanonicalEngine

        self.assertIs(QuantEngine, CanonicalEngine)

    def test_skill_registry(self):
        from quant.skill.engine import AVAILABLE_TASKS
        from agent.registry import SKILL_SPECS

        self.assertIn("portfolio_bridge", AVAILABLE_TASKS)
        quant_spec = next(spec for name, spec in SKILL_SPECS if name == "quant")
        self.assertEqual(quant_spec, "quant.skill.handler.QuantHandler")


if __name__ == "__main__":
    unittest.main()
