"""可交易严筛：ST / 市值 / 成交额 / 研究池写入。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestTradableScreen(unittest.TestCase):
    def test_is_st_name_shared(self):
        from core.universe.st_name import is_st_name
        from core.backtest.universe_filters import _is_st_name

        self.assertTrue(is_st_name("*ST禾信"))
        self.assertTrue(is_st_name("ST百利"))
        self.assertTrue(_is_st_name("退市整理"))
        self.assertFalse(is_st_name("平安银行"))

    def test_screen_codes_basic_gates(self):
        from core.universe.tradable_screen import screen_codes

        policy = {
            "version": "t_test",
            "exclude_st": True,
            "exclude_index": True,
            "require_spot_row": True,
            "require_name": True,
            "min_float_mv": 1e9,
            "min_amount": 1e7,
            "prefer_bars_avg_amount": False,
            "min_listing_bars": 0,
            "min_price": 1.0,
            "max_price": 5000.0,
            "drop_suspended_keywords": True,
        }
        spot = {
            "000001": {
                "代码": "000001",
                "名称": "平安银行",
                "流通市值": 2e11,
                "成交额": 5e8,
                "最新价": 10.0,
            },
            "600519": {
                "代码": "600519",
                "名称": "ST茅台",
                "流通市值": 2e11,
                "成交额": 5e8,
                "最新价": 10.0,
            },
            "000002": {
                "代码": "000002",
                "名称": "万科A",
                "流通市值": 1e8,
                "成交额": 5e8,
                "最新价": 10.0,
            },
            "300001": {
                "代码": "300001",
                "名称": "特锐德",
                "流通市值": 2e11,
                "成交额": 1e6,
                "最新价": 10.0,
            },
        }
        out = screen_codes(
            ["000001", "600519", "000002", "300001", "399300", "999999"],
            policy=policy,
            spot_by_code=spot,
        )
        self.assertEqual(out["kept"], ["000001"])
        reasons = {r["code"]: r["reason"] for r in out["rejected"]}
        self.assertIn("600519", reasons)
        self.assertIn("ST", reasons["600519"])
        self.assertIn("000002", reasons)
        self.assertIn("300001", reasons)
        self.assertIn("399300", reasons)
        self.assertIn("999999", reasons)

    def test_save_research_applies_screen(self):
        from core.research_universe import load_research_universe, save_research_universe

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ru.json")
            with patch(
                "core.universe.tradable_screen.screen_codes",
                return_value={
                    "ok": True,
                    "kept": ["000001"],
                    "rejected": [{"code": "600519", "reason": "ST"}],
                    "stats": {"input": 2, "kept": 1, "rejected": 1, "by_reason": {}},
                    "policy_version": "tradable_v1",
                    "policy": {},
                },
            ):
                out = save_research_universe(
                    {"codes": ["000001", "600519"]},
                    path=path,
                    apply_screen=True,
                )
            self.assertTrue(out.get("ok"))
            self.assertEqual(out["universe"]["codes"], ["000001"])
            self.assertEqual(out["universe"]["policy_version"], "tradable_v1")
            uni = load_research_universe(path=path)
            self.assertEqual(uni["codes"], ["000001"])

    def test_watching_add_requires_research_subset(self):
        from core.watching.repository import add_watchlist_item, write_watching

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "watching.json")
            with patch(
                "core.watching.repository._research_codes_allowed",
                return_value={"000001", "000002"},
            ):
                write_watching(
                    {
                        "version": 1,
                        "name": "t",
                        "max_size": 300,
                        "sources": [],
                        "watchlist": ["000001"],
                        "watchlist_names": ["平安银行"],
                        "watchlist_origins": ["手动"],
                        "watchlist_added_at": [""],
                    },
                    path=path,
                )
            with patch(
                "core.watching.repository._research_codes_allowed",
                return_value={"000001"},
            ), patch(
                "core.watching.repository._resolve_entry",
                return_value=("000002", "万科A"),
            ), patch(
                "core.data.facade.get_quote",
                return_value={"success": False},
            ):
                with self.assertRaises(ValueError) as ctx:
                    add_watchlist_item("000002", path=path)
            self.assertIn("研究池", str(ctx.exception))


class TestUniverseFiltersAbsAmount(unittest.TestCase):
    def test_min_avg_amount_absolute(self):
        from core.backtest.universe_filters import filter_universe_bars

        bars = {
            "a": [
                {"date": "2026-01-01", "close": 10, "volume": 100, "amount": 1e8},
                {"date": "2026-01-02", "close": 10, "volume": 100, "amount": 1e8},
            ],
            "b": [
                {"date": "2026-01-01", "close": 10, "volume": 100, "amount": 1e5},
                {"date": "2026-01-02", "close": 10, "volume": 100, "amount": 1e5},
            ],
        }
        kept, dropped, meta = filter_universe_bars(
            bars, min_avg_amount=1e7, exclude_st=False
        )
        self.assertIn("a", kept)
        self.assertNotIn("b", kept)
        self.assertEqual(meta["dropped_count"], 1)


if __name__ == "__main__":
    unittest.main()
