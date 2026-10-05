"""数据中心名单加载须快路径：不盯市、不信号扫描。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.services.quant_service import QuantService
from services.paper_service import PaperService


class TestWatchingLoadFast(unittest.TestCase):
    def test_read_watching_skips_live_market(self):
        svc = QuantService()
        with patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["600519"], "max_size": 50},
        ), patch(
            "core.watching.store.watchlist_names_for", return_value=["茅台"]
        ), patch(
            "core.watching.store.watchlist_origins_for", return_value={}
        ), patch(
            "core.paper.load_paper",
            return_value={
                "holdings": [
                    {"stock_code": "600519", "shares": 100, "cost": 1600.0}
                ]
            },
        ), patch("os.path.isfile", return_value=True), patch(
            "core.paper.run_signal_scan"
        ) as scan, patch(
            "core.paper.mark_to_market"
        ) as mtm:
            out = svc.read_watching()
        self.assertTrue(out.get("exists"))
        self.assertEqual(out["watching"]["watchlist"], ["600519"])
        self.assertEqual(out["watching"]["watchlist_scores"], {})
        self.assertIn("600519", out["watching"]["watchlist_holdings"])
        scan.assert_not_called()
        mtm.assert_not_called()

    def test_paper_status_lite_skips_mtm_and_scores(self):
        svc = PaperService(path="/tmp/does-not-matter-paper.json")
        paper = {
            "name": "demo",
            "strategy_id": "short_conservative",
            "cash": 1e6,
            "holdings": [{"stock_code": "600519", "shares": 100, "cost": 10}],
            "operation_log": [{"type": "sync_paper", "ts": "2026-01-01"}],
        }
        with patch("os.path.isfile", return_value=True), patch(
            "services.paper_account.load_paper", return_value=paper
        ), patch("services.paper_account.mark_to_market") as mtm, patch.object(
            PaperService, "_compute_holding_scores"
        ) as scores:
            out = svc.status(lite=True)
        self.assertTrue(out.get("lite"))
        self.assertEqual(len(out["summary"]["holdings"]), 1)
        self.assertEqual(out["summary"]["holdings"][0]["stock_code"], "600519")
        mtm.assert_not_called()
        scores.assert_not_called()


if __name__ == "__main__":
    unittest.main()
