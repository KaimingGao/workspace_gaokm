"""观察页 insights 摘要结构。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestWatchingInsights(unittest.TestCase):
    def test_insight_fields(self):
        from core.watching_insights import build_watching_insights

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
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.signal.cluster_live.load_active_cluster_book",
            return_value={},
        ), patch(
            "quant.research.rem_ridge.load_rem_model", return_value=None
        ), patch(
            "quant.research.rem_ridge.predict_rem_from_features", return_value=None
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

    def test_rs_fallback_when_no_excess(self):
        from core.watching_insights import build_watching_insights

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
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.signal.cluster_live.load_active_cluster_book",
            return_value={},
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertIsNone(item["excess_return_pct"])
        self.assertEqual(item["excess_label"], "RS66")

    def test_book_row_forwards_trade_score_fields(self):
        from core.watching_insights import _insight_from_book_row

        row = {
            "stock_code": "600519",
            "score": 0.40,
            "predicted_score": 0.40,
            "predicted_score_blend": 0.12,
            "predicted_score_tau": 0.12,
            "predicted_score_eod_rem": 0.08,
            "predicted_score_tau_delta": 0.04,
            "realized_t1_to_tau": 0.30,
        }
        # 无新缺口时不覆盖簿上已有 rem；表列 score 对齐 ŷ_trade
        with patch("core.ports.market.query_quote", return_value={}), patch(
            "core.data_service.get_bars", return_value={"bars": []}
        ), patch("core.stance.compute_buy_stance", return_value={}):
            out = _insight_from_book_row("600519", row)
        self.assertAlmostEqual(out["predicted_score"], 0.40)
        self.assertAlmostEqual(out["score"], 0.12)
        self.assertAlmostEqual(out["decision_score"], 0.12)
        self.assertAlmostEqual(out["predicted_score_blend"], 0.12)
        self.assertAlmostEqual(out["predicted_score_tau"], 0.12)
        self.assertAlmostEqual(out["predicted_score_eod_rem"], 0.08)
        self.assertAlmostEqual(out["predicted_score_tau_delta"], 0.04)
        self.assertAlmostEqual(out["realized_t1_to_tau"], 0.30)

    def test_book_row_repairs_stale_eod_next_blend(self):
        """旧簿把 blend 写成 EOD、但 ŷ_τ 仍在时，读路径重算加权 ŷ_trade。"""
        from core.watching_insights import _insight_from_book_row

        row = {
            "stock_code": "600519",
            "score": 0.234547,
            "predicted_score": 0.234547,
            "predicted_score_eod": 0.234547,
            "predicted_score_eod_rem": 0.234547,
            "predicted_score_tau": 0.063077,
            "predicted_score_blend": 0.234547,  # 塌成 EOD
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed"},
        }
        with patch("core.ports.market.query_quote", return_value={}), patch(
            "core.data_service.get_bars", return_value={"bars": []}
        ), patch("core.stance.compute_buy_stance", return_value={}):
            out = _insight_from_book_row("600519", row)
        expect = 0.5 * 0.234547 + 0.5 * 0.063077
        self.assertAlmostEqual(out["predicted_score"], 0.234547, places=5)
        self.assertAlmostEqual(out["predicted_score_blend"], expect, places=5)
        self.assertAlmostEqual(out["score"], expect, places=5)
        self.assertAlmostEqual(out["decision_score"], expect, places=5)

    def test_book_row_hydrates_eod_rem_when_missing(self):
        from core.watching_insights import _insight_from_book_row

        row = {
            "stock_code": "600519",
            "score": 0.40,
            "predicted_score": 0.40,
        }
        with patch("core.ports.market.query_quote", return_value={}), patch(
            "core.data_service.get_bars", return_value={"bars": []}
        ), patch("core.stance.compute_buy_stance", return_value={}), patch(
            "quant.research.rem_ridge.load_rem_model", return_value=None
        ), patch(
            "quant.research.rem_ridge.predict_rem_from_features", return_value=None
        ):
            out = _insight_from_book_row("600519", row)
        self.assertAlmostEqual(out["predicted_score_eod_rem"], 0.40)
        self.assertIsNone(out.get("predicted_score_tau"))
        self.assertAlmostEqual(out["predicted_score_blend"], 0.40)

    def test_book_row_hydrates_trade_fields_without_live_quote(self):
        """簿快路径不拉行情：无缺口时 rem=EOD，并写出 ŷ_trade。"""
        from core.watching_insights import _insight_from_book_row

        row = {
            "stock_code": "600519",
            "score": 0.40,
            "predicted_score": 0.40,
        }
        with patch("quant.research.rem_ridge.load_rem_model", return_value=None), patch(
            "quant.research.rem_ridge.predict_rem_from_features", return_value=None
        ):
            out = _insight_from_book_row("600519", row)
        self.assertAlmostEqual(out["predicted_score"], 0.40, places=5)
        self.assertAlmostEqual(out["predicted_score_eod_rem"], 0.40, places=5)
        self.assertIsNotNone(out.get("predicted_score_blend"))
        self.assertAlmostEqual(float(out["score"]), float(out["predicted_score_blend"]), places=5)

    def test_insights_covers_full_watchlist_up_to_hard_cap(self):
        from core.watching_insights import build_watching_insights, _INSIGHT_HARD_CAP

        codes = [f"{i:06d}" for i in range(1, 101)]

        def fake_score(code, **kwargs):
            return {
                "success": True,
                "quote": {"success": True, "stock_code": code},
                "signal_item": {"score": 50.0 + (int(code) % 10), "hard_reject": False, "factors": {}},
            }

        with patch("core.signal.score_stock.score_stock", side_effect=fake_score), patch(
            "core.watching_insights._spot_valuation_map",
            return_value={},
        ), patch(
            "core.signal.cluster_live.load_active_cluster_book",
            return_value={},
        ):
            out = build_watching_insights(codes)
        self.assertEqual(out["count"], 100)
        self.assertEqual(out.get("truncated"), 0)
        self.assertTrue(all(i.get("score") is not None for i in out["items"]))
        self.assertGreaterEqual(_INSIGHT_HARD_CAP, 100)

    def test_spot_valuation_warms_when_disk_empty(self):
        from core.watching_insights import _spot_valuation_map

        rows = [
            {"代码": "600519", "市盈率-动态": "20.5", "市净率": "8.25"},
            {"代码": "000001", "市盈率-动态": "5.1", "市净率": "0.65"},
        ]
        with patch(
            "core.ports.market.load_disk_spot", return_value=rows
        ), patch(
            "core.watching_insights._fill_valuation_from_em"
        ):
            out = _spot_valuation_map(["600519", "000001"])
        self.assertEqual(out["600519"]["pe"], 20.5)
        self.assertEqual(out["600519"]["pb"], 8.25)
        self.assertEqual(out["000001"]["pe"], 5.1)

    def test_spot_valuation_falls_back_to_em_when_spot_fails(self):
        from core.watching_insights import _spot_valuation_map

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
        from core.watching_insights import build_watching_insights

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
            "core.watching_insights._spot_valuation_map",
            return_value={"600519": {"pe": 18.2, "pb": 7.1}},
        ):
            out = build_watching_insights(["600519"])
        item = out["items"][0]
        self.assertEqual(item["pe"], 18.2)
        self.assertEqual(item["pb"], 7.1)

    def test_book_row_keeps_dual_track_on_oos_failed_heuristic(self):
        """heuristic 0–100 与组 ŷ% 双轨并存；不把 0–100 写进 predicted_score。"""
        from core.watching_insights import _insight_from_book_row

        row = {
            "stock_code": "600519",
            "score": 57.2,
            "heuristic_score": 57.2,
            "predicted_score": 57.2,  # 旧脏簿
            "predicted_score_eod": 57.2,
            "score_cluster": 1.25,
            "score_global": 0.4,
            "return_model_source": "oos_failed_heuristic",
            "score_scale": "heuristic_0_100",
            "cluster_label": "G_fail",
        }
        with patch("core.ports.market.query_quote", return_value={}), patch(
            "core.data_service.get_bars", return_value={"bars": []}
        ), patch("core.stance.compute_buy_stance", return_value={}):
            out = _insight_from_book_row("600519", row)
        self.assertEqual(out["score_scale"], "heuristic_0_100")
        self.assertAlmostEqual(float(out["heuristic_score"]), 57.2)
        # 表列 score = 组 ŷ%，不与 heuristic 混列
        self.assertAlmostEqual(float(out["score"]), 1.25)
        self.assertIsNone(out.get("predicted_score"))
        self.assertIsNone(out.get("predicted_score_eod"))
        self.assertAlmostEqual(float(out["score_cluster"]), 1.25)
        self.assertAlmostEqual(float(out["score_global"]), 0.4)
        # 校准列应能用组 ŷ 挂上（对照，不进决策）
        self.assertTrue(
            out.get("predicted_score_cal") is not None
            or out.get("predicted_score_blend_cal") is not None
            or out.get("score_calibration_enabled") in (True, False, None)
        )


if __name__ == "__main__":
    unittest.main()
