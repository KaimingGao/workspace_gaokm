"""DataService 综合单测。

合并自原 test_data_service_encapsulate.py（BarsResult / MarketPorts / facade dict 兼容）
        与 test_data_service_reinforce.py（DS-R0~R4 store / sector / ann_missing / quote_fallback 补强）。
"""

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


# ---------- helpers (来自 encapsulate) ----------


def _fresh_bars(n: int = 80):
    start = datetime.now() - timedelta(days=n + 5)
    out = []
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "open": 10.0 + i * 0.01,
                "high": 10.2 + i * 0.01,
                "low": 9.9 + i * 0.01,
                "close": 10.1 + i * 0.01,
                "volume": 1000 + i,
            }
        )
    return out


class _FakeBars:
    def __init__(self, bars=None, source="akshare_cn_daily:qfq"):
        self.bars = bars if bars is not None else _fresh_bars()
        self.source = source

    def fetch_daily(self, code, **kw):
        return (list(self.bars), self.source)


def _ports_with_bars(bars_port):
    from core.data.ports import MarketPorts, default_ports

    base = default_ports()
    return MarketPorts(
        quote=base.quote,
        bars=bars_port,
        index=base.index,
        minute=base.minute,
        fundamentals=base.fundamentals,
        news=base.news,
        spot=base.spot,
        snapshots=base.snapshots,
    )


# ---------- helpers (来自 reinforce) ----------


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


# ============================================================
# 原 test_data_service_encapsulate.py
# ============================================================


class TestTypedBarsResult(unittest.TestCase):
    def test_service_returns_bars_result_and_as_dict(self):
        from core.data import BarsResult, MarketDataService

        svc = MarketDataService(_ports_with_bars(_FakeBars()))
        r = svc.get_bars("600519", limit=60)
        self.assertIsInstance(r, BarsResult)
        self.assertTrue(r.production_ok)
        self.assertEqual(r.quality.level, "good")
        self.assertFalse(r.fallback)
        d = r.as_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["bar_count"], len(r.bars))
        self.assertIn("quality", d)
        self.assertTrue(d["production_ok"])

    def test_research_rejects_quote_fallback(self):
        from core.data import ResearchDataService

        fake = _FakeBars(
            bars=[{"date": "d0", "close": 1.0}, {"date": "d-1", "close": 1.1}],
            source="quote_fallback",
        )
        svc = ResearchDataService(_ports_with_bars(fake))
        r = svc.get_bars("600519", limit=10)
        self.assertEqual(r.data_source, "rejected_quote_fallback")
        self.assertEqual(r.bars, [])
        self.assertFalse(r.production_ok)
        self.assertTrue(r.pit.rejected_quote_fallback)


class TestFacadeCompat(unittest.TestCase):
    def tearDown(self):
        from core.data.service import set_default_service

        set_default_service(None)

    def test_facade_get_bars_returns_dict(self):
        from core.data import MarketDataService
        from core.data.service import set_default_service
        from core.data.facade import get_bars

        set_default_service(MarketDataService(_ports_with_bars(_FakeBars())))
        pack = get_bars("600519", limit=60)
        self.assertIsInstance(pack, dict)
        self.assertTrue(pack.get("production_ok"))
        self.assertIsInstance(pack.get("bars"), list)
        self.assertGreater(pack.get("bar_count", 0), 0)

    def test_gate_reexports(self):
        from core.data.facade import (
            DEFAULT_ADJUST_POLICY,
            allows_production_score,
            infer_adjust,
            normalize_adjust_policy,
        )

        ok, reason = allows_production_score(quality_level="good", fallback=False)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(normalize_adjust_policy(None), DEFAULT_ADJUST_POLICY)
        self.assertEqual(infer_adjust("akshare_cn_daily:qfq"), DEFAULT_ADJUST_POLICY)


class TestDataEnvelope(unittest.TestCase):
    def test_quote_envelope_flattens(self):
        from core.data import MarketDataService
        from core.data.ports import MarketPorts, default_ports

        class FakeQuote:
            def query(self, code):
                return {"success": True, "price": 100.0, "name": "x"}

            def batch_query(self, codes):
                return {}

        base = default_ports()
        ports = MarketPorts(
            quote=FakeQuote(),
            bars=base.bars,
            index=base.index,
            minute=base.minute,
            fundamentals=base.fundamentals,
            news=base.news,
            spot=base.spot,
            snapshots=base.snapshots,
        )
        env = MarketDataService(ports).get_quote("600519")
        d = env.as_dict()
        self.assertTrue(d.get("success"))
        self.assertEqual(d.get("price"), 100.0)
        self.assertEqual(d.get("data_source"), "tencent_quote")
        self.assertTrue(d.get("production_ok"))

    def test_spot_empty_not_ok(self):
        from core.data import MarketDataService
        from core.data.ports import MarketPorts, default_ports

        class FakeSpot:
            last_source = "mem_cache"

            def fetch(self, *, force=False):
                return []

            def load_disk(self, *, max_age_hours=0):
                return None

        base = default_ports()
        ports = MarketPorts(
            quote=base.quote,
            bars=base.bars,
            index=base.index,
            minute=base.minute,
            fundamentals=base.fundamentals,
            news=base.news,
            spot=FakeSpot(),
            snapshots=base.snapshots,
        )
        env = MarketDataService(ports).get_spot()
        self.assertFalse(env.ok)
        self.assertFalse(env.production_ok)
        self.assertEqual(env.data_source, "mem_cache")


class TestResearchInjectAndBatch(unittest.TestCase):
    def tearDown(self):
        from core.data.service import set_default_service, set_research_service

        set_default_service(None)
        set_research_service(None)

    def test_set_research_service(self):
        from core.data import ResearchDataService, get_research_service, set_research_service

        fake = ResearchDataService(_ports_with_bars(_FakeBars(source="quote_fallback")))
        set_research_service(fake)
        self.assertIs(get_research_service(), fake)
        r = get_research_service().get_bars("600519")
        self.assertEqual(r.data_source, "rejected_quote_fallback")

    def test_facade_get_bars_batch(self):
        from core.data import MarketDataService
        from core.data.service import set_default_service
        from core.data.facade import get_bars_batch
        from unittest.mock import patch

        set_default_service(MarketDataService(_ports_with_bars(_FakeBars())))
        with patch("core.ports.market.batch_map", side_effect=lambda fn, items, **kw: [fn(c, **kw) for c in items]):
            packs = get_bars_batch(["600519", "000858"], limit=60)
        self.assertEqual(len(packs), 2)
        self.assertTrue(all(isinstance(p, dict) for p in packs))
        self.assertTrue(packs[0].get("production_ok"))


class TestSectorCoverageEmpty(unittest.TestCase):
    def test_empty_universe_coverage_none(self):
        from core.portfolio_optimize import sector_map_coverage

        cov = sector_map_coverage([], sector_map={"600519": "白酒"})
        self.assertIsNone(cov["coverage"])
        self.assertTrue(cov.get("empty_universe"))


class TestScoreStockViaDs(unittest.TestCase):
    def test_fetch_daily_bars_offline_then_pool(self):
        from unittest.mock import patch
        from core.signal.score_stock import _fetch_bars_isolated

        fresh = _fresh_bars(40)
        offline = {
            "bars": fresh,
            "data_source": "cache:akshare",
            "production_ok": True,
        }
        with patch("core.data.facade.get_bars", return_value=offline) as m_get:
            bars, src = _fetch_bars_isolated("600519", limit=40)
        m_get.assert_called()
        self.assertEqual(len(bars), 40)
        self.assertIn("cache", src)

    def test_fetch_daily_bars_pool_on_miss(self):
        from unittest.mock import patch
        from core.signal.score_stock import _fetch_bars_isolated

        empty = {"bars": [], "data_source": "empty", "production_ok": False}
        pool_pack = {
            "bars": _fresh_bars(40),
            "data_source": "akshare_cn_daily:qfq",
            "production_ok": True,
        }
        with patch("core.data.facade.get_bars", return_value=empty), patch(
            "core.ports.market.batch_map", return_value=[pool_pack]
        ) as m_batch:
            bars, src = _fetch_bars_isolated("600519", limit=40, timeout=5.0)
        m_batch.assert_called()
        self.assertEqual(len(bars), 40)
        self.assertIn("akshare", src)

    def test_fetch_daily_bars_offline_only_skips_pool(self):
        from unittest.mock import patch
        from core.signal.score_stock import _fetch_bars_isolated

        empty = {"bars": [], "data_source": "empty", "production_ok": False}
        with patch("core.data.facade.get_bars", return_value=empty), patch(
            "core.ports.market.batch_map"
        ) as m_batch:
            bars, src = _fetch_bars_isolated(
                "600519", limit=40, timeout=5.0, offline_only=True
            )
        m_batch.assert_not_called()
        self.assertEqual(bars, [])
        self.assertEqual(src, "empty")


class TestMetrics(unittest.TestCase):
    def tearDown(self):
        from core.data.service import reset_metrics, set_default_service

        set_default_service(None)
        reset_metrics()

    def test_get_bars_bumps_metrics(self):
        from core.data import MarketDataService
        from core.data.service import metrics_snapshot, reset_metrics, set_default_service

        reset_metrics()
        set_default_service(MarketDataService(_ports_with_bars(_FakeBars())))
        from core.data.facade import get_bars

        get_bars("600519", limit=60)
        snap = metrics_snapshot()
        self.assertGreaterEqual(snap.get("bars_ok", 0), 1)


class TestPortfolioResolveOffline(unittest.TestCase):
    def test_offline_resolve_skips_quote(self):
        from unittest.mock import patch
        from core.research.portfolio_bars import _resolve_symbol

        with patch("core.data.facade.get_quote") as m_q:
            sym = _resolve_symbol("茅台", allow_live=False)
        m_q.assert_not_called()
        self.assertTrue(sym)

    def test_six_digit_no_network(self):
        from unittest.mock import patch
        from core.research.portfolio_bars import _resolve_symbol

        with patch("core.data.facade.get_quote") as m_q:
            self.assertEqual(_resolve_symbol("600519", allow_live=True), "600519")
        m_q.assert_not_called()


class TestLazyDataPackage(unittest.TestCase):
    def test_lazy_getattr_bars_result(self):
        import core.data as data_pkg

        # 强制走 __getattr__：清掉可能已缓存的符号
        data_pkg.__dict__.pop("BarsResult", None)
        br = data_pkg.BarsResult
        self.assertTrue(callable(br) or isinstance(br, type))


class TestIndexBars(unittest.TestCase):
    def test_get_index_bars_envelope(self):
        from unittest.mock import patch
        from core.data import MarketDataService

        fake_bars = _fresh_bars(30)
        with patch(
            "core.ports.market.fetch_index_bars",
            return_value=(fake_bars, "沪深300"),
        ):
            r = MarketDataService().get_index_bars("sh000300", limit=30)
        self.assertEqual(r.kind, "index_bars")
        self.assertEqual(r.bar_count, 30)
        self.assertTrue(r.ok)
        d = r.as_dict()
        self.assertEqual(d.get("kind"), "index_bars")
        self.assertTrue(d.get("ok"))


class TestBatchRespectsInjectedPorts(unittest.TestCase):
    def tearDown(self):
        from core.data.service import set_default_service, set_research_service

        set_default_service(None)
        set_research_service(None)

    def test_instance_batch_uses_injected_bars_not_global(self):
        from core.data import MarketDataService

        svc = MarketDataService(_ports_with_bars(_FakeBars(source="akshare_cn_daily:qfq")))
        # 未 set_default_service：须走本地线程路径，仍能拿到注入 bars
        packs = svc.get_bars_batch(["600519", "000858"], limit=40)
        self.assertEqual(len(packs), 2)
        self.assertTrue(all(p.get("production_ok") for p in packs))
        self.assertGreater(packs[0].get("bar_count", 0), 0)

    def test_research_instance_batch_rejects_fallback(self):
        from core.data import ResearchDataService

        fake = _FakeBars(
            bars=[{"date": "d0", "close": 1.0}, {"date": "d-1", "close": 1.1}],
            source="quote_fallback",
        )
        svc = ResearchDataService(_ports_with_bars(fake))
        packs = svc.get_bars_batch(["600519"], limit=10)
        self.assertEqual(packs[0].get("data_source"), "rejected_quote_fallback")
        self.assertEqual(packs[0].get("bars"), [])


class TestMinuteEmptyOk(unittest.TestCase):
    def test_empty_minute_not_ok(self):
        from core.data import MarketDataService
        from core.data.ports import MarketPorts, default_ports

        class FakeMinute:
            def fetch_minute(self, code, **kw):
                return [], {"data_source": "minute"}

        base = default_ports()
        ports = MarketPorts(
            quote=base.quote,
            bars=base.bars,
            index=base.index,
            minute=FakeMinute(),
            fundamentals=base.fundamentals,
            news=base.news,
            spot=base.spot,
            snapshots=base.snapshots,
        )
        env = MarketDataService(ports).get_minute_bars("600519")
        self.assertFalse(env.ok)
        self.assertEqual(env.gate_reason, "minute_empty")
        self.assertFalse(env.as_dict().get("success"))


# ============================================================
# 原 test_data_service_reinforce.py
# ============================================================


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


if __name__ == "__main__":
    unittest.main()
