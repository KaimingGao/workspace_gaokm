"""观察页 insights 摘要结构。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestWatchingInsights(unittest.TestCase):
    def setUp(self):
        from core.watching.insights import reset_insight_memo

        reset_insight_memo()

    def test_insight_fields(self):
        from core.watching.insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "change_raw": 1.2,
                "volume": "1.2万",
            },
            "signal_item": {
                "score": 62.0,
                "hard_reject": False,
                "factors": {
                    "volume_ratio": 1.35,
                    "value_pe": 20.5,
                    "value_pb": 8.5,
                    "excess_return_pct": 3.2,
                },
                "sub_scores": {"relative_strength": 70.0},
            },
        }

        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=None
        ):
            out = build_watching_insights(
                ["600519"],
                added_at_by_code={"600519": "2026-07-20T10:00:00"},
            )

        self.assertTrue(out["ok"])
        item = out["items"][0]
        self.assertEqual(item["predicted_score"], 62.0)
        self.assertEqual(item["score"], 62.0)  # 无 ŷ_τ 时 ŷ_trade 退回 EOD
        self.assertIn(item["stance_short"], {"轻仓", "关注", "观望", "—"})
        self.assertEqual(item["excess_return_pct"], 3.2)
        self.assertEqual(item["volume_ratio"], 1.35)
        self.assertEqual(item["pe"], 20.5)
        self.assertEqual(item["pb"], 8.5)
        self.assertIsNotNone(item["days_watched"])
        self.assertFalse(item["hard_reject"])

    def test_insight_memo_skips_rescore(self):
        from core.watching.insights import build_watching_insights, reset_insight_memo

        reset_insight_memo()
        fake_score = {
            "success": True,
            "quote": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "change_raw": 1.2,
                "volume": "1.2万",
            },
            "signal_item": {
                "score": 62.0,
                "hard_reject": False,
                "factors": {
                    "volume_ratio": 1.35,
                    "value_pe": 20.5,
                    "value_pb": 8.5,
                    "excess_return_pct": 3.2,
                },
                "sub_scores": {"relative_strength": 70.0},
            },
        }
        score_mock = Mock(return_value=fake_score)
        with patch("core.signal.score_stock.score_stock", score_mock), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=None
        ):
            first = build_watching_insights(
                ["600519"],
                added_at_by_code={"600519": "2026-07-20T10:00:00"},
            )
            second = build_watching_insights(
                ["600519"],
                added_at_by_code={"600519": "2026-07-20T10:00:00"},
            )
        self.assertTrue(first["ok"])
        self.assertEqual(score_mock.call_count, 1)
        self.assertEqual(second.get("cached_count"), 1)
        self.assertEqual(second.get("live_scored"), 0)
        self.assertEqual(
            first["items"][0]["predicted_score"],
            second["items"][0]["predicted_score"],
        )

    def test_rs_fallback_when_no_excess(self):
        from core.watching.insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {"success": True, "stock_code": "600519", "change_raw": 0.5},
            "signal_item": {
                "score": 55.0,
                "hard_reject": False,
                "factors": {"volume_ratio": 1.0},
                "sub_scores": {"relative_strength": 66.0},
            },
        }
        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={},
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertIsNone(item["excess_return_pct"])
        self.assertEqual(item["excess_label"], "RS66")

    def test_insights_covers_full_watchlist_up_to_hard_cap(self):
        from core.watching.insights import build_watching_insights, _INSIGHT_HARD_CAP

        codes = [f"{i:06d}" for i in range(1, 101)]

        def fake_score(code, **kwargs):
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code},
                "signal_item": {"score": 50.0 + (int(code) % 10), "hard_reject": False, "factors": {}},
            }

        with patch("core.signal.score_stock.score_stock", side_effect=fake_score), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.data.facade.get_bars", return_value={"bars": []}
        ):
            out = build_watching_insights(codes)
        self.assertEqual(out["count"], 100)
        self.assertEqual(out.get("truncated"), 0)
        self.assertTrue(all(i.get("score") is not None for i in out["items"]))
        self.assertGreaterEqual(_INSIGHT_HARD_CAP, 200)

    def test_spot_valuation_warms_when_disk_empty(self):
        from core.watching.insights import _spot_valuation_map

        rows = [
            {"代码": "600519", "市盈率-动态": "20.5", "市净率": "8.25"},
            {"代码": "000001", "市盈率-动态": "5.1", "市净率": "0.65"},
        ]
        with patch(
            "core.ports.market.load_disk_spot", return_value=rows
        ), patch(
            "core.watching.insights._fill_valuation_from_em"
        ):
            out = _spot_valuation_map(["600519", "000001"])
        self.assertEqual(out["600519"]["pe"], 20.5)
        self.assertEqual(out["600519"]["pb"], 8.25)
        self.assertEqual(out["000001"]["pe"], 5.1)

    def test_spot_valuation_falls_back_to_em_when_spot_fails(self):
        from core.watching.insights import _spot_valuation_map

        with patch(
            "core.ports.market.load_disk_spot", return_value=[]
        ), patch(
            "core.valuation_em.fetch_valuation_pack",
            return_value={"pe": 19.7, "pb": 6.03, "pe_ttm": 19.7},
        ):
            out = _spot_valuation_map(["600519"])
        self.assertEqual(out["600519"]["pe"], 19.7)
        self.assertEqual(out["600519"]["pb"], 6.03)

    def test_pe_pb_from_spot_when_factors_missing(self):
        from core.watching.insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {"success": True, "stock_code": "600519"},
            "signal_item": {
                "score": 55.0,
                "hard_reject": False,
                "factors": {},
            },
        }
        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={"600519": {"pe": 18.2, "pb": 7.1}},
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertEqual(item["pe"], 18.2)
        self.assertEqual(item["pb"], 7.1)

    def test_insight_passes_y_τc(self):
        """insights 仍透传 y_τc 给 tip；主表不再列。"""
        from core.watching.insights import build_watching_insights

        fake_score = {
            "success": True,
            "quote": {"success": True, "stock_code": "600519", "change_raw": 0.5},
            "signal_item": {
                "score": 1.0,
                "predicted_score": 1.0,
                "predicted_score_eod": 1.0,
                "predicted_score_eod_rem": 0.9,
                "predicted_score_tau": 0.4,
                "y_τc": 0.85,
                "predicted_score_τc": 0.85,
                "y_r": 0.85,
                "hard_reject": False,
                "factors": {"volume_ratio": 1.0},
            },
        }
        with patch("core.signal.score_stock.score_stock", return_value=fake_score), patch(
            "core.watching.insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.research.tau_ridge.load_tau_model", return_value=None
        ), patch(
            "core.research.tau_ridge.predict_tau_from_features", return_value=None
        ), patch(
            "core.data.facade.get_bars", return_value={"bars": []}
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertAlmostEqual(item.get("y_τc"), 0.85, places=6)
        self.assertAlmostEqual(item.get("predicted_score_τc"), 0.85, places=6)
        self.assertAlmostEqual(item.get("y_r"), 0.85, places=6)

    def test_insight_one_uses_offline_only(self):
        """数据中心算分默认 offline_only + 不打实时 quote。"""
        from core.watching.insights import _insight_one

        captured = {}

        def _fake_score(code, **kw):
            captured.update(kw)
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code, "price_raw": 10.0},
                "signal_item": {
                    "stock_code": code,
                    "score": 1.0,
                    "predicted_score": 0.01,
                    "hard_reject": False,
                },
            }

        with patch("core.signal.score_stock.score_stock", side_effect=_fake_score), patch(
            "core.watching.insights._spot_valuation_map", return_value={}
        ), patch("core.data.facade.get_quote") as gq:
            row = _insight_one("600519")
        self.assertTrue(captured.get("offline_only"))
        self.assertTrue(captured.get("skip_sentiment"))
        self.assertTrue(captured.get("skip_fundamentals"))
        self.assertIs(captured.get("use_minute_tau"), True)
        self.assertEqual(row.get("stock_code"), "600519")
        gq.assert_not_called()

    def test_insight_one_exposes_eod_feature_as_of(self):
        from core.watching.insights import _insight_one

        def _fake_score(code, **kw):
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code, "price_raw": 10.0},
                "signal_item": {
                    "stock_code": code,
                    "score": 1.0,
                    "predicted_score": 0.01,
                    "hard_reject": False,
                    "eod_feature_as_of": "2026-09-16",
                    "dual_score_window": "intraday",
                    "factor_anomaly": {
                        "ok": False,
                        "fatal_eod": False,
                        "issues": [{"kind": "missing", "code": "open_t"}],
                    },
                },
            }

        with patch("core.signal.score_stock.score_stock", side_effect=_fake_score), patch(
            "core.watching.insights._spot_valuation_map", return_value={}
        ):
            row = _insight_one("000001")
        self.assertEqual(row.get("eod_feature_as_of"), "2026-09-16")
        self.assertEqual((row.get("factor_anomaly") or {}).get("ok"), False)

    def test_insight_one_live_mode_allows_remote_flag(self):
        from core.watching.insights import _insight_one

        captured = {}

        def _fake_score(code, **kw):
            captured.update(kw)
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code, "price_raw": 10.0},
                "signal_item": {
                    "stock_code": code,
                    "score": 1.0,
                    "predicted_score": 0.01,
                    "hard_reject": False,
                },
            }

        with patch("core.signal.score_stock.score_stock", side_effect=_fake_score), patch(
            "core.watching.insights._spot_valuation_map", return_value={}
        ):
            _insight_one("600519", offline_only=False)
        self.assertFalse(captured.get("offline_only"))
        self.assertIs(captured.get("use_minute_tau"), True)

    def test_insight_one_stamps_ranking_from_paper_fusion(self):
        """数据中心 ranking 用 paper rank_lots 权，与交易执行同式。"""
        from core.watching.insights import _insight_one

        paper = {
            "rules": {
                "execution": {
                    "rebalance_timing": {
                        "rank_lots": {
                            "fusion_w_oo": 0.8,
                            "fusion_w_oc": 0.2,
                            "fusion_w_co": 0.0,
                        }
                    }
                }
            }
        }

        def _fake_score(code, **kw):
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code, "price_raw": 10.0},
                "signal_item": {
                    "stock_code": code,
                    "score": -3.65,
                    "predicted_score": 2.30,
                    "predicted_score_eod": 2.30,
                    "y_oo": 2.30,
                    "y_oc": 5.69,
                    "predicted_score_blend": -3.65,
                    "hard_reject": False,
                },
            }

        with patch("core.signal.score_stock.score_stock", side_effect=_fake_score), patch(
            "core.watching.insights._spot_valuation_map", return_value={}
        ), patch("core.watching.insights._hydrate_insight_tau_fields"), patch(
            "core.stance.compute_buy_stance",
            return_value={"stance_code": "wait", "stance_label": "观望"},
        ):
            row = _insight_one("600869", paper_ctx=paper)
        self.assertAlmostEqual(float(row.get("fusion_w_oo")), 0.8)
        self.assertAlmostEqual(float(row.get("fusion_w_oc")), 0.2)
        self.assertAlmostEqual(float(row.get("ranking")), 0.8 * 2.30 + 0.2 * 5.69, places=4)

    def test_insight_quote_bars_offline_no_live_quote(self):
        from core.watching.insights import _insight_quote_bars

        bars = [
            {"date": "2026-08-27", "close": 9.5, "open": 9.4},
            {"date": "2026-08-28", "close": 10.0, "open": 9.8, "high": 10.2, "low": 9.7},
        ]
        with patch(
            "core.data.facade.get_bars",
            return_value={"bars": bars, "data_source": "cache"},
        ) as gb, patch("core.data.facade.get_quote") as gq:
            q, b = _insight_quote_bars("600519")
        self.assertEqual(len(b), 2)
        self.assertTrue(q.get("success"))
        self.assertEqual(q.get("price_raw"), 10.0)
        self.assertEqual(q.get("data_source"), "offline_daily_synth")
        self.assertEqual(q.get("date"), "2026-08-28")
        self.assertEqual(q.get("prev_close"), 9.5)
        self.assertTrue(gb.call_args.kwargs.get("offline_only"))
        gq.assert_not_called()


class TestHoldingScoresCausalRebalance(unittest.TestCase):
    def test_compute_holding_scores_uses_causal_rebalance(self):
        """交易执行持仓表算分走 09:30–10:00 调仓因果前缀。"""
        from services.paper_account import PaperAccountMixin

        captured = {}

        class _R:
            def as_dict(self):
                return {
                    "success": True,
                    "signal_item": {"stock_code": "600519", "score": 1.0},
                }

        class _Svc:
            def score_one(self, code, **kw):
                captured.update(kw)
                return _R()

            def pack_holding_row(self, item, **kw):
                return dict(item)

        mixin = PaperAccountMixin()
        paper = {"holdings": [{"stock_code": "600519"}]}
        summary = {"holdings": [{"stock_code": "600519"}]}
        with patch(
            "core.signal.service.get_default_signal_service",
            return_value=_Svc(),
        ), patch(
            "core.signal.score_display.annotate_score_gate",
            return_value={"min_score": 0, "below_min_score": False},
        ), patch(
            "core.signal.score_display.selection_min_score",
            return_value=0,
        ):
            mixin._compute_holding_scores(paper, summary)
        self.assertIs(captured.get("use_minute_tau"), True)


if __name__ == "__main__":
    unittest.main()
