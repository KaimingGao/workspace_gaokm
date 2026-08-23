"""Eval / golden / quant 路由（合并原 P15 / P23–P26 / P31·routing / P36–P38 + 相关 golden）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.prompts import QUANT_HINT
from agent.routing import build_user_hints, infer_quant_task, is_quant_question, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.services.quant_report_export import export_quant_report, render_quant_report_html
import json
import tempfile
from unittest.mock import MagicMock, patch
from core.watching.health import check_watching_health
from quant.ops.daily_health import build_daily_health
from quant.services.quant_report_index import list_quant_reports, read_quant_report_file
from quant.services.quant_service import QuantService
from quant.skill.engine import QuantEngine
from unittest.mock import patch
from evals.run_checklist import load_cases, run_skills
from research.daily_check import check_daily_last_run, main as daily_check_main
from quant.services.signal_config_preview import build_config_diff_preview
from agent.routing import infer_quant_task, prepare_tool_params
from quant.ops.daily_presets import list_daily_presets
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine
from evals.run_agent_check import main as run_agent_check_main
from quant.services.portfolio_quant_bridge import build_portfolio_quant_bridge
from services.eval_service import EvalService
from evals.run_checklist import load_cases
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.services.quant_report_index import list_quant_reports
import importlib
from io import StringIO
from evals.run_checklist import filter_quant_cases, load_cases, main as checklist_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p15_quant.py::TestQuantRouting ---
class TestQuantRouting(unittest.TestCase):
    def test_is_quant_question(self):
        self.assertTrue(is_quant_question("观察池组合 historically 表现如何"))
        self.assertFalse(is_quant_question("茅台现价"))
    def test_infer_quant_task(self):
        self.assertEqual(infer_quant_task("观察池组合 historically 表现如何"), "portfolio_backtest")
        self.assertEqual(infer_quant_task("量化报告摘要"), "daily_summary")
    def test_prepare_tool_params_quant(self):
        q = "观察池组合 historically 表现如何"
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "portfolio_backtest")
    def test_build_user_hints_quant(self):
        hinted = build_user_hints("量化报告怎么说")
        self.assertIn(QUANT_HINT, hinted)

# --- test_p15_quant.py::TestGoldenQuantCase ---
class TestGoldenQuantCase(unittest.TestCase):
    def test_quant_portfolio_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_backtest")
        self.assertEqual(check_routing_expect(case), [])
    def test_quant_portfolio_offline_mock(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_backtest")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertIn("metrics", result)

# --- test_p17_quant.py::TestInferQuantHealthRouting ---
class TestInferQuantHealthRouting(unittest.TestCase):
    def test_infer_health_task(self):
        from agent.routing import infer_quant_task

        self.assertEqual(infer_quant_task("量化系统状态怎么样"), "health")

# --- test_p18_quant.py::TestQuantHealthGolden ---
class TestQuantHealthGolden(unittest.TestCase):
    def test_quant_health_case_offline(self):
        case = next(c for c in load_cases() if c["id"] == "quant_health")
        out = run_skills(case, use_mock=True)
        quant = out["bundled"]["quant"]
        self.assertTrue(quant.get("success"))
        self.assertTrue(quant.get("ok"))
        self.assertEqual(quant.get("task"), "health")

# --- test_p20_quant.py::TestQuantWeightDiffGolden ---
class TestQuantWeightDiffGolden(unittest.TestCase):
    def test_quant_weight_diff_offline(self):
        case = next(c for c in load_cases() if c["id"] == "quant_weight_diff")
        out = run_skills(case, use_mock=True)
        quant = out["bundled"]["quant"]
        self.assertTrue(quant.get("success"))
        self.assertTrue((quant.get("config_diff") or {}).get("success"))

# --- test_p23_quant.py::TestP23QuantRouting ---
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

# --- test_p23_quant.py::TestP23QuantTasks ---
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

# --- test_p24_quant.py::TestP24PortfolioBridge ---
class TestP24PortfolioBridge(unittest.TestCase):
    @patch("quant.services.quant_service.QuantService.load_last_daily")
    @patch("os.path.isfile")
    def test_bridge_empty_paper(self, mock_isfile, mock_daily):
        mock_isfile.return_value = False
        mock_daily.return_value = {"empty": True}
        out = build_portfolio_quant_bridge()
        self.assertTrue(out["success"])
        self.assertFalse(out["portfolio"]["exists"])
        self.assertIn("未初始化", out["note"])
    @patch("quant.services.quant_service.QuantService.load_last_daily")
    @patch("os.path.isfile")
    def test_bridge_with_holdings(self, mock_isfile, mock_daily):
        def isfile(path):
            s = str(path)
            return s.endswith("paper.json") or s.endswith("watching.json")

        mock_isfile.side_effect = isfile
        mock_daily.return_value = {"empty": False}
        with patch("core.paper.load_paper") as mock_paper, patch(
            "core.watching.store.read_watching"
        ) as mock_uni:
            mock_paper.return_value = {
                "cash": 10000,
                "holdings": [{"stock_code": "600519", "shares": 100, "cost": 1500}],
                "watchlist": [],
            }
            mock_uni.return_value = {"watchlist": ["600519", "000001"]}
            out = build_portfolio_quant_bridge()
        self.assertEqual(out["portfolio"]["count"], 1)
        self.assertEqual(out["portfolio"]["source"], "paper")
        self.assertEqual(out["quant"]["overlap_count"], 1)
        self.assertEqual(out.get("actions"), {})
    @patch("quant.services.quant_service.QuantService.build_portfolio_bridge")
    def test_quant_portfolio_bridge_task(self, mock_bridge):
        mock_bridge.return_value = {"success": True, "task": "portfolio_bridge"}
        out = QuantEngine().run({"task": "portfolio_bridge"})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "portfolio_bridge")

# --- test_p24_quant.py::TestP24Routing ---
class TestP24Routing(unittest.TestCase):
    def test_infer_portfolio_bridge(self):
        self.assertEqual(infer_quant_task("持仓和量化怎么对照"), "portfolio_bridge")
    def test_prepare_portfolio_bridge(self):
        params = prepare_tool_params("quant", {}, "持仓联动状态")
        self.assertEqual(params.get("task"), "portfolio_bridge")
    def test_available_tasks_include_p24(self):
        self.assertIn("portfolio_bridge", AVAILABLE_TASKS)

# --- test_p24_quant.py::TestP24WebBridgeApi ---
class TestP24WebBridgeApi(unittest.TestCase):
    def test_portfolio_quant_bridge_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        with patch.object(
            deps.quant,
            "build_portfolio_bridge",
            return_value={"success": True, "task": "portfolio_bridge"},
        ):
            client = TestClient(web_app.app)
            res = client.get("/api/portfolio/quant-bridge")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

# --- test_p25_quant.py::TestP25GoldenPortfolioBridge ---
class TestP25GoldenPortfolioBridge(unittest.TestCase):
    def test_case_exists_and_routing(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_bridge")
        self.assertEqual(check_routing_expect(case), [])
    def test_offline_mock_skills(self):
        case = next(c for c in load_cases() if c["id"] == "quant_portfolio_bridge")
        run = run_skills(case, use_mock=True)
        self.assertTrue(run["skill_runs"][0]["result"].get("success"))

# --- test_p25_quant.py::TestP25EvalService ---
class TestP25EvalService(unittest.TestCase):
    def test_summary_includes_case_count(self):
        summary = EvalService().summary()
        self.assertTrue(summary["success"])
        self.assertGreaterEqual(summary["case_count"], 16)
        self.assertIn("quant_portfolio_bridge", summary["quant_case_ids"])
        self.assertTrue(summary["presets"]["ok"])
    def test_run_with_presets(self):
        report = EvalService().run(use_mock=True, with_agent=False, with_presets=True, save=False)
        self.assertIn("presets", report)
        self.assertTrue(report["presets"]["ok"])

# --- test_p25_quant.py::TestP25EvalsApi ---
class TestP25EvalsApi(unittest.TestCase):
    def test_evals_summary_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(data["case_count"], 16)
    def test_evals_presets_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/presets")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
    def test_evals_run_with_presets(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/evals/run",
            json={
                "case_id": "quant_portfolio_bridge",
                "use_mock": True,
                "with_agent": False,
                "with_presets": False,
            },
        )
        self.assertIn(res.status_code, (200, 422))
        data = res.json()
        self.assertEqual(data["total"], 1)

# --- test_p26_quant.py::TestP26EvalRoutingMap ---
class TestP26EvalRoutingMap(unittest.TestCase):
    def test_all_routing_expect_ok(self):
        out = build_eval_routing_map()
        self.assertTrue(out["success"])
        self.assertEqual(out["count"], len(load_cases()))
        self.assertEqual(out["ok_count"], out["with_expect"])
        for row in out["cases"]:
            if row["has_routing_expect"]:
                self.assertTrue(row["ok"], row)
    def test_eval_service_list_routing(self):
        out = EvalService().list_routing()
        self.assertGreaterEqual(out["with_expect"], 5)

# --- test_p26_quant.py::TestP26Api ---
class TestP26Api(unittest.TestCase):
    def test_evals_routing_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/evals/routing")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ok_count"], data["with_expect"])

# --- test_p31_quant.py::TestP31EvalRouting ---
class TestP31EvalRouting(unittest.TestCase):
    def test_build_eval_routing_map_from_quant_ops(self):
        from quant.ops.eval_routing_map import build_eval_routing_map

        out = build_eval_routing_map()
        self.assertTrue(out["success"])
        self.assertGreaterEqual(out["with_expect"], 5)
    def test_eval_service_uses_quant_ops(self):
        from services.eval_service import EvalService

        out = EvalService().list_routing()
        self.assertTrue(out["success"])

# --- test_p36_quant.py::TestP36GoldenPackageInfo ---
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

# --- test_p36_quant.py::TestP36QuantEnginePackageInfo ---
class TestP36QuantEnginePackageInfo(unittest.TestCase):
    def test_engine_without_mock_uses_real_package_info(self):
        out = QuantEngine().run({"task": "package_info"})
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("task"), "package_info")
        self.assertGreaterEqual(out.get("module_count", 0), 10)

# --- test_p37_quant.py::TestP37QuantOnlyFilter ---
class TestP37QuantOnlyFilter(unittest.TestCase):
    def test_filter_quant_cases(self):
        quant = filter_quant_cases(load_cases())
        ids = {c["id"] for c in quant}
        self.assertEqual(
            ids,
            {
                "quant_portfolio_backtest",
                "quant_portfolio_neutral_compare",
                "quant_daily_neutral_section",
                "quant_interpret_neutral",
                "quant_cross_section_score",
                "quant_health",
                "quant_weight_diff",
                "quant_portfolio_bridge",
                "quant_package_info",
                "quant_model_policy",
                "quant_factor_ols",
            },
        )
    def test_checklist_quant_only_mock(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            code = checklist_main(["--mock", "--quant-only"])
        self.assertEqual(code, 0)
        self.assertIn("Quant-only: ON（11 quant_* case(s)）", buf.getvalue())
    def test_checklist_quant_only_unknown_case_fails(self):
        code = checklist_main(["--mock", "--quant-only", "--case", "buy_kuaishou"])
        self.assertEqual(code, 2)
    def test_agent_check_requires_api_key(self):
        with patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "", "DOUBAO_API_KEY": ""}, clear=False
        ):
            code = run_agent_check_main(["--quant-only"])
        self.assertEqual(code, 2)

# --- test_p37_quant.py::TestP37EvalSummaryCommands ---
class TestP37EvalSummaryCommands(unittest.TestCase):
    def test_summary_includes_quant_regression_commands(self):
        summary = EvalService().summary()
        cmds = summary["ci_commands"]
        self.assertIn("checklist_quant", cmds)
        self.assertIn("agent_weekend_quant", cmds)
        self.assertIn("quant_package_info", summary["quant_case_ids"])

# --- test_p38_quant.py::TestP38EvalServiceQuantOnly ---
class TestP38EvalServiceQuantOnly(unittest.TestCase):
    def test_run_quant_only_mock(self):
        report = EvalService().run(
            use_mock=True,
            with_agent=False,
            with_presets=True,
            quant_only=True,
            save=False,
        )
        self.assertTrue(report.get("quant_only"))
        self.assertEqual(report["total"], 11)
        self.assertTrue(report["ok"])
        ids = {c["id"] for c in report["cases"]}
        self.assertTrue(ids.issubset({c for c in ids if c.startswith("quant_")}))
        self.assertIn("quant_daily_neutral_section", ids)
    def test_run_quant_only_unknown_case(self):
        report = EvalService().run(
            case_id="buy_kuaishou",
            use_mock=True,
            quant_only=True,
            save=False,
        )
        self.assertFalse(report.get("ok"))
        self.assertIn("quant_*", report.get("error", ""))

# --- test_p38_quant.py::TestP38EvalsApiQuantOnly ---
class TestP38EvalsApiQuantOnly(unittest.TestCase):
    def test_evals_run_quant_only_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/evals/run",
            json={
                "use_mock": True,
                "with_agent": False,
                "with_presets": True,
                "quant_only": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("quant_only"))
        self.assertEqual(data["total"], 11)
        self.assertTrue(data["ok"])
    def test_summary_web_quant_ci_command(self):
        summary = EvalService().summary()
        self.assertIn("web_quant_ci", summary["ci_commands"])


if __name__ == "__main__":
    unittest.main()
