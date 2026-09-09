"""W2.5 audit timeline + alerts/last smoke tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestAuditTimeline(unittest.TestCase):
    def test_build_timeline_emptyish(self):
        from web.audit_timeline import build_audit_timeline

        with patch("services.platform_service.PlatformService") as MockPlat:
            inst = MockPlat.return_value
            inst.list_decisions.return_value = {"items": []}
            inst.get_schedule_last.return_value = {"empty": True}
            with patch("web.audit_timeline.load_promoted", return_value=None):
                with patch("os.path.isfile", return_value=False):
                    out = build_audit_timeline(limit=10)
        self.assertTrue(out["success"])
        self.assertIn("items", out)

    def test_unix_ts_normalized_and_decision_has_no_self_href(self):
        from web.audit_timeline import build_audit_timeline

        with patch("services.platform_service.PlatformService") as MockPlat:
            inst = MockPlat.return_value
            inst.list_decisions.return_value = {
                "items": [
                    {
                        "ts": 1756022400.0,
                        "stock_name": "测试",
                        "stance_label": "买入",
                        "source": "paper",
                        "id": "d1",
                    }
                ]
            }
            inst.get_schedule_last.return_value = {
                "last": {
                    "kind": "paper_daily",
                    "ts": 1756022500.0,
                    "strategy_id": "short_conservative",
                    "ok": True,
                }
            }
            with patch("web.audit_timeline.load_promoted", return_value=None):
                with patch("os.path.isfile", return_value=False):
                    out = build_audit_timeline(limit=10)
        items = out["items"]
        kinds = {ev["kind"]: ev for ev in items}
        self.assertIn("decision", kinds)
        self.assertIn("schedule", kinds)
        self.assertRegex(kinds["decision"]["ts"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(kinds["schedule"]["ts"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertNotIn("href", kinds["decision"])
        self.assertEqual(kinds["schedule"]["href"], "/platform#platform-schedule-section")

    def test_clear_timeline_logs_keeps_promote(self):
        from web.audit_timeline import clear_audit_timeline_logs

        with tempfile.TemporaryDirectory() as td:
            dec = os.path.join(td, "decisions.jsonl")
            alerts = os.path.join(td, "alerts_last.json")
            promo = os.path.join(td, "strategy_promoted.json")
            with open(dec, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "a", "ts": 1}) + "\n")
                f.write(json.dumps({"id": "b", "ts": 2}) + "\n")
            with open(alerts, "w", encoding="utf-8") as f:
                json.dump({"ts": 1, "alerts": []}, f)
            with open(promo, "w", encoding="utf-8") as f:
                json.dump({"promoted_at": "2026-08-05"}, f)
            with patch("core.decision_record.DECISIONS_PATH", dec):
                with patch("web.audit_timeline.ALERTS_LAST_PATH", alerts):
                    out = clear_audit_timeline_logs()
            self.assertTrue(out["ok"])
            self.assertEqual(out["decisions_cleared"], 2)
            self.assertTrue(out["alerts_last_cleared"])
            self.assertFalse(os.path.isfile(dec))
            self.assertFalse(os.path.isfile(alerts))
            self.assertTrue(os.path.isfile(promo))

    def test_routes(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")
        client = TestClient(app)
        with patch(
            "web.audit_timeline.build_audit_timeline",
            return_value={"success": True, "items": [], "count": 0},
        ):
            r1 = client.get("/api/audit/timeline")
        self.assertEqual(r1.status_code, 200, r1.text)
        with patch(
            "web.audit_timeline.read_alerts_last",
            return_value={"success": True, "empty": True, "alerts": [], "alert_count": 0},
        ):
            r2 = client.get("/api/alerts/last")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertTrue(r2.json().get("empty"))
        with patch(
            "web.deps.platform.clear_audit_timeline",
            return_value={
                "success": True,
                "ok": True,
                "decisions_cleared": 3,
                "alerts_last_cleared": True,
            },
        ):
            r3 = client.post("/api/audit/timeline/clear")
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertEqual(r3.json().get("decisions_cleared"), 3)


if __name__ == "__main__":
    unittest.main()
