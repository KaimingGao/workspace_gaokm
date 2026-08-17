import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.common.history import bars_from_quote_fallback, normalize_bars
from core.signal.scorer import rank_candidates, score_bars


def _rising_bars():
    # 温和上涨 + 放量
    base = 100.0
    bars = []
    for i in range(15):
        close = base + i * 0.4
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 50,
            }
        )
    return bars


def _overheated_bars():
    bars = []
    price = 100.0
    for i in range(10):
        price *= 1.06  # 暴涨
        bars.append(
            {
                "date": f"d{i}",
                "open": price * 0.99,
                "high": price * 1.02,
                "low": price * 0.98,
                "close": price,
                "volume": 2000,
            }
        )
    return bars


class TestSignalScorer(unittest.TestCase):
    def test_normalize_bars(self):
        rows = [{"日期": "2026-01-01", "收盘": 10, "开盘": 9.5, "最高": 10.2, "最低": 9.4, "成交量": 100}]
        bars = normalize_bars(rows)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 10.0)

    def test_score_rising(self):
        result = score_bars(_rising_bars(), horizon_days=3, quote={"change_raw": 1.0})
        self.assertFalse(result["hard_reject"])
        self.assertGreaterEqual(result["score"], 50)
        self.assertTrue(result["reasons"])
        self.assertTrue(result["invalidation"])

    def test_hard_reject_overheat(self):
        result = score_bars(_overheated_bars(), horizon_days=3)
        self.assertTrue(result["hard_reject"])

    def test_quote_fallback_bars(self):
        bars = bars_from_quote_fallback({"price_raw": 100, "change_raw": 2})
        self.assertEqual(len(bars), 2)
        scored = score_bars(bars, quote={"change_raw": 2})
        self.assertIn("score", scored)

    def test_rank(self):
        items = [
            {"score": 80, "hard_reject": False},
            {"score": 40, "hard_reject": False},
            {"score": 90, "hard_reject": True},
        ]
        ranked = rank_candidates(items, limit=5, min_score=50)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["score"], 80)


class TestSignalHandler(unittest.TestCase):
    def test_execute_with_codes_mocked(self):
        from unittest.mock import patch
        from skills.signal.handler import SignalHandler

        fake_quote = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "price": "1500元",
            "price_raw": 1500.0,
            "change": "+1.00%",
            "change_raw": 1.0,
        }

        with patch(
            "core.signal.score_stock.score_stock",
            return_value={
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "quote": fake_quote,
                "signal_item": {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "score": 72,
                    "hard_reject": False,
                    "reasons": ["mock"],
                    "invalidation": [],
                    "data_source": "mock",
                    "horizon_days": 3,
                },
            },
        ):
            out = SignalHandler().execute(
                {"parameters": {"stock_codes": ["茅台"], "horizon_days": 3, "limit": 5}}
            )
        import json

        data = json.loads(out)
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn("不保证收益", data["note"])


if __name__ == "__main__":
    unittest.main()
