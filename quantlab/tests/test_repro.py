import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.repro import run_repro_case, stable_fingerprint
from core.paper import init_from_example, load_paper, mark_to_market, save_paper


class TestReproEvals(unittest.TestCase):
    def test_stable_fingerprint_order_independent(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        self.assertEqual(stable_fingerprint(a), stable_fingerprint(b))

    def test_score_fixture_repro(self):
        case = {
            "id": "t",
            "engine": "score_bars",
            "params": {"horizon_days": 3},
            "quote": {"change_raw": 1.0, "price_raw": 105},
            "bars": [
                {"date": "d1", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1},
                {"date": "d2", "open": 101, "high": 102, "low": 100, "close": 101.5, "volume": 1},
                {"date": "d3", "open": 102, "high": 103, "low": 101, "close": 102.5, "volume": 1},
                {"date": "d4", "open": 103, "high": 104, "low": 102, "close": 103.5, "volume": 1},
                {"date": "d5", "open": 104, "high": 105, "low": 103, "close": 104.5, "volume": 1},
                {"date": "d6", "open": 105, "high": 106, "low": 104, "close": 105.5, "volume": 1},
            ],
        }
        r = run_repro_case(case)
        self.assertTrue(r["ok"])

    def test_fixtures_file_all_pass(self):
        path = os.path.join(ROOT, "evals", "repro_fixtures.json")
        with open(path, "r", encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        for case in cases:
            r = run_repro_case(case)
            self.assertTrue(r.get("ok"), f"{case.get('id')}: {r}")


class TestPaperAccount(unittest.TestCase):
    def test_init_and_mark(self):
        from unittest.mock import patch

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "paper.json")
        out = init_from_example(path)
        self.assertTrue(os.path.isfile(out))
        paper = load_paper(path)
        paper["holdings"] = [
            {"stock_code": "600519", "stock_name": "茅台", "shares": 100, "cost": 1400}
        ]
        with patch(
            "adapters.market.quote_api.StockAPI.query",
            return_value={
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "price_raw": 1500.0,
            },
        ):
            summary = mark_to_market(paper)
        self.assertGreater(summary["equity"], summary["cash"])
        save_paper(paper, path)


if __name__ == "__main__":
    unittest.main()
