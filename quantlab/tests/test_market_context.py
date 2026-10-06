"""市场上下文与 prior 综合单元测试。

合并自原 test_market_context.py（cross_market / sentiment / regulatory / tail_anomaly / regime / snapshot / priors bundle）
        + test_market_context_enhancements.py（概念图谱 / prior_policy / regime defer）
        + test_market_context_merge.py（context_merge / ipo_metrics）
        + test_market_context_round2.py（concept cache / macro asof / minute prefetch）
        + test_market_context_round3.py（summarize / macro history / facts_summary）。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from adapters.announcement.concept_graph import enrich_regulatory_with_concepts
from adapters.announcement.engine import scan_regulatory_notices
from adapters.announcement.ipo_metrics import compute_drain_ratios
from adapters.macro.history import build_macro_history_rows, save_macro_history_index
from core.concept_graph_store import (
    load_concept_graph_cache,
    merge_concept_into_index,
    save_concept_graph_cache,
    stock_in_penalty_concepts,
)
from core.cross_market_prior import build_cross_market_prior
from core.facts import facts_summary
from core.market.context import build_market_priors, summarize_market_context
from core.market.context_merge import freshness_report, merge_macro_snapshots
from core.market.context_store import load_macro_snapshot, save_market_snapshot
from core.market.prior_policy import apply_market_priors_to_buy, apply_market_priors_to_hold
from core.market.sentiment_prior import build_market_sentiment_prior
from core.regulatory_prior import build_ipo_drain_prior, build_regulatory_prior
from core.research.macro_asof import macro_view_asof, pct_change_from_bars
from core.signal.factors.tail_anomaly import score_tail_anomaly
from core.signal.minute_prefetch import prefetch_minute_bars
from core.signal.regime import assess_regime


# ============================================================
# 原 test_market_context.py
# ============================================================


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
            os.environ["QUANTLAB_STORE_DIR"] = tmp
            try:
                from core import paths

                paths.STORE_DIR = tmp
                payload = {"success": True, "as_of": "2026-08-19", "series": {"qqq": {}}}
                save_market_snapshot("macro", payload, data_source="test")
                loaded, meta = load_macro_snapshot(max_age_hours=1.0)
                self.assertTrue(meta.get("cache_hit"))
                self.assertEqual(loaded.get("as_of"), "2026-08-19")
            finally:
                os.environ.pop("QUANTLAB_STORE_DIR", None)

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


# ============================================================
# 原 test_market_context_enhancements.py
# ============================================================


class TestMarketContextEnhancements(unittest.TestCase):
    def test_concept_graph_membership(self):
        reg = enrich_regulatory_with_concepts(
            {
                "active": True,
                "count": 1,
                "penalty_codes": ["600001"],
                "concept_tags": ["机器人"],
                "hits": [{"stock_code": "600001", "title": "停牌核查"}],
            },
            concept_hints=["机器人"],
            max_concepts=0,
        )
        reg["code_concepts"] = {
            "600001": ["机器人"],
            "600002": ["机器人", "半导体"],
        }
        reg["penalty_concept_graph"] = {"600001": ["机器人"]}
        reg["concept_graph_built"] = True
        self.assertTrue(stock_in_penalty_concepts("600002", reg, fallback_tags=["机器人"]))

    def test_regulatory_prior_graph_hit(self):
        ann = {
            "regulatory": {
                "active": True,
                "hits": [{"stock_code": "600001", "title": "停牌核查"}],
                "concept_tags": ["机器人"],
                "penalty_codes": ["600001"],
                "code_concepts": {"600002": ["机器人"]},
                "penalty_concept_graph": {"600001": ["机器人"]},
                "concept_graph_built": True,
            }
        }
        prior = build_regulatory_prior(
            ann,
            config={"regulatory_prior": {"mode": "gate"}},
            stock_code="600002",
        )
        self.assertTrue(prior.get("active"))

    def test_ipo_drain_concept_hit(self):
        ann = {
            "concept_index": {"600519": ["机器人"]},
            "ipo": {
                "extreme_ipo_day": True,
                "liquidity_drain_ratio_proxy": 4.0,
                "drain_ratios_by_concept": {"机器人": 4.5},
            },
        }
        prior = build_ipo_drain_prior(
            ann,
            config={"ipo_drain_prior": {"mode": "gate"}},
            stock_code="600519",
        )
        self.assertTrue(prior.get("active"))

    def test_market_prior_min_scale_merge(self):
        item = {
            "cross_market_prior": {
                "active": True,
                "warnings": ["a"],
                "actions": [{"type": "scale_buy", "scale": 0.5}],
            },
            "market_sentiment_prior": {
                "active": True,
                "warnings": ["b"],
                "actions": [{"type": "scale_buy", "scale": 0.55}],
            },
        }
        out = apply_market_priors_to_buy(
            item,
            position_ratio=1.0,
            config={"market_prior_policy": {"merge_mode": "min_scale"}},
        )
        self.assertAlmostEqual(out["ratio"], 0.5)

    def test_market_prior_hold_min_scale(self):
        item = {
            "cross_market_prior": {
                "active": True,
                "actions": [{"type": "scale_hold", "scale": 0.5}],
            },
            "regulatory_prior": {
                "active": True,
                "actions": [{"type": "scale_hold", "scale": 0.4}],
            },
        }
        out = apply_market_priors_to_hold(
            item,
            shares=1000,
            config={"market_prior_policy": {"merge_mode": "min_scale"}},
        )
        self.assertTrue(out.get("trim"))
        self.assertEqual(out.get("keep_shares"), 400.0)

    def test_regime_defer_macro_to_cross_market(self):
        bars = [{"date": f"2026-08-{i:02d}", "close": 100 + i} for i in range(1, 25)]
        macro = {"overseas_tech_1d_pct": -2.5, "liquidity_stress_score": 0, "series": {}}
        info = assess_regime(
            bars,
            {
                "enabled": True,
                "macro_overlay": {
                    "enabled": True,
                    "defer_tech_drag_to_cross_market_prior": True,
                },
            },
            macro=macro,
        )
        overlay = info.get("macro_overlay") or {}
        self.assertTrue(overlay.get("deferred_to_cross_market_prior"))
        self.assertFalse(overlay.get("applied"))


# ============================================================
# 原 test_market_context_merge.py
# ============================================================


class TestMarketContextMerge(unittest.TestCase):
    def test_merge_macro_keeps_prior_series(self):
        prior = {
            "series": {"qqq": {"close": 400, "change_1d_pct": -1.0}},
            "overseas_tech_1d_pct": -1.0,
        }
        fresh = {"series": {}, "errors": ["sox:empty"], "liquidity_stress_score": 1.0}
        merged = merge_macro_snapshots(fresh, prior)
        self.assertEqual(merged["series"]["qqq"]["close"], 400)
        self.assertTrue(merged.get("merged_from_prior"))

    def test_merge_macro_prunes_stale_errors(self):
        prior = {
            "series": {"a50": {"close": 14700, "change_1d_pct": 0.3}},
            "errors": ["a50:empty", "cnh:timeout"],
        }
        fresh = {
            "series": {"cnh": {"close": 678.5, "change_1d_pct": -0.08}},
            "errors": ["cnh:empty"],
        }
        merged = merge_macro_snapshots(fresh, prior)
        self.assertEqual(merged["series"]["a50"]["close"], 14700)
        self.assertEqual(merged["series"]["cnh"]["close"], 678.5)
        self.assertEqual(merged["errors"], [])

        ctx = {"macro": {"fetched_at": "2020-01-01T08:00:00"}}
        rep = freshness_report(ctx, stale_hours=24.0)
        self.assertTrue(rep.get("needs_ingest"))

    def test_drain_ratio(self):
        events = [{"market_cap_est": 200e9, "name": "Mega IPO"}]
        amounts = {"机器人": 50e9}
        out = compute_drain_ratios(events, amounts)
        self.assertGreater(out["liquidity_drain_ratio"], 3.0)
        self.assertTrue(out["extreme_ipo_day"])


# ============================================================
# 原 test_market_context_round2.py
# ============================================================


class TestMarketContextRound2(unittest.TestCase):
    def test_concept_graph_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["QUANTLAB_STORE_DIR"] = tmp
            try:
                from core import paths

                paths.STORE_DIR = tmp
                idx = merge_concept_into_index({}, "机器人", ["600001", "600002"])
                save_concept_graph_cache(
                    concepts={"机器人": ["600001", "600002"]},
                    code_index=idx,
                )
                loaded, meta = load_concept_graph_cache(max_age_hours=1.0)
                self.assertTrue(meta.get("cache_hit"))
                self.assertIn("600001", loaded.get("code_index") or {})
            finally:
                os.environ.pop("QUANTLAB_STORE_DIR", None)

    def test_macro_view_asof(self):
        macro = {
            "liquidity_stress_score": 0,
            "series": {
                "sox": {
                    "recent_bars": [
                        {"date": "2026-08-17", "close": 100.0},
                        {"date": "2026-08-18", "close": 98.0},
                        {"date": "2026-08-19", "close": 96.0},
                    ]
                },
                "ndx": {
                    "recent_bars": [
                        {"date": "2026-08-17", "close": 200.0},
                        {"date": "2026-08-18", "close": 199.0},
                        {"date": "2026-08-19", "close": 197.0},
                    ]
                },
            },
        }
        view = macro_view_asof(macro, "2026-08-18")
        self.assertEqual(view.get("as_of"), "2026-08-18")
        self.assertIsNotNone(view.get("overseas_tech_1d_pct"))
        self.assertTrue(view.get("synthetic"))

    def test_pct_change_from_bars(self):
        bars = [
            {"date": "2026-08-18", "close": 100.0},
            {"date": "2026-08-19", "close": 95.0},
        ]
        self.assertAlmostEqual(pct_change_from_bars(bars, days=1), -5.0)

    def test_minute_prefetch_empty(self):
        out = prefetch_minute_bars([], fetch_if_missing=False)
        self.assertTrue(out.get("ok"))


# ============================================================
# 原 test_market_context_round3.py
# ============================================================


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
            os.environ["QUANTLAB_STORE_DIR"] = tmp
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
                os.environ.pop("QUANTLAB_STORE_DIR", None)

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
