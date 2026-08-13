import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import web.deps as deps  # noqa: E402


class TestWebApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            cls.client = None
            return
        cls.client = TestClient(app)

    def test_health(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        with patch("web.routers.meta.LLMClient") as MockLLM:
            inst = MockLLM.return_value
            inst.api_key = "x"
            inst.model = "test-model"
            inst.is_available.return_value = True
            inst.get_last_error.return_value = None
            res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("quote", data["tools"])

    def test_chat_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        from unittest.mock import MagicMock
        import web.app as web_app
        import web.deps as deps

        agent = MagicMock()
        agent.llm.api_key = "x"
        agent.llm.is_available.return_value = True
        agent.chat.return_value = "测试回复\n\n以上为量化研究与模拟结论，市场有风险，不保证收益，不代客下单。"
        agent.get_last_turn_usage.return_value = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "calls": 1,
            "reasoning_tokens": 0,
        }
        agent.get_session_usage.return_value = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "calls": 1,
            "reasoning_tokens": 0,
        }
        agent.get_last_artifacts.return_value = []
        with patch.object(deps.chat, "get_or_create", return_value=("test-session", agent)):
            res = self.client.post(
                "/api/chat",
                json={"message": "茅台现价"},
                headers={"X-Session-Id": "test-session"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["session_id"], "test-session")
        self.assertIn("测试回复", data["reply"])
        self.assertEqual(data.get("artifacts"), [])
        self.assertEqual(data.get("primary_tab"), "reply")

    def test_index_redirects_to_watching(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/", follow_redirects=False)
        self.assertIn(res.status_code, (301, 302, 303, 307, 308))
        self.assertEqual(res.headers.get("location"), "/watching")
        watching = self.client.get("/watching")
        self.assertEqual(watching.status_code, 200)
        self.assertIn("Investment", watching.text)
        self.assertIn('data-page="watching"', watching.text)
        self.assertIn("ai-drawer", watching.text)
        self.assertIn("api-degrade-banner", watching.text)
        self.assertIn("command-palette", watching.text)
        self.assertNotIn("workspace-results", watching.text)
        self.assertNotIn("dashboard-root", watching.text)
        self.assertNotIn(">仪表盘<", watching.text)

    def test_chat_page_offline_redirect(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/chat", follow_redirects=False)
        self.assertIn(res.status_code, (302, 307))
        loc = res.headers.get("location") or ""
        self.assertIn("/watching", loc)

    def test_quant_config(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/api/quant/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("weights", data)

    def test_quant_weight_suggest_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "current_weights": {"momentum": 0.4, "volume_price": 0.3},
            "suggested_weights": {"momentum": 0.43, "volume_price": 0.27},
            "rationale": ["momentum IC=+0.08 → 建议提高 momentum 权重 +0.03"],
            "config_diff": {
                "success": True,
                "target_file": "data/signal_config.json",
                "patch": {"weights": {"momentum": 0.43, "volume_price": 0.27}},
            },
        }
        with patch.object(deps.quant, "suggest_weights", return_value=mock_out):
            res = self.client.post(
                "/api/quant/weight-suggest",
                json={"code": "茅台", "lookback": 80, "horizon_days": 3},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertIn("suggested_weights", res.json())
        self.assertIn("config_diff", res.json())

    def test_quant_portfolio_backtest_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "loaded_stocks": ["600519", "600036"],
            "params": {"common_dates": 80, "top_k": 2},
            "metrics": {"trade_count": 3, "total_return_pct": 1.2, "win_rate_pct": 66.7},
            "trade_count": 3,
            "equity_curve": [
                {"date": "2026-01-10", "equity": 100.0, "return_pct": 0.0},
                {"date": "2026-01-13", "equity": 101.2, "return_pct": 1.2},
            ],
        }
        with patch.object(deps.quant, "run_portfolio_backtest", return_value=mock_out):
            res = self.client.post(
                "/api/quant/portfolio-backtest",
                json={"codes": ["600519", "600036"], "top_k": 2, "lookback": 80},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["loaded_stocks"]), 2)
        self.assertIn("equity_curve", data)

    def test_quant_threshold_suggest_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "current_thresholds": {"avoid": 45, "wait": 55, "probe": 68},
            "suggested_thresholds": {"avoid": 46, "wait": 57, "probe": 68},
            "config_diff": {"success": True, "patch": {"stance_thresholds": {"wait": 57}}},
        }
        with patch.object(deps.quant, "suggest_thresholds", return_value=mock_out):
            res = self.client.post(
                "/api/quant/threshold-suggest",
                json={"code": "茅台", "lookback": 80},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

    def test_signal_config_stance_save_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        mock_out = {
            "success": True,
            "stance_thresholds": {"avoid": -0.5, "wait": 0.5, "probe": 0.85},
            "signal_config_weights_touched": False,
            "scoring_touched": False,
        }
        with patch.object(deps.quant, "save_stance_thresholds", return_value=mock_out):
            res = self.client.post(
                "/api/signal/config/stance",
                json={
                    "avoid": -0.5,
                    "wait": 0.5,
                    "probe": 0.85,
                    "note": "test",
                },
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["stance_thresholds"]["wait"], 0.5)

    def test_quant_interpret_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "interpretation": "量化报告解读测试",
            "usage": {"total_tokens": 10},
        }
        with patch.object(deps.quant, "interpret_report", return_value=mock_out):
            res = self.client.post(
                "/api/quant/interpret",
                json={"use_saved": True},
            )
        self.assertEqual(res.status_code, 200)
        self.assertIn("解读", res.json()["interpretation"])

    def test_quant_export_html_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "format": "html",
            "filename": "quant_daily.html",
            "content": "<!DOCTYPE html><html><body>量化</body></html>",
        }
        with patch.object(deps.quant, "export_report", return_value=mock_out):
            res = self.client.get("/api/quant/export?format=html")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["format"], "html")

    def test_quant_export_markdown_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "success": True,
            "format": "markdown",
            "filename": "quant_daily.md",
            "content": "# 量化研究日报\n",
        }
        with patch.object(deps.quant, "export_report", return_value=mock_out):
            res = self.client.get("/api/quant/export?format=markdown")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertIn("量化研究日报", res.json()["content"])

    def test_paper_rebalance_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "ok": True,
            "success": True,
            "sell_trades": [{"stock_code": "600036", "side": "sell"}],
            "buy_trades": [{"stock_code": "600519", "side": "buy"}],
            "summary": {"equity": 99500.0},
        }
        with patch.object(deps.paper, "rebalance", return_value=mock_out):
            res = self.client.post(
                "/api/paper/rebalance",
                json={"top_k": 3, "limit": 10},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["sell_trades"]), 1)

    def test_paper_not_initialized(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService
        import web.app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            res = self.client.get("/api/paper")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["ok"])
            self.assertFalse(data["initialized"])

    def test_paper_init_and_status(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService
        import web.app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            res = self.client.post("/api/paper/init")
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["ok"])
            res2 = self.client.get("/api/paper")
            self.assertEqual(res2.status_code, 200)
            data = res2.json()
            self.assertTrue(data["initialized"])
            self.assertIn("summary", data)
            self.assertIn("equity", data["summary"])

    def test_paper_run_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService
        import web.app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            self.client.post("/api/paper/init")
            with patch("services.paper_service.run_daily_cycle") as mock_run:
                mock_run.return_value = {
                    "signals": 2,
                    "trades": 0,
                    "equity": 100000.0,
                }
                res = self.client.post(
                    "/api/paper/run",
                    json={"simulate_buy": False},
                )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["ok"])
            mock_run.assert_called_once()

    def test_paper_init_conflict(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService
        import web.app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            self.client.post("/api/paper/init")
            res = self.client.post("/api/paper/init")
            self.assertEqual(res.status_code, 409)

    def test_paper_manual_buy_sell(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService

        def _q(code):
            return {
                "success": True,
                "stock_code": code,
                "stock_name": "测试",
                "price_raw": 10.0,
            }

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            self.client.post("/api/paper/init")
            with patch("skills.common.quote_api.StockAPI.query", side_effect=_q):
                res = self.client.post(
                    "/api/paper/buy",
                    json={"stock_code": "600519", "amount": 5000},
                )
                self.assertEqual(res.status_code, 200, res.text)
                data = res.json()
                self.assertTrue(data["ok"])
                self.assertEqual(data["trade"]["shares"], 500)
                self.assertEqual(len(data["summary"]["holdings"]), 1)

                res2 = self.client.post(
                    "/api/paper/sell",
                    json={"stock_code": "600519", "shares": 200},
                )
                self.assertEqual(res2.status_code, 200, res2.text)
                held = res2.json()["summary"]["holdings"][0]["shares"]
                self.assertEqual(held, 300)

                res3 = self.client.post(
                    "/api/paper/sell",
                    json={"codes": ["600519"]},
                )
                self.assertEqual(res3.status_code, 200, res3.text)
                self.assertEqual(res3.json()["summary"]["holdings"], [])

    def test_paper_holding_chart(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile

        from services.paper_service import PaperService

        def _q(code):
            return {
                "success": True,
                "stock_code": code,
                "stock_name": "测试",
                "price_raw": 10.0,
            }

        bars = [
            {"date": f"2024-01-{i:02d}", "close": 9.0 + i * 0.1}
            for i in range(1, 12)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            deps.paper = PaperService(path)
            self.client.post("/api/paper/init")
            with patch("skills.common.quote_api.StockAPI.query", side_effect=_q):
                self.client.post(
                    "/api/paper/buy",
                    json={"stock_code": "600519", "amount": 5000},
                )
            with patch(
                "skills.common.history.fetch_daily_bars",
                return_value=(bars, "mock"),
            ):
                res = self.client.get("/api/paper/holding-chart?code=600519")
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["mode"], "stock")
            self.assertGreaterEqual(len(data["points"]), 10)

    def test_evals_cases(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/api/evals/cases")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["cases"]), 15)

    def test_evals_run_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_report = {
            "ok": True,
            "total": 1,
            "passed": 1,
            "failed": 0,
            "use_mock": True,
            "with_agent": False,
            "cases": [{"id": "quote_moutai", "ok": True, "failures": [], "skills": []}],
            "failures": [],
        }
        with patch.object(deps.evals, "run", return_value=mock_report):
            res = self.client.post(
                "/api/evals/run",
                json={"use_mock": True, "with_agent": False, "case_id": "quote_moutai"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_evals_last_empty(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import tempfile
        import web.app as web_app
        from services.eval_service import EvalService

        with tempfile.TemporaryDirectory() as tmp:
            deps.evals = EvalService(
                last_run_path=os.path.join(tmp, "last.json"),
                job_path=os.path.join(tmp, "job.json"),
            )
            res = self.client.get("/api/evals/last")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["exists"])

    def test_evals_job_idle(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/api/evals/job")
        self.assertEqual(res.status_code, 200)
        self.assertIn("job", res.json())

    def test_daily_run_mocked(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        import web.app as web_app

        mock_out = {
            "ok": True,
            "steps": [
                {"name": "paper_run", "ok": True, "equity": 100000},
                {"name": "eval_mock", "ok": True, "passed": 11, "failed": 0, "total": 11},
            ],
            "failures": [],
        }
        with patch.object(deps.daily, "run", return_value=mock_out):
            res = self.client.post(
                "/api/daily/run",
                json={"paper_run": True, "eval_mock": True},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])


if __name__ == "__main__":
    unittest.main()
