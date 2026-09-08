"""参数网格产品面已下线：POST /api/quant/param-grid 返回 410。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestParamGridGone(unittest.TestCase):
    def test_param_grid_route_gone(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")
        client = TestClient(app)
        res = client.post("/api/quant/param-grid", json={"max_cells": 1})
        self.assertEqual(res.status_code, 410, res.text)
        detail = res.json().get("detail") or ""
        self.assertIn("paper_replay", detail)


if __name__ == "__main__":
    unittest.main()
