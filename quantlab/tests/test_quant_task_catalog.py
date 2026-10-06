"""Quant 任务目录同步（原 P57）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import json
from agent.prompts import QUANT_HINT, QUANT_TASK_ENUM, QUANT_TASK_ROUTES, SYSTEM_PROMPT
from agent.routing import infer_quant_task, prepare_tool_params
from quant.skill.engine import AVAILABLE_TASKS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p57_quant.py::TestP57QuantTaskCatalog ---

class TestP57QuantTaskCatalog(unittest.TestCase):
    def test_prompt_tasks_match_engine(self):
        route_tasks = [task for task, _ in QUANT_TASK_ROUTES]
        self.assertEqual(route_tasks, list(AVAILABLE_TASKS))
        self.assertEqual(QUANT_TASK_ENUM.count("|") + 1, len(AVAILABLE_TASKS))

    def test_system_prompt_lists_all_tasks(self):
        for task in AVAILABLE_TASKS:
            self.assertIn(task, SYSTEM_PROMPT)

    def test_quant_hint_covers_all_tasks(self):
        for task in AVAILABLE_TASKS:
            self.assertIn(task, QUANT_HINT)

    def test_tool_config_enum_matches(self):
        path = os.path.join(ROOT, "skills", "quant", "tool_config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        enum_tasks = cfg["parameters"]["properties"]["task"]["enum"]
        self.assertEqual(enum_tasks, list(AVAILABLE_TASKS))

    def test_infer_key_tasks(self):
        cases = {
            "观察池横截面 Top10": "cross_section",
            "quant 包结构有哪些模块": "package_info",
            "观察池组合 historically 如何": "portfolio_backtest",
            "观察池组合中性化和绝对分回测差多少": "portfolio_backtest",
            "量化日报中性化对照专节包含什么": "daily_summary",
            "量化日报 AI 解读": "interpret",
        }
        for question, task in cases.items():
            self.assertEqual(infer_quant_task(question), task, msg=question)
            params = prepare_tool_params("quant", {}, question)
            self.assertEqual(params.get("task"), task, msg=question)


if __name__ == "__main__":
    unittest.main()
