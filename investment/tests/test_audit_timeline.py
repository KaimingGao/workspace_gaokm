"""W2.5 audit timeline + alerts/last smoke tests."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
