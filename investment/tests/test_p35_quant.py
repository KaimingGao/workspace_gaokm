import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine


class TestP35PackageInfoRouting(unittest.TestCase):
    def test_infer_package_info(self):
        self.assertEqual(infer_quant_task("quant 包结构有哪些模块"), "package_info")
        self.assertEqual(infer_quant_task("quant/ 目录树"), "package_info")

    def test_prepare_package_info(self):
        params = prepare_tool_params("quant", {}, "quant 包结构有哪些模块")
        self.assertEqual(params.get("task"), "package_info")

    def test_available_tasks_include_package_info(self):
        self.assertIn("package_info", AVAILABLE_TASKS)


class TestP35PackageInfoTask(unittest.TestCase):
    @patch("quant.services.quant_service.QuantService.build_package_info")
    def test_package_info_task(self, mock_info):
        mock_info.return_value = {
            "success": True,
            "package": "quant",
            "module_count": 12,
            "subpackages": ["services", "ops", "research", "skill"],
        }
        out = QuantEngine().run({"task": "package_info"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "package_info")
        self.assertEqual(out["module_count"], 12)


if __name__ == "__main__":
    unittest.main()
