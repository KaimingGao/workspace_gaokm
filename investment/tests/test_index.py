import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills.index.engine import build_relative, _pct
from skills.index.handler import IndexHandler


def _bars(start=100.0, steps=None):
    steps = steps or [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    out = []
    for i, s in enumerate(steps):
        out.append(
            {
                "date": f"d{i}",
                "open": start + s,
                "high": start + s + 1,
                "low": start + s - 1,
                "close": start + s,
                "volume": 1000,
            }
        )
    return out


class TestIndex(unittest.TestCase):
    def test_pct(self):
        self.assertEqual(_pct(100, 110), 10.0)

    def test_relative_mocked(self):
        stock = _bars(100, list(range(0, 21)))  # +20
        index = _bars(100, [i * 0.5 for i in range(0, 21)])  # +10
        fake_quote = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "贵州茅台",
        }
        with patch(
            "skills.index.engine.query_quote", return_value=fake_quote
        ), patch(
            "skills.index.engine.resolve_market_code", return_value=("CN", "600519")
        ), patch(
            "skills.index.engine.bars_and_source", return_value=(stock, "mock")
        ), patch(
            "skills.index.engine.fetch_index_bars", return_value=(index, "沪深300")
        ):
            result = build_relative("茅台", days=20)

        self.assertTrue(result["success"])
        self.assertGreater(result["excess_return_pct"], 0)
        self.assertEqual(result["relative_stance"], "相对偏强")

    def test_handler_missing(self):
        out = IndexHandler().execute({"parameters": {}})
        self.assertIn("stock_code", out)


if __name__ == "__main__":
    unittest.main()
