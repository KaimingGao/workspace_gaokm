"""FH5+：分组面板 PIT 财务探针与 lookahead_flags。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestClusterPanelsPit(unittest.TestCase):
    def test_pit_on_sets_pit_as_of_flags(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0 + i} for i in range(1, 40)]
        resolved = {
            "ok": True,
            "metrics": {"pe": 12.0, "pb": 1.5},
            "fundamentals_pit": True,
            "mode": "as_of",
            "as_of": "2024-01-20",
        }
        with patch(
            "core.data_service.get_quote",
            return_value={"success": True, "stock_code": "600519", "stock_name": "茅台"},
        ), patch(
            "core.data_service.bars_and_source", return_value=(bars, "akshare")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "600519")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value=resolved,
        ), patch(
            "core.signal.config.load_signal_config",
            return_value={"fundamentals": {"pit_mode": "as_of"}},
        ):
            out = build_cluster_ols_panels(
                ["600519"], lookback=40, pit_fundamentals=True
            )
        flags = out["lookahead_flags"]
        self.assertTrue(flags["pit_fundamentals"])
        self.assertEqual(flags["fundamentals"], "pit_as_of")
        panel = out["panels"][0]
        self.assertEqual(panel.get("fundamentals_mode"), "pit_as_of")
        self.assertIsNotNone(panel.get("fundamentals"))
        self.assertEqual(
            (flags.get("pit_summary") or {}).get("resolved_ok"), 1
        )

    def test_pit_off_marks_none(self):
        from quant.research.cluster_panels import build_cluster_ols_panels

        bars = [{"date": f"2024-01-{i:02d}", "close": 10.0} for i in range(1, 30)]
        with patch(
            "core.data_service.get_quote",
            return_value={"success": True, "stock_code": "000001"},
        ), patch(
            "core.data_service.bars_and_source", return_value=(bars, "akshare")
        ), patch(
            "core.ports.market.resolve_market_code", return_value=("cn", "000001")
        ), patch(
            "core.ports.market.default_benchmark", return_value="000300"
        ), patch(
            "core.ports.market.fetch_index_bars", return_value=([], "empty")
        ), patch(
            "core.signal.config.load_signal_config", return_value={}
        ):
            out = build_cluster_ols_panels(
                ["000001"], lookback=30, pit_fundamentals=False
            )
        self.assertEqual(out["lookahead_flags"]["fundamentals"], "none")
        self.assertIsNone(out["panels"][0].get("fundamentals"))


class TestClusterLiveAuditSplit(unittest.TestCase):
    def test_pick_audit_codes_round_robin(self):
        from core.signal.cluster_live_audit import pick_audit_codes

        cmap = {
            "a": {"cluster_label": "G1"},
            "b": {"cluster_label": "G1"},
            "c": {"cluster_label": "G2"},
        }
        codes = pick_audit_codes(cmap, [{"stock_code": "a"}], 2, offset=0)
        self.assertEqual(len(codes), 2)
        self.assertIn("a", codes)


if __name__ == "__main__":
    unittest.main()
