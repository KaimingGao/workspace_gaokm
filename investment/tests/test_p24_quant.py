import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_agent_check import main as run_agent_check_main
from quant.services.portfolio_quant_bridge import build_portfolio_quant_bridge
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine


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
            "core.watching_store.read_watching"
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


class TestP24Routing(unittest.TestCase):
    def test_infer_portfolio_bridge(self):
        self.assertEqual(infer_quant_task("持仓和量化怎么对照"), "portfolio_bridge")

    def test_prepare_portfolio_bridge(self):
        params = prepare_tool_params("quant", {}, "持仓联动状态")
        self.assertEqual(params.get("task"), "portfolio_bridge")

    def test_available_tasks_include_p24(self):
        self.assertIn("portfolio_bridge", AVAILABLE_TASKS)


class TestP24AgentCheckCli(unittest.TestCase):
    def test_agent_check_requires_api_key(self):
        with patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "", "DOUBAO_API_KEY": ""}, clear=False
        ):
            code = run_agent_check_main([])
        self.assertEqual(code, 2)


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


if __name__ == "__main__":
    unittest.main()
