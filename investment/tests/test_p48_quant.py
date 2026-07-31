import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import load_signal_config
from core.signal.factor_panel import build_factor_panel, build_factor_panel_rows


class TestP48FactorPanel(unittest.TestCase):
    def test_panel_has_rows_with_weights(self):
        panel = build_factor_panel()
        self.assertTrue(panel["success"])
        self.assertGreaterEqual(panel["factor_count"], 8)
        self.assertAlmostEqual(panel["weight_sum"], 1.0, places=3)
        names = {r["factor"] for r in panel["rows"]}
        self.assertIn("value", names)
        self.assertIn("quality", names)
        self.assertIn("growth", names)

    def test_panel_merges_experiment_ic(self):
        experiment = {
            "factors": [
                {"factor": "momentum", "label": "动量", "ic": 0.05, "sample_count": 20},
                {
                    "factor": "value",
                    "label": "估值",
                    "ic": None,
                    "sample_count": 2,
                    "exclusion_reason": "sparse",
                },
            ],
            "exclusion_reasons": {"value": "sparse"},
        }
        rows = build_factor_panel_rows(experiment=experiment)
        by_name = {r["factor"]: r for r in rows}
        self.assertEqual(by_name["momentum"]["ic"], 0.05)
        self.assertEqual(by_name["volume_price"]["ic"], None)
        self.assertEqual(by_name["value"]["exclusion_reason"], "sparse")
        panel = build_factor_panel(experiment=experiment)
        self.assertEqual(panel["exclusion_reasons"].get("value"), "sparse")

    def test_config_weights_match_panel(self):
        cfg = load_signal_config(reload=True)
        panel = build_factor_panel(config=cfg)
        for row in panel["rows"]:
            self.assertIn(row["factor"], cfg["weights"])


class TestP48FactorPanelApi(unittest.TestCase):
    def test_factor_panel_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/quant/factor-panel")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(len(data["rows"]), 8)
        self.assertIn("factors", data)

    def test_factors_api_returns_panel_shape(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.get("/api/quant/factors")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(len(data.get("rows") or []), 8)


if __name__ == "__main__":
    unittest.main()
