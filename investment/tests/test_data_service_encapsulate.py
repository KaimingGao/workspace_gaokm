"""DS encapsulate：BarsResult / MarketPorts / facade dict 兼容。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


if __name__ == "__main__":
    unittest.main()
