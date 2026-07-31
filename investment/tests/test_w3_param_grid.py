"""W3 · param-grid API smoke (mocked backtest cells)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestParamGrid(unittest.TestCase):
    def test_run_param_grid_picks_best(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        calls = []

        def fake_bt(**kwargs):
            calls.append(kwargs)
            lb = kwargs.get("lookback")
            tk = kwargs.get("top_k")
            return {
                "success": True,
                "metrics": {
                    "total_return_pct": float(lb) / 10 + float(tk),
                    "max_drawdown_pct": 5.0,
                    "win_rate_pct": 50.0,
                    "trade_count": 3,
                },
            }

        with patch.object(svc, "run_portfolio_backtest", side_effect=fake_bt):
            out = svc.run_param_grid(
                top_k_values=[2, 3],
                lookback_values=[60, 90],
                max_cells=4,
            )
        self.assertTrue(out["success"])
        self.assertEqual(out["cell_count"], 4)
        self.assertEqual(len(calls), 4)
        self.assertFalse(calls[0].get("include_wf_slices"))
        self.assertFalse(calls[0].get("include_cost_compare"))
        best = out["best"]
        self.assertIsNotNone(best)
        # 90/10+3 = 12 > others
        self.assertEqual(best["lookback"], 90)
        self.assertEqual(best["top_k"], 3)

    def test_param_grid_route(self):
        try:
            from fastapi.testclient import TestClient
            from web.app import app
        except ImportError:
            self.skipTest("fastapi not installed")
        client = TestClient(app)
        with patch(
            "web.deps.quant.run_param_grid",
            return_value={
                "success": True,
                "cells": [],
                "best": None,
                "cell_count": 0,
                "axes": {"lookback": [60], "top_k": [3]},
            },
        ):
            res = client.post("/api/quant/param-grid", json={"max_cells": 1})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json().get("success"))


if __name__ == "__main__":
    unittest.main()
