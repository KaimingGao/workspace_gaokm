import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestP70InterpretOfflineApi(unittest.TestCase):
    def test_interpret_api_passes_offline_flag(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock_out = {
            "success": True,
            "source": "rule_based",
            "interpretation": "历史回测：规则解读测试",
        }
        with patch.object(deps.quant, "interpret_report", return_value=mock_out) as mocked:
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/interpret",
                json={"use_saved": True, "offline": True},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["source"], "rule_based")
        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        self.assertTrue(kwargs.get("offline"))

    def test_quant_service_offline_interpret(self):
        from quant.services.quant_interpret import build_rule_based_interpret
        from quant.services.quant_service import QuantService

        report = {
            "portfolio_backtest_summary": {
                "success": True,
                "total_return_pct": 1.2,
                "win_rate_pct": 50,
                "trade_count": 2,
            }
        }
        out = QuantService().interpret_report(report, use_saved=False, offline=True)
        expected = build_rule_based_interpret(report)
        self.assertEqual(out.get("source"), expected.get("source"))


if __name__ == "__main__":
    unittest.main()
