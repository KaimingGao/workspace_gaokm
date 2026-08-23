"""market context 第三轮补强：history / summarize / schedule。"""

from __future__ import annotations

import os
import tempfile
import unittest

from core.facts import facts_summary
from core.market.context import summarize_market_context
from skills.macro.history import build_macro_history_rows, save_macro_history_index


class TestMarketContextRound3(unittest.TestCase):
    def test_summarize_market_context(self):
        ctx = {
            "macro": {"overseas_tech_1d_pct": -2.0, "liquidity_stress_score": 1},
            "market_sentiment": {"broken_limit_rate": 0.3, "sentiment_cycle_score": 40},
            "announcement": {
                "regulatory": {"active": True, "count": 2, "penalty_codes": ["600001"]},
                "ipo": {"extreme_ipo_day": False},
            },
        }
        s = summarize_market_context(ctx, stock_code="600002", sector="半导体")
        self.assertIn("prior_flags", s)
        self.assertIn("macro", s)

    def test_macro_history_rows(self):
        macro = {
            "series": {
                "sox": {
                    "recent_bars": [
                        {"date": "2026-08-18", "close": 100.0},
                        {"date": "2026-08-19", "close": 98.0},
                    ]
                },
                "ndx": {
                    "recent_bars": [
                        {"date": "2026-08-18", "close": 200.0},
                        {"date": "2026-08-19", "close": 198.0},
                    ]
                },
            }
        }
        rows = build_macro_history_rows(macro)
        self.assertGreaterEqual(len(rows), 2)

    def test_macro_history_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["INVESTMENT_STORE_DIR"] = tmp
            try:
                from core import paths

                paths.STORE_DIR = tmp
                macro = {
                    "as_of": "2026-08-19",
                    "series": {
                        "sox": {
                            "recent_bars": [
                                {"date": "2026-08-19", "close": 98.0},
                            ]
                        }
                    },
                }
                path = save_macro_history_index(macro)
                self.assertTrue(os.path.isfile(path))
            finally:
                os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_facts_summary_market_context(self):
        facts = {
            "quote": {"success": True},
            "signal": {"success": True},
            "signal_item": {
                "market_prior_active": True,
                "market_prior_warnings": ["跨市场·海外科技隔夜 -2%"],
            },
            "kline": {},
            "peer": {},
            "index": {},
            "market_context": {"prior_active": True, "prior_warnings": ["a"]},
        }
        s = facts_summary(facts)
        self.assertIn("market_context", s)
        self.assertTrue(s["market_prior"]["active"])


if __name__ == "__main__":
    unittest.main()
