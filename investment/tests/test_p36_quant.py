import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.skill.engine import QuantEngine


class TestP36GoldenPackageInfo(unittest.TestCase):
    def test_golden_case_exists(self):
        ids = {c["id"] for c in load_cases()}
        self.assertIn("quant_package_info", ids)

    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_package_info")
        self.assertEqual(check_routing_expect(case), [])

    def test_infer_and_prepare_routing(self):
        q = "quant 包结构有哪些模块"
        self.assertEqual(infer_quant_task(q), "package_info")
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "package_info")

    def test_offline_mock_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_package_info")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "package_info")
        self.assertEqual(result.get("package"), "quant")
        self.assertIn("services", result.get("subpackages") or [])

    def test_eval_routing_map_includes_case(self):
        out = build_eval_routing_map()
        row = next(r for r in out["cases"] if r["id"] == "quant_package_info")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "package_info")


class TestP36QuantEnginePackageInfo(unittest.TestCase):
    def test_engine_without_mock_uses_real_package_info(self):
        out = QuantEngine().run({"task": "package_info"})
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("task"), "package_info")
        self.assertGreaterEqual(out.get("module_count", 0), 10)


if __name__ == "__main__":
    unittest.main()
