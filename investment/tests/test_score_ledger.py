"""昨日复盘 · score ledger / outcomes / review。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestScoreLedger(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.ledger_root = os.path.join(self._tmpdir.name, "score_ledger")
        os.makedirs(self.ledger_root, exist_ok=True)

    def _patch_dir(self):
        return patch("core.score_ledger.ledger_dir", return_value=self.ledger_root)

    def test_upsert_and_load(self):
        from core.score_ledger import load_ledger, upsert_ledger_rows

        with self._patch_dir():
            out = upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "predicted_score": 1.2,
                        "cluster_label": "G1",
                        "score_formula_terms": {
                            "terms": [
                                {"key": "momentum", "contrib": 0.8, "z": 1.0, "beta": 0.8},
                                {"key": "value", "contrib": -0.2, "z": -0.5, "beta": 0.4},
                            ]
                        },
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": -0.5,
                        "score_formula_terms": {
                            "terms": [
                                {"key": "momentum", "contrib": -0.4, "z": -1.0, "beta": 0.4},
                            ]
                        },
                    },
                ],
                source="test",
            )
            self.assertTrue(out["success"])
            self.assertEqual(out["n_rows"], 2)
            loaded = load_ledger("2026-08-05")
            self.assertFalse(loaded["empty"])
            codes = {r["code"] for r in loaded["rows"]}
            self.assertEqual(codes, {"600519", "000001"})
            top = next(r for r in loaded["rows"] if r["code"] == "600519")
            self.assertEqual(top["formula_terms_top"][0]["key"], "momentum")

    def test_fill_outcomes_and_review(self):
        from core.score_ledger import (
            build_score_review,
            fill_outcomes,
            upsert_ledger_rows,
        )

        def fake_bars(code, limit=40, offline_ok=True):
            # as_of 08-05 → next 1d = 08-06 (assume weekday calendar lite)
            bars = [
                {"date": "2026-08-04", "close": 100.0},
                {"date": "2026-08-05", "close": 100.0},
                {"date": "2026-08-06", "close": 102.0},  # +2%
                {"date": "2026-08-07", "close": 103.0},
                {"date": "2026-08-08", "close": 104.0},
            ]
            if str(code).endswith("001"):
                # wrong direction for negative yhat if we use +2%
                bars = [
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 100.0},
                    {"date": "2026-08-06", "close": 98.0},  # -2%
                    {"date": "2026-08-07", "close": 97.0},
                    {"date": "2026-08-08", "close": 96.0},
                ]
            return bars, "test"

        with self._patch_dir(), patch(
            "core.data_service.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market_calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: {
                1: "2026-08-06",
                3: "2026-08-08",
            }.get(int(n), "2026-08-06"),
        ), patch(
            "core.market_calendar.is_trading_day", return_value=True
        ):
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.5,
                        "score_formula_terms": {
                            "terms": [{"key": "momentum", "contrib": 1.0, "z": 1.0, "beta": 1.0}]
                        },
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": 1.0,  # predicts up, but -2% → wrong
                        "score_formula_terms": {
                            "terms": [{"key": "momentum", "contrib": 0.9, "z": 0.9, "beta": 1.0}]
                        },
                    },
                ],
                source="test",
            )
            filled = fill_outcomes("2026-08-05", horizon_days=1)
            self.assertTrue(filled["success"])
            self.assertEqual(filled["filled"], 2)
            rev = build_score_review("2026-08-05", horizon_days=1, autofill=False)
            self.assertTrue(rev["success"])
            self.assertFalse(rev["empty"])
            self.assertEqual(rev["summary"]["n_scored"], 2)
            self.assertEqual(rev["summary"]["hits"], 1)
            self.assertEqual(rev["summary"]["wrong"], 1)
            wrong_codes = {r["code"] for r in rev["wrong_rows"]}
            self.assertIn("000001", wrong_codes)

    def test_row_from_book_shape(self):
        from core.score_ledger import row_from_scored_item

        row = row_from_scored_item(
            {"stock_code": "300750", "score": 0.8, "predicted_score": 0.8},
            as_of="2026-08-05",
            source="book",
        )
        self.assertEqual(row["code"], "300750")
        self.assertAlmostEqual(row["yhat"], 0.8)


if __name__ == "__main__":
    unittest.main()
