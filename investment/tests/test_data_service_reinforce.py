"""DS-R0~R4 DataService / store / sector / ann_missing / quote_fallback 补强。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n=20, start=None):
    start = start or (datetime.now() - timedelta(days=n + 2))
    out = []
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "open": 10 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.8 + i * 0.1,
                "close": 10.2 + i * 0.1,
                "volume": 1000 + i,
            }
        )
    return out


class TestStoreLockMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_concurrent_merge_save_keeps_union(self):
        from core.store import load_daily_cache, merge_save_daily_cache, save_daily_cache

        save_daily_cache(
            "CN",
            "600519",
            _bars(5, datetime(2024, 1, 1)),
            data_source="seed",
            store_dir=self.tmp,
        )

        errors = []

        def w(offset):
            try:
                merge_save_daily_cache(
                    "CN",
                    "600519",
                    _bars(5, datetime(2024, 1, 1) + timedelta(days=offset)),
                    data_source="t",
                    store_dir=self.tmp,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=w, args=(i * 3,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        loaded = load_daily_cache(
            "CN", "600519", max_age_hours=0, ignore_age=True, store_dir=self.tmp
        )
        self.assertIsNotNone(loaded)
        dates = {b["date"] for b in loaded[0]}
        self.assertGreaterEqual(len(dates), 5)

    def test_assess_quality_rejects_pseudo(self):
        from core.store import assess_quality

        q = assess_quality(
            [{"date": "d-1", "close": 1}, {"date": "d0", "close": 2}],
            data_source="quote_fallback",
        )
        self.assertEqual(q["level"], "empty")
        self.assertTrue(q.get("pseudo_dates"))


class TestSectorBoardSplit(unittest.TestCase):
    def test_unmapped_is_not_board(self):
        from core.data.policy import UNMAPPED_SECTOR
        from core.portfolio_optimize import _board_for, _sector_for

        self.assertEqual(_sector_for("600519", {}), UNMAPPED_SECTOR)
        self.assertEqual(_board_for("600519"), "主板沪")
        self.assertEqual(_sector_for("300750", {"300750": "新能源"}), "新能源")


class TestRejectQuoteFallback(unittest.TestCase):
    def test_get_bars_reject(self):
        from unittest.mock import patch

        from core.data.facade import get_bars

        fake = (
            [
                {"date": "d-1", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
                {"date": "d0", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            ],
            "quote_fallback",
        )
        with patch("core.ports.market.fetch_daily_bars", return_value=fake):
            pack = get_bars("600519", reject_quote_fallback=True)
        self.assertEqual(pack["bars"], [])
        self.assertTrue(pack["fallback"])
        self.assertFalse(pack["production_ok"])


class TestAnnMissingPolicy(unittest.TestCase):
    def test_zero_weight_clears_metrics(self):
        from core.fundamentals_pit import resolve_fundamentals_for_score
        from unittest.mock import patch

        hist = [
            {
                "as_of": "2024-01-01",
                "available_as_of": "2024-01-01",
                "ann_missing": True,
                "metrics": {"pe": 10, "roe": 0.1},
            }
        ]
        with patch(
            "core.fundamentals_pit.load_fundamentals_panel",
            return_value={"history": hist},
        ):
            out = resolve_fundamentals_for_score(
                "600519",
                as_of="2024-06-01",
                fund_cfg={"ann_missing_policy": "zero_weight"},
                live_fallback=False,
            )
        self.assertFalse(out.get("ok"))
        self.assertIsNone(out.get("metrics"))
        self.assertTrue(out.get("ann_missing"))


class TestDataPolicyImports(unittest.TestCase):
    def test_constants(self):
        from core.data import policy as p
        from core.data.facade import FUNDAMENTALS_CACHE_HOURS, NEWS_CACHE_HOURS

        self.assertEqual(FUNDAMENTALS_CACHE_HOURS, p.FUNDAMENTALS_CACHE_HOURS)
        self.assertEqual(NEWS_CACHE_HOURS, p.NEWS_CACHE_HOURS)
        self.assertGreater(p.QFQ_LONG_GAP_DAYS, 0)


if __name__ == "__main__":
    unittest.main()


class TestBoardLabelScrub(unittest.TestCase):
    def test_board_in_map_counts_unmapped(self):
        from core.data.policy import UNMAPPED_SECTOR
        from core.portfolio_optimize import _sector_for, sector_map_coverage

        smap = {"600519": "白酒", "000001": "主板沪", "300750": "新能源"}
        self.assertEqual(_sector_for("000001", smap), UNMAPPED_SECTOR)
        self.assertEqual(_sector_for("600519", smap), "白酒")
        cov = sector_map_coverage(["600519", "000001", "300750"], sector_map=smap)
        self.assertEqual(cov["mapped"], 2)
        self.assertEqual(cov["board_labeled"], 1)

    def test_scrub_removes_boards(self):
        import tempfile
        from pathlib import Path

        from core.sector_map_sync import scrub_board_labels_from_sector_map, save_sector_map
        from core.portfolio_optimize import load_sector_map

        td = tempfile.mkdtemp()
        path = str(Path(td) / "sector_map.json")
        save_sector_map({"600519": "白酒", "000725": "主板深"}, path=path)
        out = scrub_board_labels_from_sector_map(write=True, path=path)
        self.assertEqual(out["removed_count"], 1)
        # load_sector_map reads DATA_DIR; verify via file
        import json
        with open(path, encoding="utf-8") as f:
            left = json.load(f)
        self.assertEqual(left, {"600519": "白酒"})


class TestSpotIndustryEnrich(unittest.TestCase):
    def test_normalize_industry(self):
        from core.sector_map_sync import normalize_industry_label

        self.assertEqual(normalize_industry_label("白酒"), "白酒")
        self.assertEqual(normalize_industry_label("软件开发"), "软件开发")
        self.assertIsNone(normalize_industry_label("主板沪"))
        self.assertIsNone(normalize_industry_label(""))

    def test_enrich_from_mock_spot(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from core.sector_map_sync import enrich_sector_map_from_spot, save_sector_map

        td = tempfile.mkdtemp()
        path = str(Path(td) / "sector_map.json")
        save_sector_map({"600519": "白酒"}, path=path)

        rows = [
            {"代码": "600519", "所属行业": "白酒"},
            {"代码": "300750", "所属行业": "电池"},
            {"代码": "000001", "所属行业": "银行"},
        ]

        def fake_spot_get(row, field):
            aliases = {
                "code": ("代码",),
                "industry": ("所属行业",),
            }
            for k in aliases.get(field, (field,)):
                if k in row:
                    return row[k]
            return None

        with patch(
            "core.ports.market.load_disk_spot", return_value=rows
        ), patch(
            "core.ports.market.spot_row_get", side_effect=fake_spot_get
        ), patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["600519", "300750", "000001"]},
        ):
            out = enrich_sector_map_from_spot(
                codes=["600519", "300750", "000001"],
                write=True,
                path=path,
                overwrite=False,
                scrub_boards=False,
            )
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["added_count"], 2)
        with open(path, encoding="utf-8") as f:
            left = json.load(f)
        self.assertEqual(left["600519"], "白酒")
        self.assertEqual(left["300750"], "电池")
        self.assertEqual(left["000001"], "银行")


class TestSectorCoverageRiskGate(unittest.TestCase):
    def test_require_sector_map_blocks(self):
        from core.risk.checks import check_account_risk

        paper = {"cash": 0, "strategy_id": "short_conservative", "holdings": []}
        summary = {
            "equity": 100000,
            "max_drawdown_pct": 1.0,
            "holdings": [
                {
                    "stock_code": "999991",
                    "shares": 100,
                    "market_value": 50000,
                },
                {
                    "stock_code": "999992",
                    "shares": 100,
                    "market_value": 50000,
                },
            ],
        }
        risk = {
            "max_drawdown_pct": 50,
            "max_position_pct": 60,
            "max_sector_pct": 80,
            "max_positions": 10,
            "require_sector_map": True,
            "min_sector_map_coverage_block": 0.5,
        }
        out = check_account_risk(paper, summary, risk=risk)
        self.assertFalse(out["ok"])
        self.assertIn("sector_map_thin", out["block_codes"])
