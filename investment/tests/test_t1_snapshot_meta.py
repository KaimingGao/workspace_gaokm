"""T1：日报冻结 snapshot_meta。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestDailySnapshotMeta(unittest.TestCase):
    def test_load_last_daily_adds_snapshot_meta(self):
        from quant.services.quant_service import QuantService

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "quant_daily.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "success": True,
                        "generated_at": "2026-07-30T00:53:00",
                        "portfolio_backtest_summary": {"success": True},
                    },
                    f,
                )
            svc = QuantService()
            with patch("quant.services.quant_service_ops.QUANT_DAILY_PATH", path):
                out = svc.load_last_daily()
            self.assertFalse(out.get("empty"))
            meta = out.get("snapshot_meta") or {}
            self.assertTrue(meta.get("frozen"))
            self.assertEqual(meta.get("source"), "quant_daily")
            self.assertEqual(meta.get("generated_at"), "2026-07-30T00:53:00")

    def test_build_daily_report_sets_generated_at(self):
        from quant.services.quant_service import QuantService

        svc = QuantService()
        with patch.object(svc, "config_summary", return_value={"success": True}):
            with patch.object(svc, "run_factor_report", return_value={"success": False}):
                with patch.object(svc, "run_factor_experiment", return_value={"success": False}):
                    with patch.object(svc, "run_factor_ols_experiment", return_value={"success": False}):
                        with patch.object(
                            svc, "suggest_thresholds", return_value={"success": False}
                        ):
                            with patch.object(svc, "list_strategies", return_value=[]):
                                with patch.object(
                                    svc,
                                    "portfolio_daily_summary",
                                    return_value={"success": True, "total_return_pct": 1},
                                ):
                                    with patch.object(
                                        svc,
                                        "portfolio_neutral_compare_summary",
                                        return_value={"success": True},
                                    ):
                                        report = svc.build_daily_report(
                                            include_cross_section=False,
                                            include_portfolio_backtest=True,
                                            include_portfolio_neutral_compare=True,
                                        )
        self.assertTrue(report.get("success"))
        self.assertTrue(str(report.get("generated_at") or "").startswith("20"))


if __name__ == "__main__":
    unittest.main()
