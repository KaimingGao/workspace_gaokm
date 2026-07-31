"""R1 · 财务 PIT / 冲击成本 / 源一致性单测。"""

from __future__ import annotations

import os
import tempfile
import unittest

from core.fundamentals_pit import (
    fundamentals_pit_summary,
    merge_history_point,
    resolve_fundamentals_for_score,
    select_point_as_of,
)
from core.store import save_snapshot_cache, snapshot_cache_path


class TestFundamentalsPit(unittest.TestCase):
    def test_select_point_no_lookahead(self):
        history = [
            {"as_of": "2024-03-31", "metrics": {"pe": 20.0, "pb": 2.0}},
            {"as_of": "2024-06-30", "metrics": {"pe": 18.0, "pb": 1.8}},
            {"as_of": "2024-12-31", "metrics": {"pe": 15.0, "pb": 1.5}},
        ]
        point, meta = select_point_as_of(history, "2024-07-15")
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["selected_as_of"], "2024-06-30")
        self.assertEqual(point["metrics"]["pe"], 18.0)

        missing, meta2 = select_point_as_of(history, "2023-01-01")
        self.assertIsNone(missing)
        self.assertEqual(meta2["reason"], "no_point_on_or_before")
        self.assertTrue(meta2["lookahead"])

    def test_resolve_rejects_future_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            code = "600519"
            # 写入只有 2025 年点
            save_snapshot_cache(
                "fundamentals",
                code,
                {
                    "success": True,
                    "metrics": {
                        "pe": 25.0,
                        "pb": 3.0,
                        "roe": 30.0,
                        "as_of": "2025-06-30",
                    },
                },
                data_source="test",
                store_dir=td,
                as_of="2025-06-30",
            )
            # monkey via store_dir: resolve uses default STORE — patch panel loader
            from unittest.mock import patch

            panel = {
                "ok": True,
                "history": [
                    {
                        "as_of": "2025-06-30",
                        "metrics": {"pe": 25.0, "pb": 3.0, "roe": 30.0},
                    }
                ],
            }
            with patch(
                "core.fundamentals_pit.load_fundamentals_panel",
                return_value=panel,
            ):
                out = resolve_fundamentals_for_score(
                    code,
                    as_of="2024-01-01",
                    fund_cfg={
                        "pit_mode": "as_of",
                        "missing_as_of_policy": "zero_weight",
                    },
                    live_fallback=False,
                )
            self.assertFalse(out["ok"])
            self.assertIsNone(out["metrics"])
            self.assertEqual(out["mode"], "as_of_missing")
            self.assertTrue((out.get("pit_meta") or {}).get("lookahead"))

    def test_resolve_as_of_ok(self):
        panel = {
            "ok": True,
            "history": [
                {"as_of": "2023-12-31", "metrics": {"pe": 22.0, "pb": 2.2}},
                {"as_of": "2024-06-30", "metrics": {"pe": 19.0, "pb": 2.0}},
            ],
        }
        from unittest.mock import patch

        with patch(
            "core.fundamentals_pit.load_fundamentals_panel",
            return_value=panel,
        ):
            out = resolve_fundamentals_for_score(
                "600519",
                as_of="2024-08-01",
                fund_cfg={"pit_mode": "as_of"},
                live_fallback=False,
            )
        self.assertTrue(out["ok"])
        self.assertTrue(out["fundamentals_pit"])
        self.assertEqual(out["metrics"]["pe"], 19.0)
        self.assertEqual(out["as_of"], "2024-06-30")

    def test_save_snapshot_builds_history(self):
        with tempfile.TemporaryDirectory() as td:
            code = "000001"
            save_snapshot_cache(
                "fundamentals",
                code,
                {"success": True, "metrics": {"pe": 10, "pb": 1, "as_of": "2024-03-31"}},
                data_source="t1",
                store_dir=td,
            )
            save_snapshot_cache(
                "fundamentals",
                code,
                {"success": True, "metrics": {"pe": 11, "pb": 1.1, "as_of": "2024-06-30"}},
                data_source="t2",
                store_dir=td,
            )
            path = snapshot_cache_path("fundamentals", code, td)
            import json

            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            hist = payload.get("history") or []
            self.assertGreaterEqual(len(hist), 2)
            self.assertEqual(hist[-1]["as_of"], "2024-06-30")

    def test_merge_history_dedupe(self):
        h = merge_history_point(
            [],
            as_of="2024-01-01",
            metrics={"pe": 1},
            fetched_at="2024-01-02T00:00:00",
        )
        h2 = merge_history_point(
            h,
            as_of="2024-01-01",
            metrics={"pe": 2},
            fetched_at="2024-01-03T00:00:00",
        )
        self.assertEqual(len(h2), 1)
        self.assertEqual(h2[0]["metrics"]["pe"], 2)

    def test_pit_summary(self):
        rows = [
            {"fundamentals_pit": True, "ok": True, "mode": "as_of"},
            {"fundamentals_pit": True, "ok": False, "mode": "as_of_missing"},
        ]
        s = fundamentals_pit_summary(rows)
        self.assertEqual(s["resolved_ok"], 1)
        self.assertEqual(s["missing_as_of"], 1)
        self.assertTrue(s["fundamentals_pit_partial"])


class TestImpactCost(unittest.TestCase):
    def test_impact_positive_with_volume(self):
        from core.backtest.costs import estimate_impact_cost, get_cost_breakdown

        bps = estimate_impact_cost(1_000_000, 50_000_000)
        self.assertGreater(bps, 0)
        bars = [{"close": 10.0, "volume": 5_000_000}]
        bd = get_cost_breakdown(10.0, 10000, bars=bars)
        self.assertGreater(bd["impact_cost_bps"], 0)
        self.assertIn("impact_cost", bd)

    def test_impact_zero_without_volume(self):
        from core.backtest.costs import estimate_impact_cost

        self.assertEqual(estimate_impact_cost(1000, 0), 0.0)


class TestSourceAudit(unittest.TestCase):
    def test_audit_empty_codes(self):
        from core.data_consistency import audit_code_sources

        out = audit_code_sources([])
        self.assertEqual(out["status"], "empty")

    def test_attach_source_audit(self):
        from unittest.mock import patch

        from core.data_consistency import attach_source_audit

        with patch(
            "core.data_consistency.audit_code_sources",
            return_value={"ok": True, "status": "ok", "fallback_count": 0},
        ):
            out = attach_source_audit({"success": True}, codes=["600519"])
        self.assertIn("source_audit", out)
        self.assertEqual(out["source_audit"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
