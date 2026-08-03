"""观察页 insights 摘要结构。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestWatchingInsights(unittest.TestCase):
    def test_insight_fields(self):
        from core.watching_insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "change_raw": 1.2,
                "volume": "1.2万",
            },
            "signal_item": {
                "score": 62.0,
                "hard_reject": False,
                "factors": {
                    "volume_ratio": 1.35,
                    "value_pe": 20.5,
                    "value_pb": 8.5,
                    "excess_return_pct": 3.2,
                },
                "sub_scores": {"relative_strength": 70.0},
            },
        }

        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ):
            out = build_watching_insights(
                ["600519"],
                added_at_by_code={"600519": "2026-07-20T10:00:00"},
            )

        self.assertTrue(out["ok"])
        item = out["items"][0]
        self.assertEqual(item["score"], 62.0)
        self.assertIn(item["stance_short"], {"轻仓", "关注", "观望", "—"})
        self.assertEqual(item["excess_return_pct"], 3.2)
        self.assertEqual(item["volume_ratio"], 1.35)
        self.assertEqual(item["pe"], 20.5)
        self.assertEqual(item["pb"], 8.5)
        self.assertIsNotNone(item["days_watched"])
        self.assertFalse(item["hard_reject"])

    def test_rs_fallback_when_no_excess(self):
        from core.watching_insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {"success": True, "stock_code": "600519", "change_raw": 0.5},
            "signal_item": {
                "score": 55.0,
                "hard_reject": False,
                "factors": {"volume_ratio": 1.0},
                "sub_scores": {"relative_strength": 66.0},
            },
        }
        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertIsNone(item["excess_return_pct"])
        self.assertEqual(item["excess_label"], "RS66")

    def test_insights_covers_full_watchlist_up_to_80(self):
        from core.watching_insights import build_watching_insights

        codes = [f"{i:06d}" for i in range(1, 41)]

        def fake_score(code, **kwargs):
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code},
                "signal_item": {"score": 50.0 + (int(code) % 10), "hard_reject": False, "factors": {}},
            }

        with patch("core.signal.score_stock.score_stock", side_effect=fake_score), patch(
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ):
            out = build_watching_insights(codes)
        self.assertEqual(out["count"], 40)
        self.assertEqual(out.get("truncated"), 0)
        self.assertTrue(all(i.get("score") is not None for i in out["items"]))


if __name__ == "__main__":
    unittest.main()
