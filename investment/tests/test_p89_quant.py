import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP89WebFactorOls(unittest.TestCase):
    def test_factor_ols_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock_out = {
            "success": True,
            "task": "factor_ols",
            "stock_code": "600519",
            "r_squared": 0.12,
            "sample_count": 25,
            "coefficients": {"momentum": 0.1},
            "current_weights": {"momentum": 0.28},
        }
        with patch.object(
            deps.quant,
            "run_factor_ols_experiment",
            return_value=mock_out,
        ):
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/factor-ols",
                json={"code": "茅台", "lookback": 120, "horizon_days": 3},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data.get("task"), "factor_ols")

    def test_index_has_ols_controls(self):
        path = os.path.join(ROOT, "web", "static", "partials", "quant_panel.html")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("quant-ols-run", text)
        self.assertIn("quant-ols-summary", text)
        self.assertIn("quant-factor-list", text)
        self.assertIn("quant-ridge-lambda", text)
        self.assertIn("ridge λ", text)

    def test_app_js_has_factor_ols_api(self):
        path = os.path.join(ROOT, "web", "static", "js", "quant.js")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("/api/quant/factor-ols", text)
        self.assertIn("renderFactorOls", text)
        self.assertIn("OLS", text)
        self.assertIn("ridge_lambda", text)
        self.assertIn("readRidgeLambda", text)


if __name__ == "__main__":
    unittest.main()
