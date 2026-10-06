"""W4 · /ws/live WebSocket smoke test."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestLiveWs(unittest.TestCase):
    def test_snapshot_shape(self):
        from web.routers import live_ws

        fake_paper = {
            "initialized": True,
            "summary": {
                "equity": 1_000_000,
                "cash": 200_000,
                "position_count": 3,
                "total_pnl_pct": 1.2,
                "invested_pct": 80.0,
                "max_drawdown_pct": 5.0,
            },
            "snapshots": [{"ts": "2026-01-01", "equity": 1_000_000}],
            "strategy_id": "demo",
        }
        with patch("web.deps.paper") as paper_svc:
            paper_svc.status.return_value = fake_paper
            with patch(
                "quant.ops.daily_health.build_daily_health",
                return_value={"ok": True, "issues": [], "warnings": []},
            ):
                with patch(
                    "web.audit_timeline.read_alerts_last",
                    return_value={"success": True, "empty": True, "alert_count": 0},
                ):
                    snap = live_ws._snapshot()
        self.assertEqual(snap["type"], "snapshot")
        self.assertIsNotNone(snap.get("paper"))
        self.assertEqual(snap["paper"]["equity"], 1_000_000)
        self.assertTrue(snap["health"]["ok"])

    def test_ws_connect(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")

        fake = {
            "type": "snapshot",
            "ts": 1.0,
            "paper": {"equity": 100.0},
            "health": {"ok": True, "issues": [], "warnings": []},
            "alerts": {"empty": True, "alert_count": 0},
        }
        client = TestClient(app)
        with patch("web.routers.live_ws._snapshot", return_value=fake):
            with client.websocket_connect("/ws/live") as ws:
                raw = ws.receive_text()
                data = json.loads(raw)
                self.assertEqual(data.get("type"), "snapshot")
                self.assertEqual(data.get("paper", {}).get("equity"), 100.0)
                ws.send_text(json.dumps({"type": "ping"}))
                raw2 = ws.receive_text()
                data2 = json.loads(raw2)
                self.assertEqual(data2.get("type"), "snapshot")


if __name__ == "__main__":
    unittest.main()
