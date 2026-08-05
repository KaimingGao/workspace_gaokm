"""FH4：评分路径失败可见（非裸 pass）。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.return_score import ReturnScoreModel
from core.signal.score_stock import score_stock


class TestFh4ScoreWarnings(unittest.TestCase):
    def test_sentiment_failure_surfaces_warning(self):
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": 10.0,
            "change": 0.0,
        }
        bars = [{"date": "2024-01-01", "close": 10.0}] * 30
        global_m = ReturnScoreModel(
            intercept=1.0,
            coefficients={"momentum": 0.0},
            standardized=False,
        )
        scored = {
            "score": 50.0,
            "hard_reject": False,
            "sub_scores": {"momentum": 0.0},
            "factors": {},
            "reasons": [],
            "factor_contrib": {},
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars", return_value=(bars, "akshare")
        ), patch(
            "core.signal.score_stock.allows_production_score", return_value=(True, "")
        ), patch(
            "core.signal.score_stock.score_bars", return_value=scored
        ), patch(
            "core.sentiment.fetch_stock_headlines",
            side_effect=TimeoutError("舆情超时"),
        ), patch(
            "core.signal.return_score_store.load_return_model",
            return_value=(global_m, {}),
        ), patch(
            "core.signal.score_stock.load_signal_config",
            return_value={
                "fundamentals": {"enabled": False},
                "cluster_scoring": {"enabled": False, "mode": "off"},
            },
        ):
            out = score_stock(
                "600000",
                quote=quote,
                skip_fundamentals=True,
                bypass_quality_gate=True,
                cluster_mode="off",
            )
        warns = (out.get("signal_item") or {}).get("warnings") or []
        self.assertTrue(any("sentiment" in str(w) for w in warns))


if __name__ == "__main__":
    unittest.main()
