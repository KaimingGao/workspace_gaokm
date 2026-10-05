"""市场上下文与 prior 单元测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from core.cross_market_prior import build_cross_market_prior
from core.market.context import build_market_priors
from core.market.context_store import save_market_snapshot, load_macro_snapshot
from core.market.sentiment_prior import build_market_sentiment_prior
from core.regulatory_prior import build_ipo_drain_prior, build_regulatory_prior
from core.signal.factors.tail_anomaly import score_tail_anomaly
from core.signal.regime import assess_regime
from adapters.announcement.engine import scan_regulatory_notices


class TestMarketContext(unittest.TestCase):
    def test_cross_market_prior_tech_drag(self):
        macro = {"overseas_tech_1d_pct": -3.5, "liquidity_stress_score": 0, "series": {}}
        prior = build_cross_market_prior(
            macro,
            config={"cross_market": {"mode": "gate"}},
            sector="半导体",
        )
        self.assertTrue(prior.get("active"))
        self.assertTrue(prior.get("tech_drag"))
        self.assertTrue(any("海外科技" in w for w in prior.get("warnings") or []))

    def test_market_sentiment_prior_ebb(self):
        snap = {
            "limit_up_open_premium_pct": -1.5,
            "broken_limit_rate": 0.3,
            "sentiment_cycle_score": 35,
        }
        prior = build_market_sentiment_prior(
            snap,
            config={"market_sentiment_prior": {"mode": "gate"}},
            rev_consecutive_limit=4,
        )
        self.assertTrue(prior.get("active"))
        self.assertTrue(prior.get("high_board_stock"))

    def test_regulatory_scan_hits(self):
        rows = [{"公告标题": "某某股份停牌核查公告", "代码": "600001"}]
        out = scan_regulatory_notices(rows)
        self.assertGreaterEqual(out.get("count"), 1)

    def test_regulatory_and_ipo_prior(self):
        ann = {
            "regulatory": {
                "active": True,
                "hits": [{"stock_code": "600001", "title": "停牌核查"}],
                "concept_tags": ["机器人"],
            },
            "ipo": {"extreme_ipo_day": True, "liquidity_drain_ratio_proxy": 4.0},
        }
        reg = build_regulatory_prior(
            ann,
            config={"regulatory_prior": {"mode": "gate"}},
            sector="机器人",
        )
        ipo = build_ipo_drain_prior(
            ann,
            config={"ipo_drain_prior": {"mode": "gate"}},
            sector="机器人",
        )
        self.assertTrue(reg.get("active"))
        self.assertTrue(ipo.get("active"))

    def test_tail_anomaly_factor(self):
        minute_bars = []
        for i in range(8):
            minute_bars.append(
                {
                    "datetime": f"2026-08-19 14:{30 + i * 5:02d}:00",
                    "date": "2026-08-19",
                    "open": 10.0 - i * 0.05,
                    "close": 10.0 - i * 0.08,
                    "volume": 1000 if i < 4 else 5000,
                }
            )
        score, meta = score_tail_anomaly([], minute_bars=minute_bars)
        self.assertLess(score, 50.0)
        self.assertIsNotNone(meta.get("tail_volume_ratio"))

    def test_regime_macro_overlay(self):
        bars = [{"date": f"2026-08-{i:02d}", "close": 100 + i} for i in range(1, 25)]
        macro = {"overseas_tech_1d_pct": -2.5, "liquidity_stress_score": 1, "series": {}}
        info = assess_regime(
            bars,
            {"enabled": True, "macro_overlay": {"enabled": True}},
            macro=macro,
        )
        self.assertTrue(info.get("macro_overlay", {}).get("applied"))

    def test_snapshot_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["INVESTMENT_STORE_DIR"] = tmp
            try:
                from core import paths

                paths.STORE_DIR = tmp
                payload = {"success": True, "as_of": "2026-08-19", "series": {"qqq": {}}}
                save_market_snapshot("macro", payload, data_source="test")
                loaded, meta = load_macro_snapshot(max_age_hours=1.0)
                self.assertTrue(meta.get("cache_hit"))
                self.assertEqual(loaded.get("as_of"), "2026-08-19")
            finally:
                os.environ.pop("INVESTMENT_STORE_DIR", None)

    def test_build_market_priors_bundle(self):
        ctx = {
            "macro": {"overseas_tech_1d_pct": -2.0, "liquidity_stress_score": 0, "series": {}},
            "market_sentiment": {"broken_limit_rate": 0.3, "limit_up_open_premium_pct": -1},
            "announcement": {
                "regulatory": {"active": True, "hits": [], "concept_tags": ["芯片"]},
                "ipo": {"extreme_ipo_day": False},
            },
        }
        priors = build_market_priors(ctx, sector="半导体")
        self.assertIn("cross_market_prior", priors)
        self.assertIn("market_sentiment_prior", priors)


if __name__ == "__main__":
    unittest.main()
