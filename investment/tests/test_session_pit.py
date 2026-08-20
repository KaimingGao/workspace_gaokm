"""盘中 / 收盘后日线 PIT 与双层 ŷ 窗口。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestSessionPit(unittest.TestCase):
    def test_strips_incomplete_asof_bar_during_session(self):
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-08-14", "open": 10.0, "close": 10.0},
            {"date": "2026-08-15", "open": 10.3, "close": 10.4},
        ]
        quote = {"date": "2026-08-15", "open": 10.3, "change_raw": 4.0}
        now = datetime(2026, 8, 15, 10, 15)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual([b["date"] for b in eod], ["2026-08-14"])
        self.assertTrue(pit["stripped_asof_bar"])
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "intraday")

    def test_close_buffer_1505(self):
        from core.signal.session_pit import asof_session_final

        asof = "2026-08-14"
        self.assertFalse(
            asof_session_final(asof, now=datetime(2026, 8, 14, 15, 0))
        )
        self.assertTrue(
            asof_session_final(asof, now=datetime(2026, 8, 14, 15, 5))
        )
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-08-14", "open": 10.0, "close": 10.0},
            {"date": "2026-08-15", "open": 10.3, "close": 10.5},
        ]
        quote = {"date": "2026-08-15", "open": 10.3}
        now = datetime(2026, 8, 15, 15, 30)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual(eod[-1]["date"], "2026-08-15")
        self.assertTrue(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "eod_next")

    def test_weekend_treats_last_session_as_final(self):
        from core.signal.session_pit import prepare_eod_bars

        bars = [{"date": "2026-08-14", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-08-14", "open": 10.0}
        now = datetime(2026, 8, 16, 10, 0)  # Sunday
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual(len(eod), 1)
        self.assertTrue(pit["rolled_to_next"])

    def test_quote_change_stripped_when_intraday(self):
        from core.signal.session_pit import quote_for_eod_score

        q = quote_for_eod_score(
            {"change_raw": 3.2, "change": "3.20%", "open": 10.0},
            strip_intraday_change=True,
        )
        self.assertIsNone(q.get("change_raw"))


class TestEodNextFusion(unittest.TestCase):
    def test_fuse_intraday_false_strips_tau_from_trade(self):
        from core.signal.dual_score import apply_tau_score_fields, rank_key_for_item

        item = {"predicted_score": 1.2, "score": 1.2}
        apply_tau_score_fields(
            item,
            rem_yhat=0.8,
            gap_pct=2.0,
            feats={"gap_pct": 2.0},
            fuse_intraday=False,
        )
        self.assertEqual(item["dual_score_window"], "eod_next")
        # 收盘后不减缺口
        self.assertAlmostEqual(item["predicted_score_eod_rem"], 1.2)
        # 主排序分剥离 τ：ŷ_trade = ŷ_EOD
        self.assertAlmostEqual(item["predicted_score_blend"], 1.2)
        self.assertAlmostEqual(item["predicted_score_tau"], 0.8)
        self.assertAlmostEqual(rank_key_for_item(item), 1.2)
        self.assertFalse((item.get("dual_score_weights") or {}).get("tau_in_trade"))
        # nowcast 对照仍吃 ŷ_τ，不能塌成 ŷ_EOD
        self.assertEqual(item["nowcast_as_of"], "open")
        self.assertNotAlmostEqual(item["predicted_score_nowcast"], 1.2, places=3)

    def test_eod_next_skips_stale_tau_buy_gate(self):
        from core.signal.dual_score import buy_passes_tau_gate

        ok, reason = buy_passes_tau_gate(
            {
                "predicted_score_tau": -1.0,
                "dual_score_window": "eod_next",
            },
            config={"dual_score": {"min_predicted_score_tau": 0.0}},
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_attach_respects_eod_next_window(self):
        from core.signal.dual_score import attach_dual_score_pit, rank_key_for_item

        item = {
            "predicted_score": 1.5,
            "dual_score_window": "eod_next",
        }
        quote = {"date": "2026-08-14", "open": 10.3}
        bars = [
            {"date": "2026-08-13", "open": 10.0, "close": 10.0},
            {"date": "2026-08-14", "open": 10.3, "close": 10.5},
        ]
        attach_dual_score_pit(item, quote=quote, bars=bars)
        self.assertEqual(item["dual_score_window"], "eod_next")
        # rem=EOD；收盘后主排序分 = ŷ_EOD（剥离 τ）
        self.assertAlmostEqual(item["predicted_score_eod_rem"], 1.5)
        self.assertAlmostEqual(item.get("predicted_score_blend"), 1.5)
        self.assertAlmostEqual(rank_key_for_item(item), 1.5)

    def test_mom3_hard_reject_can_be_skipped(self):
        from core.signal.scorer import score_bars

        bars = []
        price = 100.0
        for i in range(10):
            price *= 1.06
            bars.append(
                {
                    "date": f"2026-01-{i+1:02d}",
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1000,
                }
            )
        rejected = score_bars(bars)
        self.assertTrue(rejected.get("hard_reject"))
        self.assertIn("sub_scores", rejected)
        self.assertTrue(rejected.get("sub_scores"))
        kept = score_bars(bars, mom3_hard_reject=False)
        self.assertFalse(kept.get("hard_reject"))
        self.assertTrue(kept.get("sub_scores"))
        self.assertTrue(kept.get("mom3_chase_risk"))
        # 硬拒路径也必须留下 sub_scores，不能掐死 ŷ
        self.assertTrue(rejected.get("sub_scores"))
        self.assertGreater(float(rejected.get("score") or 0), 0)

    def test_default_still_maps_remaining(self):
        from core.signal.dual_score import apply_tau_score_fields, eod_remaining_at_tau

        item = {"predicted_score": 2.0}
        apply_tau_score_fields(item, rem_yhat=0.3, gap_pct=1.0, feats={"gap_pct": 1.0})
        rem = eod_remaining_at_tau(2.0, 1.0)
        self.assertEqual(item["dual_score_window"], "intraday")
        self.assertAlmostEqual(item["predicted_score_eod_rem"], rem, places=5)

    def test_rank_key_recomputes_stale_eod_next_blend(self):
        from core.signal.dual_score import (
            align_trade_score_fields,
            decision_score_for_item,
            dual_score_book_fields,
            rank_key_for_item,
        )

        item = {
            "predicted_score": 0.542957,
            "predicted_score_eod": 0.542957,
            "predicted_score_eod_rem": 0.542957,
            "predicted_score_tau": 0.047501,
            "predicted_score_blend": 0.295229,  # 旧错：掺了 τ
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed"},
            "gap_pct": 1.0,
        }
        # 收盘后排序 / 决策分 = ŷ_EOD
        self.assertAlmostEqual(rank_key_for_item(item), 0.542957, places=5)
        self.assertAlmostEqual(decision_score_for_item(item), 0.542957, places=5)
        align_trade_score_fields(item)
        self.assertAlmostEqual(item["predicted_score_blend"], 0.542957, places=5)
        self.assertAlmostEqual(item["score"], 0.542957, places=5)
        self.assertAlmostEqual(item["predicted_score"], 0.542957, places=5)

        # book_fields 返回已对齐 blend，且不改写调用方 score（仍为 EOD 主分场景）
        src = {
            "predicted_score": 0.542957,
            "predicted_score_eod_rem": 0.542957,
            "predicted_score_tau": 0.047501,
            "predicted_score_blend": 0.295229,
            "score": 0.542957,
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5},
            "gap_pct": 1.0,
        }
        fields = dual_score_book_fields(src)
        self.assertAlmostEqual(fields["predicted_score_blend"], 0.542957, places=5)
        self.assertEqual(src["score"], 0.542957)

    def test_load_active_cluster_book_aligns_stale_blend(self):
        from core.signal.cluster_live import _align_cluster_book_trade_scores

        doc = {
            "scored_all": [
                {
                    "stock_code": "603019",
                    "predicted_score": 1.896714,
                    "predicted_score_eod_rem": 1.896714,
                    "predicted_score_tau": 0.04579,
                    "predicted_score_blend": 0.971252,  # 旧错：0.5*(eod+tau)
                    "score": 0.971252,
                    "dual_score_window": "eod_next",
                    "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5},
                    "gap_pct": 0.0223,
                }
            ],
            "book": [],
        }
        _align_cluster_book_trade_scores(doc)
        row = doc["scored_all"][0]
        self.assertAlmostEqual(row["predicted_score_blend"], 1.896714, places=5)
        self.assertAlmostEqual(row["score"], 1.896714, places=5)
        self.assertAlmostEqual(row["predicted_score"], 1.896714, places=5)

    def test_align_repairs_unlifted_intraday_blend(self):
        """盘中旧簿用 w·ŷ_EOD+w·ŷ_τ（未抬缺口）时，读路径重算缺口∘ŷ_τ。"""
        from core.signal.dual_score import align_trade_score_fields
        from core.signal.nowcast_kf import compound_pct

        y_eod, y_tau, gap = 2.381498, 0.236315, -0.3114
        item = {
            "predicted_score": y_eod,
            "predicted_score_eod": y_eod,
            "predicted_score_tau": y_tau,
            "predicted_score_blend": 0.5 * y_eod + 0.5 * y_tau,
            "dual_score_window": "intraday",
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5},
            "gap_pct": gap,
        }
        align_trade_score_fields(item)
        tau_cc = compound_pct(gap, y_tau)
        expect = 0.5 * y_eod + 0.5 * tau_cc
        self.assertAlmostEqual(item["predicted_score_blend"], expect, places=5)
        self.assertAlmostEqual(item["predicted_score_blend_tau_cc"], tau_cc, places=5)
        self.assertEqual(item["predicted_score_blend_vs"], "prev_close")


if __name__ == "__main__":
    unittest.main()
