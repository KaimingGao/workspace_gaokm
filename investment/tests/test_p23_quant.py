import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from quant.ops.daily_presets import list_daily_presets
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine


class TestP23QuantRouting(unittest.TestCase):
    def test_infer_config_diff_task(self):
        self.assertEqual(infer_quant_task("signal_config diff 怎么合并"), "config_diff")
        self.assertEqual(infer_quant_task("配置 diff 预览"), "config_diff")

    def test_weight_diff_still_weight_suggest(self):
        self.assertEqual(infer_quant_task("量化因子权重 diff 怎么改配置"), "weight_suggest")

    def test_infer_daily_presets_task(self):
        self.assertEqual(infer_quant_task("daily preset 有哪些"), "daily_presets")
        self.assertEqual(infer_quant_task("cron 定时任务怎么配"), "daily_presets")

    def test_prepare_tool_params_new_tasks(self):
        params = prepare_tool_params("quant", {}, "配置 diff 预览")
        self.assertEqual(params.get("task"), "config_diff")

    def test_prepare_tool_params_daily_presets(self):
        params = prepare_tool_params("quant", {}, "有哪些 daily preset")
        self.assertEqual(params.get("task"), "daily_presets")


class TestP23QuantTasks(unittest.TestCase):
    def test_available_tasks_include_p23(self):
        self.assertIn("config_diff", AVAILABLE_TASKS)
        self.assertIn("daily_presets", AVAILABLE_TASKS)

    @patch("quant.services.quant_service.QuantService.build_config_diff_preview")
    def test_config_diff_task(self, mock_preview):
        mock_preview.return_value = {
            "success": True,
            "ok": True,
            "weights": {"success": True, "changes": {"momentum": {"from": 0.3, "to": 0.35}}},
            "thresholds": {"success": False},
            "note": "预览",
        }
        out = QuantEngine().run({"task": "config_diff"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "config_diff")
        self.assertEqual(out["change_counts"]["weights"], 1)

    def test_daily_presets_task(self):
        out = QuantEngine().run({"task": "daily_presets"})
        self.assertTrue(out["success"])
        names = {p["name"] for p in out["presets"]}
        self.assertEqual(names, {p["name"] for p in list_daily_presets()})


class TestP23WebPresets(unittest.TestCase):
    def test_daily_presets_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/daily/presets")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        names = {p["name"] for p in data["presets"]}
        self.assertEqual(names, {"advisor", "quant", "full", "quant_paper"})


if __name__ == "__main__":
    unittest.main()
