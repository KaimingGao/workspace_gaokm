"""中性化对照研究口已下线：HTTP 410，问题回落到产品回测。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import load_cases


class TestP52NeutralCompareApi(unittest.TestCase):
    def test_portfolio_neutral_compare_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/quant/portfolio-neutral-compare",
            json={"top_k": 2, "horizon_days": 3, "min_score": 40},
        )
        self.assertEqual(res.status_code, 410, res.text)


class TestP53GoldenNeutralCompare(unittest.TestCase):
    def test_golden_case_removed(self):
        ids = {c["id"] for c in load_cases()}
        self.assertNotIn("quant_portfolio_neutral_compare", ids)
        self.assertNotIn("quant_daily_neutral_section", ids)

    def test_infer_falls_back_to_portfolio_backtest(self):
        q = "观察池组合中性化和绝对分回测差多少"
        self.assertEqual(infer_quant_task(q), "portfolio_backtest")
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "portfolio_backtest")


if __name__ == "__main__":
    unittest.main()
