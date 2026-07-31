import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.preset_check import check_daily_presets
from evals.run_preset_check import main as run_preset_check_main
from services.daily_service import DailyRunService


class TestQuantPaperDailyIntegration(unittest.TestCase):
    @patch("core.watching_health.check_watching_health")
    def test_quant_paper_preset_runs_rebalance(self, mock_health):
        mock_health.return_value = {
            "success": True,
            "issues": [],
            "warnings": [],
            "watchlist_count": 3,
        }
        paper = MagicMock()
        paper.exists.return_value = True
        paper.rebalance.return_value = {
            "success": True,
            "sell_trades": [],
            "buy_trades": [{"stock_code": "600519"}],
            "summary": {"equity": 100000},
        }

        with patch("quant.services.quant_service.QuantService") as qcls:
            inst = qcls.return_value
            inst.refresh_watching.return_value = {
                "success": True,
                "refresh": {"count": 3},
            }
            inst.run_cross_section.return_value = {
                "success": True,
                "ranked_count": 3,
            }
            inst.build_daily_report.return_value = {
                "success": True,
                "factor_ic": {"sample_count": 10},
            }
            inst.save_daily_report.return_value = os.path.join(ROOT, "data", "quant_daily.json")
            inst.save_report_exports.return_value = {
                "success": True,
                "paths": {"markdown": "/tmp/x.md"},
            }

            out = DailyRunService(paper=paper).run(preset="quant_paper")

        names = [s.get("name") for s in out.get("steps") or []]
        self.assertIn("paper_rebalance", names)
        reb = next(s for s in out["steps"] if s["name"] == "paper_rebalance")
        self.assertTrue(reb.get("ok"))
        paper.rebalance.assert_called_once()


class TestCiPresetCheck(unittest.TestCase):
    def test_preset_check_cli(self):
        code = run_preset_check_main([])
        self.assertEqual(code, 0)

    def test_quant_paper_in_expectations(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"])
        self.assertIn("quant_paper", out["checked"])


if __name__ == "__main__":
    unittest.main()
