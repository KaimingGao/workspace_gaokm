"""Agent chat artifacts → Web 右侧 Tab。"""

from __future__ import annotations

import json
import unittest

from agent.artifacts import build_artifact, compact_payload, primary_tab, resolve_tab


class TestArtifacts(unittest.TestCase):
    def test_resolve_tab(self):
        self.assertEqual(resolve_tab("quant", {"task": "portfolio_backtest"}), "quant")
        self.assertEqual(resolve_tab("quant", {"task": "portfolio_bridge"}), "follow")
        self.assertEqual(resolve_tab("position", {}), "follow")
        self.assertEqual(resolve_tab("quote", {}), "reply")
        self.assertEqual(resolve_tab("backtest", {}), "quant")

    def test_build_artifact_portfolio(self):
        raw = json.dumps(
            {
                "success": True,
                "metrics": {"total_return_pct": 12.5, "win_rate_pct": 55},
                "trades_sample": [{"entry_date": "2024-01-01"}] * 30,
                "loaded_stocks": ["a"] * 5,
            },
            ensure_ascii=False,
        )
        art = build_artifact("quant", {"task": "portfolio_backtest"}, raw)
        self.assertEqual(art["tab"], "quant")
        self.assertTrue(art["success"])
        self.assertLessEqual(len(art["data"]["trades_sample"]), 12)
        self.assertIn("累计", art["summary"])

    def test_primary_tab(self):
        arts = [
            build_artifact("quote", {}, json.dumps({"price": 1})),
            build_artifact(
                "quant",
                {"task": "portfolio_backtest"},
                json.dumps({"success": True, "metrics": {"total_return_pct": 1}}),
            ),
        ]
        self.assertEqual(primary_tab(arts), "quant")
        self.assertEqual(primary_tab([]), "reply")

    def test_compact_keeps_metrics(self):
        out = compact_payload({"success": True, "metrics": {"a": 1}, "noise_big": {"x": 1}})
        self.assertEqual(out["metrics"]["a"], 1)


class TestChatArtifactsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from web.app import app

            cls.client = TestClient(app)
        except Exception:
            cls.client = None

    def test_chat_returns_artifacts(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        from unittest.mock import MagicMock, patch
        import web.deps as deps

        agent = MagicMock()
        agent.llm.api_key = "x"
        agent.llm.is_available.return_value = True
        agent.chat.return_value = "回测完成"
        agent.get_last_turn_usage.return_value = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "calls": 1,
            "reasoning_tokens": 0,
        }
        agent.get_session_usage.return_value = agent.get_last_turn_usage.return_value
        agent.get_last_artifacts.return_value = [
            {
                "tool": "quant",
                "params": {"task": "portfolio_backtest"},
                "tab": "quant",
                "success": True,
                "summary": "quant · portfolio_backtest",
                "data": {"success": True, "metrics": {"total_return_pct": 3}},
            }
        ]
        with patch.object(deps.chat, "get_or_create", return_value=("s1", agent)):
            res = self.client.post(
                "/api/chat",
                json={"message": "组合回测一下"},
                headers={"X-Session-Id": "s1"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["primary_tab"], "quant")
        self.assertEqual(len(data["artifacts"]), 1)
        self.assertEqual(data["artifacts"][0]["tab"], "quant")


if __name__ == "__main__":
    unittest.main()
