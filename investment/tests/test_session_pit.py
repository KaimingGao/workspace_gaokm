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
            {"date": "2026-09-17", "open": 10.0, "close": 10.0},
            {"date": "2026-09-18", "open": 10.3, "close": 10.4},
        ]
        quote = {"date": "2026-09-18", "open": 10.3, "change_raw": 4.0}
        now = datetime(2026, 9, 18, 10, 15)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual([b["date"] for b in eod], ["2026-09-17"])
        self.assertTrue(pit["stripped_asof_bar"])
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "intraday")

    def test_close_keeps_oo_cycle_until_next_open(self):
        """T 收盘后 ŷ_oo 仍是 T 开→T+1 开；今收不进 X。"""
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-09-17", "open": 10.0, "close": 10.0},
            {"date": "2026-09-18", "open": 10.3, "close": 10.5},
        ]
        quote = {"date": "2026-09-18", "open": 10.3}
        now = datetime(2026, 9, 18, 15, 30)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual([b["date"] for b in eod], ["2026-09-17"])
        self.assertTrue(pit["stripped_asof_bar"])
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["eod_as_of"], "2026-09-17")
        self.assertEqual(pit["oo_cycle_date"], "2026-09-18")
        self.assertEqual(pit["dual_score_window"], "eod_next")

    def test_close_buffer_1505(self):
        from core.signal.session_pit import asof_session_final

        asof = "2026-08-14"
        self.assertFalse(
            asof_session_final(asof, now=datetime(2026, 8, 14, 15, 0))
        )
        self.assertTrue(
            asof_session_final(asof, now=datetime(2026, 8, 14, 15, 5))
        )

    def test_oo_cycle_date_spans_close_until_next_open(self):
        from core.signal.session_pit import oo_cycle_date

        self.assertEqual(oo_cycle_date(now=datetime(2026, 9, 18, 10, 0)), "2026-09-18")
        self.assertEqual(oo_cycle_date(now=datetime(2026, 9, 18, 16, 0)), "2026-09-18")
        self.assertEqual(oo_cycle_date(now=datetime(2026, 9, 19, 10, 0)), "2026-09-18")
        self.assertEqual(oo_cycle_date(now=datetime(2026, 9, 21, 8, 0)), "2026-09-18")
        self.assertEqual(oo_cycle_date(now=datetime(2026, 9, 21, 9, 30)), "2026-09-21")

    def test_weekend_keeps_friday_oo_cycle(self):
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-09-17", "open": 9.9, "close": 10.0},
            {"date": "2026-09-18", "open": 10.0, "close": 10.2},
        ]
        quote = {"date": "2026-09-18", "open": 10.0}
        now = datetime(2026, 9, 19, 10, 0)  # Saturday
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual([b["date"] for b in eod], ["2026-09-17"])
        self.assertTrue(pit["stripped_asof_bar"])
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "eod_next")

    def test_refresh_flips_stale_eod_next_after_open(self):
        """簿上昨晚 eod_next，次日盘中应翻回 intraday。"""
        from unittest.mock import patch

        from core.signal.session_pit import refresh_dual_score_window
        from core.signal.dual_score import align_trade_score_fields

        item = {
            "predicted_score": 1.2,
            "predicted_score_eod": 1.2,
            "predicted_score_tau": 0.8,
            "predicted_score_blend": 1.2,
            "gap_pct": 1.0,
            "dual_score_window": "eod_next",
            "dual_score_head": "single_oo",
            "dual_score_single_head": True,
            "dual_score_weights": {"w_eod": 0.5, "w_tau": 0.5},
        }
        now = datetime(2026, 8, 21, 10, 15)
        quote = {"date": "2026-08-21", "open": 10.0}
        bars = [
            {"date": "2026-08-20", "open": 10.0, "close": 10.0},
            {"date": "2026-08-21", "open": 10.0, "close": 10.2},
        ]
        win = refresh_dual_score_window(item, quote=quote, bars=bars, now=now)
        self.assertEqual(win, "intraday")
        self.assertEqual(item["dual_score_window"], "intraday")
        # align 无 quote 时会再 refresh；钉死盘中时钟，避免夜间跑测翻成 eod_next
        with patch("core.signal.session_pit.shanghai_now", return_value=now):
            align_trade_score_fields(item, write_score=True)
        self.assertEqual(item["dual_score_window"], "intraday")
        self.assertFalse(item.get("dual_score_single_head"))
        self.assertEqual(item.get("dual_score_head"), "blend")
        # 0.5·1.2 + 0.5·缺口∘0.8；缺口=1 → tau_cc=(1.01)(1.008)-1)*100
        self.assertNotAlmostEqual(float(item["predicted_score_blend"]), 1.2, places=3)

    def test_refresh_keeps_eod_next_after_close(self):
        from core.signal.session_pit import refresh_dual_score_window

        item = {"dual_score_window": "eod_next"}
        now = datetime(2026, 8, 21, 15, 30)
        quote = {"date": "2026-08-21", "open": 10.0}
        bars = [
            {"date": "2026-08-20", "open": 10.0, "close": 10.0},
            {"date": "2026-08-21", "open": 10.0, "close": 10.5},
        ]
        win = refresh_dual_score_window(item, quote=quote, bars=bars, now=now)
        self.assertEqual(win, "eod_next")

    def test_refresh_preopen_without_quote_is_eod_next(self):
        """开盘前无 quote/bars：勿误判 intraday（否则持仓打包把 ŷ_trade 重算歪）。"""
        from core.signal.session_pit import refresh_dual_score_window

        item = {"dual_score_window": "eod_next"}
        now = datetime(2026, 8, 31, 2, 15)
        win = refresh_dual_score_window(item, now=now)
        self.assertEqual(win, "eod_next")
        self.assertEqual(item["dual_score_window"], "eod_next")

    def test_refresh_midday_without_quote_is_intraday(self):
        from core.signal.session_pit import refresh_dual_score_window

        item = {"dual_score_window": "eod_next"}
        now = datetime(2026, 8, 31, 10, 30)
        win = refresh_dual_score_window(item, now=now)
        self.assertEqual(win, "intraday")

    def test_refresh_weekend_daytime_without_quote_is_eod_next(self):
        """周末 10:00 不得因 session 回退到周五而标盘中。"""
        from core.signal.session_pit import refresh_dual_score_window

        item = {"dual_score_window": "intraday"}
        now = datetime(2026, 8, 16, 10, 0)  # Sunday
        win = refresh_dual_score_window(item, now=now)
        self.assertEqual(win, "eod_next")

    def test_offline_yesterday_quote_is_intraday_next_session(self):
        """本地仓 quote.date=昨收：次日盘中须 intraday，否则 TRADE 假「单」。"""
        from core.signal.session_pit import prepare_eod_bars, refresh_dual_score_window

        bars = [{"date": "2026-09-07", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-09-07", "open": 10.0, "close": 10.2}
        now = datetime(2026, 9, 8, 14, 21)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual(eod[-1]["date"], "2026-09-07")
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "intraday")
        item = {"dual_score_window": "eod_next"}
        win = refresh_dual_score_window(item, quote=quote, bars=bars, now=now)
        self.assertEqual(win, "intraday")

    def test_offline_yesterday_quote_is_eod_next_after_close(self):
        from core.signal.session_pit import prepare_eod_bars

        bars = [{"date": "2026-09-07", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-09-07", "open": 10.0, "close": 10.2}
        now = datetime(2026, 9, 8, 15, 30)
        _eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertTrue(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "eod_next")

    def test_offline_yesterday_quote_is_eod_next_preopen(self):
        """次日 09:30 前仍是上一周期；ŷ_oo 不滚。"""
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-09-04", "open": 9.8, "close": 9.9},
            {"date": "2026-09-07", "open": 10.0, "close": 10.2},
        ]
        quote = {"date": "2026-09-07", "open": 10.0, "close": 10.2}
        now = datetime(2026, 9, 8, 8, 0)
        eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertEqual([b["date"] for b in eod], ["2026-09-04"])
        self.assertFalse(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "eod_next")
        self.assertEqual(pit["oo_cycle_date"], "2026-09-07")

    def test_historical_asof_stays_eod_next_at_live_midday(self):
        """更早的历史报价日：即使此刻盘中，该 asof 会话已收盘，仍 eod_next。"""
        from core.signal.session_pit import prepare_eod_bars

        bars = [
            {"date": "2026-08-13", "open": 10.0, "close": 10.0},
            {"date": "2026-08-14", "open": 10.3, "close": 10.5},
        ]
        quote = {"date": "2026-08-14", "open": 10.3}
        now = datetime(2026, 9, 8, 14, 21)
        _eod, pit = prepare_eod_bars(bars, quote, now=now)
        self.assertTrue(pit["rolled_to_next"])
        self.assertEqual(pit["dual_score_window"], "eod_next")

    def test_quote_change_stripped_when_intraday(self):
        from core.signal.session_pit import quote_for_eod_score

        q = quote_for_eod_score(
            {"change_raw": 3.2, "change": "3.20%", "open": 10.0},
            strip_intraday_change=True,
        )
        self.assertIsNone(q.get("change_raw"))

    def test_quote_for_eod_stamps_open_gap_not_last_change(self):
        from core.signal.session_pit import quote_for_eod_score

        q = quote_for_eod_score(
            {"change_raw": 3.2, "change": "3.20%", "open": 10.0, "price_raw": 10.4},
            strip_intraday_change=False,
            gap_pct=1.5,
            open_t=10.2,
        )
        self.assertAlmostEqual(q.get("change_raw"), 1.5)
        self.assertAlmostEqual(float(q.get("open_raw")), 10.2)
        self.assertIsNone(q.get("change"))

    def test_resolve_open_t_daily_then_quote_then_minute(self):
        from core.signal.session_pit import (
            OPEN_T_SOURCE_DAILY,
            OPEN_T_SOURCE_MINUTE,
            OPEN_T_SOURCE_QUOTE,
            resolve_open_t,
        )

        bars = [
            {"date": "2026-09-16", "open": 10.0, "close": 10.2},
            {"date": "2026-09-17", "open": 10.4, "close": 10.5},
        ]
        quote = {"date": "2026-09-17", "open": 10.4, "open_raw": 10.4}
        got = resolve_open_t(quote, bars, trade_day="2026-09-17")
        self.assertEqual(got["source"], OPEN_T_SOURCE_DAILY)
        self.assertAlmostEqual(got["open"], 10.4)
        self.assertAlmostEqual(got["prev_close"], 10.2)
        self.assertAlmostEqual(got["gap_pct"], round((10.4 / 10.2 - 1.0) * 100.0, 4))

        live = {"open": 10.35, "open_raw": 10.35}
        hist = [{"date": "2026-09-16", "open": 10.0, "close": 10.2}]
        got2 = resolve_open_t(live, hist, trade_day="2026-09-17")
        self.assertEqual(got2["source"], OPEN_T_SOURCE_QUOTE)
        self.assertAlmostEqual(got2["open"], 10.35)
        self.assertAlmostEqual(got2["prev_close"], 10.2)

        synth = {"date": "2026-09-16", "open": 10.1}
        mins = [
            {
                "date": "2026-09-17",
                "datetime": "2026-09-17 09:30:00",
                "open": 10.33,
                "close": 10.40,
            }
        ]
        got3 = resolve_open_t(
            synth, hist, trade_day="2026-09-17", minute_bars=mins
        )
        self.assertEqual(got3["source"], OPEN_T_SOURCE_MINUTE)
        self.assertAlmostEqual(got3["open"], 10.33)
        self.assertAlmostEqual(got3["prev_close"], 10.2)

        late = [
            {
                "date": "2026-09-17",
                "datetime": "2026-09-17 09:40:00",
                "open": 10.50,
                "close": 10.55,
            }
        ]
        got4 = resolve_open_t(
            synth, hist, trade_day="2026-09-17", minute_bars=late
        )
        self.assertIsNone(got4["open"])
        self.assertIsNone(got4["source"])

    def test_resolve_open_t_rejects_synth_yesterday_open(self):
        from core.signal.session_pit import resolve_open_t

        bars = [{"date": "2026-09-16", "open": 10.1, "close": 10.2}]
        synth = {"date": "2026-09-16", "open": 10.1, "prev_close": 10.0}
        got = resolve_open_t(synth, bars, trade_day="2026-09-17")
        self.assertIsNone(got["open"])
        self.assertIsNone(got["gap_pct"])
        self.assertAlmostEqual(got["prev_close"], 10.2)


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
        self.assertNotIn("predicted_score_nowcast", item)

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
        # 历史 asof 已换期：主排序分 = ŷ_EOD（剥离 τ）
        self.assertAlmostEqual(item["predicted_score_eod_rem"], 1.5)
        self.assertAlmostEqual(item.get("predicted_score_blend"), 1.5)
        self.assertAlmostEqual(rank_key_for_item(item), 1.5)

    def test_attach_after_close_same_cycle_keeps_tau(self):
        """收盘后同一 ŷ_oo 周期：仍 fuse ŷ_oc，不因时钟 eod_next 剥 τ。"""
        from unittest.mock import patch

        from core.signal.dual_score import apply_tau_score_fields
        from core.signal.dual_score.tau import _resolve_fuse_intraday

        item = {"predicted_score": 1.5}
        quote = {"date": "2026-09-18", "open": 10.3}
        bars = [
            {"date": "2026-09-17", "open": 10.0, "close": 10.0},
            {"date": "2026-09-18", "open": 10.3, "close": 10.5},
        ]
        now = datetime(2026, 9, 18, 15, 30)
        with patch("core.signal.session_pit.shanghai_now", return_value=now):
            fuse = _resolve_fuse_intraday(
                item, fuse_intraday=None, quote=quote, bars=bars
            )
            apply_tau_score_fields(
                item, rem_yhat=0.8, gap_pct=3.0, fuse_intraday=fuse
            )
        self.assertTrue(fuse)
        self.assertEqual(item["dual_score_window"], "intraday")
        self.assertAlmostEqual(item.get("predicted_score_tau"), 0.8)

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
        from unittest.mock import patch
        from datetime import timezone, timedelta
        from core.signal.dual_score import (
            align_trade_score_fields,
            decision_score_for_item,
            dual_score_book_fields,
            rank_key_for_item,
        )

        cn = timezone(timedelta(hours=8))
        after_close = datetime(2026, 8, 20, 16, 0, tzinfo=cn)
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
        with patch("core.signal.session_pit.shanghai_now", return_value=after_close):
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

    def test_align_trade_score_fields_repairs_stale_eod_next_blend(self):
        from unittest.mock import patch
        from datetime import timezone, timedelta
        from core.signal.dual_score import align_trade_score_fields

        cn = timezone(timedelta(hours=8))
        after_close = datetime(2026, 8, 20, 16, 0, tzinfo=cn)
        row = {
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
        with patch("core.signal.session_pit.shanghai_now", return_value=after_close):
            align_trade_score_fields(row, write_score=True)
            self.assertAlmostEqual(row["predicted_score_blend"], 1.896714, places=5)
            self.assertAlmostEqual(row["score"], 1.896714, places=5)
            self.assertAlmostEqual(row["predicted_score"], 1.896714, places=5)

    def test_align_refresh_false_keeps_offline_eod_next_at_midday(self):
        """仅本地仓 asof 昨收：score_one 标 eod_next；二次 align 不得用盘中时钟翻成 intradate。"""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch

        from core.signal.dual_score import align_trade_score_fields

        cn = timezone(timedelta(hours=8))
        midday = datetime(2026, 9, 1, 10, 30, tzinfo=cn)
        item = {
            "predicted_score": 0.80,
            "predicted_score_eod": 0.80,
            "predicted_score_tau": -0.40,
            "predicted_score_blend": 0.80,  # eod_next 已剥离 τ
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 1.0, "w_tau": 0.0, "w_mode": "fixed"},
            "gap_pct": 1.0,
        }
        with patch("core.signal.session_pit.shanghai_now", return_value=midday):
            align_trade_score_fields(item, write_score=False, refresh_window=False)
        self.assertEqual(item["dual_score_window"], "eod_next")
        self.assertAlmostEqual(float(item["predicted_score_blend"]), 0.80, places=5)

        # 对照：默认 refresh 会被盘中时钟改成 intradate 并可能重融 τ
        flipped = dict(item)
        flipped["predicted_score_blend"] = 0.80
        flipped["dual_score_window"] = "eod_next"
        with patch("core.signal.session_pit.shanghai_now", return_value=midday):
            align_trade_score_fields(flipped, write_score=False, refresh_window=True)
        self.assertEqual(flipped["dual_score_window"], "intraday")

    def test_book_fields_preserves_eod_next_window(self):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch

        from core.signal.dual_score import dual_score_book_fields

        cn = timezone(timedelta(hours=8))
        midday = datetime(2026, 9, 1, 10, 30, tzinfo=cn)
        src = {
            "predicted_score": 0.80,
            "predicted_score_eod": 0.80,
            "predicted_score_tau": -0.40,
            "predicted_score_blend": 0.80,
            "score": 0.80,
            "dual_score_window": "eod_next",
            "dual_score_weights": {"w_eod": 1.0, "w_tau": 0.0},
            "gap_pct": 1.0,
        }
        with patch("core.signal.session_pit.shanghai_now", return_value=midday):
            fields = dual_score_book_fields(src)
        self.assertEqual(fields.get("dual_score_window"), "eod_next")
        self.assertAlmostEqual(float(fields["predicted_score_blend"]), 0.80, places=5)

    def test_intraday_repairs_stale_eod_next_zero_w_tau(self):
        """盘中：旧簿 eod_next 的 w_τ=0 残留时，读路径须恢复双头融合。"""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch

        from core.signal.dual_score import align_trade_score_fields
        from core.signal.yhat_geom import compound_pct

        y_eod, y_tau, gap = 2.5, -0.04, 0.26
        item = {
            "predicted_score": y_eod,
            "predicted_score_eod": y_eod,
            "predicted_score_blend": y_eod,
            "predicted_score_tau": y_tau,
            "score": y_eod,
            "gap_pct": gap,
            "dual_score_window": "eod_next",
            "dual_score_weights": {
                "w_eod": 0.5,
                "w_tau": 0.0,
                "tau_in_trade": False,
                "window": "eod_next",
            },
        }
        cn = timezone(timedelta(hours=8))
        midday = datetime(2026, 8, 26, 12, 30, tzinfo=cn)
        with patch("core.signal.session_pit.shanghai_now", return_value=midday):
            align_trade_score_fields(
                item, config={"dual_score": {"w_oo": 0.5, "w_tau": 0.5}}
            )
        self.assertEqual(item["dual_score_window"], "intraday")
        tau_cc = compound_pct(gap, y_tau)
        expect = 0.5 * y_eod + 0.5 * tau_cc
        self.assertAlmostEqual(item["predicted_score_blend"], expect, places=4)
        self.assertNotAlmostEqual(item["predicted_score_blend"], y_eod, places=3)
        bw = item.get("dual_score_weights") or {}
        self.assertAlmostEqual(float(bw.get("w_tau") or 0), 0.5, places=4)
        self.assertTrue(bw.get("tau_in_trade"))

    def test_offline_yesterday_quote_fuses_tau_next_session(self):
        """数据中心 offline 昨收 quote：次日盘中 ŷ_trade 须融 τ，不得单头「单」。"""
        from core.signal.session_pit import prepare_eod_bars
        from core.signal.dual_score import apply_tau_score_fields

        bars = [{"date": "2026-09-07", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-09-07", "open": 10.0}
        now = datetime(2026, 9, 8, 14, 21)
        _eod, pit = prepare_eod_bars(bars, quote, now=now)
        item = {"predicted_score": 2.38, "score": 2.38}
        apply_tau_score_fields(
            item,
            rem_yhat=1.79,
            gap_pct=-0.58,
            feats={"gap_pct": -0.58},
            fuse_intraday=not bool(pit.get("rolled_to_next")),
        )
        self.assertEqual(item["dual_score_window"], "intraday")
        self.assertEqual(item.get("dual_score_head"), "blend")
        self.assertFalse(item.get("dual_score_single_head"))
        self.assertTrue((item.get("dual_score_weights") or {}).get("tau_in_trade"))
        self.assertNotAlmostEqual(float(item["predicted_score_blend"]), 2.38, places=3)

    def test_minute_tau_trade_date_live_uses_session_not_yesterday_bar(self):
        """实时行情无 date：盘中 ŷ_oc 的 T 是今天，不是昨收完整 K。"""
        from core.signal.session_pit import resolve_minute_tau_trade_date

        bars = [{"date": "2026-09-16", "open": 10.0, "close": 10.2}]
        quote = {"open": 10.3, "prev_close": 10.2}
        now = datetime(2026, 9, 17, 11, 42)
        self.assertEqual(
            resolve_minute_tau_trade_date(quote, bars, now=now),
            "2026-09-17",
        )

    def test_minute_tau_trade_date_offline_yesterday_quote_upgrades_intraday(self):
        """offline 合成 quote.date=昨收：次日盘中仍切到会话日。"""
        from core.signal.session_pit import resolve_minute_tau_trade_date

        bars = [{"date": "2026-09-16", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-09-16", "open": 10.0}
        now = datetime(2026, 9, 17, 11, 42)
        self.assertEqual(
            resolve_minute_tau_trade_date(quote, bars, now=now),
            "2026-09-17",
        )

    def test_minute_tau_trade_date_keeps_historical_pit_day(self):
        """回测报价日不是「最新完整日线」时，不得被墙上时钟改成今天。"""
        from core.signal.session_pit import resolve_minute_tau_trade_date

        bars = [
            {"date": "2026-08-13", "close": 10.0},
            {"date": "2026-08-14", "close": 10.5},
        ]
        quote = {"date": "2026-08-14", "open": 10.3}
        now = datetime(2026, 9, 17, 11, 42)
        self.assertEqual(
            resolve_minute_tau_trade_date(quote, bars, now=now),
            "2026-08-14",
        )

    def test_minute_tau_trade_date_after_close_stays_on_cycle_t(self):
        """收盘后 ŷ_oc 仍是今日 10:00，不退回昨收日。"""
        from core.signal.session_pit import resolve_minute_tau_trade_date

        bars = [{"date": "2026-09-17", "open": 10.0, "close": 10.2}]
        quote = {"date": "2026-09-17", "open": 10.0, "close": 10.2}
        now = datetime(2026, 9, 17, 16, 10)
        self.assertEqual(
            resolve_minute_tau_trade_date(quote, bars, now=now),
            "2026-09-17",
        )

    def test_minute_tau_trade_date_weekend_stays_last_session(self):
        from core.signal.session_pit import resolve_minute_tau_trade_date

        bars = [{"date": "2026-09-18", "close": 10.2}]
        quote = {"date": "2026-09-18", "open": 10.0}
        now = datetime(2026, 9, 19, 11, 42)
        self.assertEqual(
            resolve_minute_tau_trade_date(quote, bars, now=now),
            "2026-09-18",
        )

    def test_tau_open_drops_yesterday_synth_keeps_live_undated(self):
        from core.signal.session_pit import tau_open_and_prev_close

        bars = [
            {"date": "2026-09-15", "open": 9.8, "close": 10.0},
            {"date": "2026-09-16", "open": 10.1, "close": 10.2},
        ]
        synth = {"date": "2026-09-16", "open": 10.1, "prev_close": 10.0}
        o, pc = tau_open_and_prev_close(synth, bars, "2026-09-17")
        self.assertIsNone(o)
        self.assertAlmostEqual(pc, 10.2)

        live = {"open": 10.35, "price_raw": 10.4}
        o2, pc2 = tau_open_and_prev_close(live, bars, "2026-09-17")
        self.assertAlmostEqual(o2, 10.35)
        self.assertAlmostEqual(pc2, 10.2)

        pit = {"date": "2026-08-14", "open": 10.3, "prev_close": 10.0}
        pit_bars = [
            {"date": "2026-08-13", "close": 10.0},
            {"date": "2026-08-14", "open": 10.3, "close": 10.5},
        ]
        o3, pc3 = tau_open_and_prev_close(pit, pit_bars, "2026-08-14")
        self.assertAlmostEqual(o3, 10.3)
        self.assertAlmostEqual(pc3, 10.0)

    def test_attach_pit_trade_day_keeps_yesterday_gap_next_evening(self):
        """回测 T=昨收完整日：夜盘墙上时钟不得把缺口抬丢。"""
        from unittest.mock import patch

        from core.signal.dual_score import attach_dual_score_pit

        item = {"stock_code": "000739", "predicted_score": 1.0}
        quote = {
            "date": "2026-09-17",
            "open": 10.049,
            "open_raw": 10.049,
            "prev_close": 10.0,
        }
        bars = [
            {"date": "2026-09-16", "open": 9.9, "high": 10.1, "low": 9.8, "close": 10.0},
            {
                "date": "2026-09-17",
                "open": 10.049,
                "high": 10.2,
                "low": 10.0,
                "close": 10.1,
                "prev_close": 10.0,
            },
        ]
        now = datetime(2026, 9, 18, 22, 10)
        with patch("core.signal.session_pit.shanghai_now", return_value=now):
            out = attach_dual_score_pit(
                dict(item),
                quote=quote,
                bars=bars,
                use_minute_tau=False,
                trade_day="2026-09-17",
            )
        feats = out.get("features_tau") or {}
        self.assertAlmostEqual(float(feats.get("gap_pct")), 0.49, places=2)
        self.assertAlmostEqual(float(out.get("gap_pct")), 0.49, places=2)


if __name__ == "__main__":
    unittest.main()
