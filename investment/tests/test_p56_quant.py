import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.preset_check import check_daily_presets
from quant.ops.daily_presets import resolve_daily_preset
from services.daily_service import DailyRunService


class TestP56DailyPresetNeutralCompare(unittest.TestCase):
    def test_quant_presets_enable_neutral_compare(self):
        for name in ("quant", "full", "quant_paper"):
            flags = resolve_daily_preset(name)["flags"]
            self.assertTrue(
                flags["portfolio_neutral_compare"],
                msg=f"{name} should enable portfolio_neutral_compare",
            )

    def test_advisor_preset_disables_neutral_compare(self):
        flags = resolve_daily_preset("advisor")["flags"]
        self.assertFalse(flags["portfolio_neutral_compare"])

    def test_preset_check_expectations(self):
        out = check_daily_presets()
        self.assertTrue(out["ok"], msg="; ".join(out.get("failures") or []))

    def test_daily_service_passes_flag_to_build_daily_report(self):
        mock_report = {
            "factor_ic": {"sample_count": 3},
            "portfolio_neutral_compare_summary": {
                "success": True,
                "winner": "neutralized",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            svc = DailyRunService(last_run_path=os.path.join(tmp, "daily.json"))
            with patch(
                "quant.services.quant_service.QuantService.build_daily_report",
                return_value=mock_report,
            ) as build_mock, patch.object(
                svc,
                "_save_last_run",
            ), patch(
                "quant.services.quant_service.QuantService.save_daily_report",
                return_value="/tmp/quant_daily.json",
            ), patch(
                "quant.services.quant_service.QuantService.save_report_exports",
                return_value={"success": True, "paths": {}},
            ), patch(
                "quant.services.quant_service.QuantService.refresh_watching",
                return_value={"refresh": {"count": 2}},
            ), patch(
                "quant.services.quant_service.QuantService.run_cross_section",
                return_value={"success": True, "ranked_count": 2},
            ), patch(
                "core.watching_health.check_watching_health",
                return_value={"success": True, "warnings": [], "issues": []},
            ):
                out = svc.run(preset="quant")

            self.assertTrue(out["ok"])
            build_mock.assert_called_once()
            kwargs = build_mock.call_args.kwargs
            self.assertTrue(kwargs.get("include_portfolio_neutral_compare"))
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertTrue(step.get("portfolio_neutral_compare"))
            self.assertTrue(step.get("neutral_compare_ok"))
            self.assertEqual(step.get("neutral_compare_winner"), "neutralized")

    def test_daily_service_can_disable_neutral_compare(self):
        mock_report = {"factor_ic": {"sample_count": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            svc = DailyRunService(last_run_path=os.path.join(tmp, "daily.json"))
            with patch(
                "quant.services.quant_service.QuantService.build_daily_report",
                return_value=mock_report,
            ) as build_mock, patch.object(
                svc,
                "_save_last_run",
            ), patch(
                "quant.services.quant_service.QuantService.save_daily_report",
                return_value="/tmp/quant_daily.json",
            ), patch(
                "core.watching_health.check_watching_health",
                return_value={"success": True, "warnings": [], "issues": []},
            ):
                out = svc.run(preset="quant", portfolio_neutral_compare=False)

            kwargs = build_mock.call_args.kwargs
            self.assertFalse(kwargs.get("include_portfolio_neutral_compare"))
            step = next(s for s in out["steps"] if s["name"] == "quant_report")
            self.assertFalse(step.get("portfolio_neutral_compare"))


if __name__ == "__main__":
    unittest.main()
