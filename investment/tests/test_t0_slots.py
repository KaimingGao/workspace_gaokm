"""多轮独立做 T：决策时钟确认根收盘开第一腿。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.t0.config import load_t0_rules, t0_slots_enabled
from core.t0.rules import simulate_t0_day


def _bar(date, o, h, l, c):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _am_mins(date, n, *, px):
    """从 09:35 起 n 根 5m；px(i, hm) → (o,h,l,c)。"""
    h, m = 9, 35
    out = []
    for i in range(n):
        hm = f"{h:02d}:{m:02d}"
        o, hi, lo, c = px(i, hm)
        out.append(
            {
                "datetime": f"{date} {hm}:00",
                "date": date,
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
            }
        )
        m += 5
        if m >= 60:
            h += 1
            m -= 60
    return out


def _slot_rules(**kwargs):
    base = {
        "t0_slots_enabled": True,
        "t0_slots": [
            {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.20},
            {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.20},
        ],
        "direction": "buy_then_sell",
        "t0_round_ratio": 0.2,
        "y_τc_enter": 0,
        "y_τc_strong": 0,
        "y_τc_enter_amount": 0,
        "y_τc_strong_amount": 0,
        "y_tpd_max": 1.0,
        "y_t30_strong": 1.0,
        "y_τ30_strong": 1.0,
        "y_t60_strong": 1.0,
        "y_τ60_strong": 1.0,
        "y_t90_strong": 1.0,
        "y_τ90_strong": 1.0,
        "min_range_pct": 0.1,
        "min_range_pct_buy_then_sell": 0.1,
        "t0_pm_degrade": "",
        "t0_pm_degrade_buy_then_sell": "",
        "t0_stop_pct_buy_then_sell": 0,
        "t0_stop_pct_sell_then_buy": 0,
        "t0_lock_win_pct_buy_then_sell": 0,
        "t0_lock_win_pct_sell_then_buy": 0,
        "must_cover_same_day_buy_then_sell": False,
        "fill_mode": "trigger",
        "lot_size": 100,
        "ref": "open",
        "path_mode": "first_touch",
        "use_atr": False,
    }
    base.update(kwargs)
    return base


def _px_bts_confirm_then_rally(i, hm):
    """正T：前缀 ŷ_τw≥门槛开第一腿；其后冲高卖第二腿。"""
    if i < 5:
        return (99.8, 100.5, 99.0, 100.2)
    if i == 5:
        return (100.0, 100.6, 99.8, 100.4)
    if i >= 8:
        return (99.5, 104.0, 99.0, 103.0)
    return (99.0, 100.0, 98.8, 99.5)


def _px_stb_confirm_then_drop(i, hm):
    """反T：前缀上冲；10:00 确认根收阴开第一腿；其后下探回补。"""
    if i < 5:
        return (100.2, 100.5, 100.1, 100.3)
    if i == 5:
        return (100.3, 100.6, 100.0, 100.0)
    if i >= 8:
        return (99.5, 100.0, 96.0, 97.0)
    return (100.0, 100.5, 99.5, 100.2)


class TestT0Slots(unittest.TestCase):
    def test_default_four_slots_until_1130(self):
        cfg = load_t0_rules()
        self.assertTrue(t0_slots_enabled(cfg))
        hms = [s["hm"] for s in cfg["t0_slots"]]
        self.assertEqual(hms, ["11:00"] * 5)
        self.assertEqual(cfg["t0_slots"][-1]["prefix_bars"], 18)
        self.assertEqual(cfg["t0_slots"][0]["prefix_bars"], 18)
        self.assertAlmostEqual(float(cfg["t0_slots"][0]["ratio"]), 0.20)
        self.assertAlmostEqual(sum(float(s["ratio"]) for s in cfg["t0_slots"]), 1.0)
        self.assertAlmostEqual(float(cfg.get("y_tw_enter") or 0), 2.0)
        self.assertAlmostEqual(float(cfg.get("y_τc_enter") or 0), 0.5)
        self.assertAlmostEqual(float(cfg.get("y_τc_strong") or 0), 1.0)
        self.assertNotIn("y_tw_strong", cfg)
        self.assertNotIn("t0_leg1_close_extreme", cfg)
        self.assertNotIn("t0_bar_oc_gate", cfg)
        self.assertNotIn("t0_ytw_prefix_confirm", cfg)
        self.assertAlmostEqual(float(cfg.get("y_tw_midpoint") or 0), 47.0)
        self.assertAlmostEqual(float(cfg.get("t0_close_band_delta_pct") or 0), 0.5)

    def test_pm_slot_stripped_from_custom_list(self):
        from core.t0.config import normalize_t0_slots

        slots = normalize_t0_slots(
            [
                {"id": "s4", "hm": "11:30", "prefix_bars": 24, "ratio": 0.20},
                {"id": "s5", "hm": "13:00", "prefix_bars": 25, "ratio": 0.20},
            ]
        )
        # 11:30/13:00 均超 v6 末轮 → 回落默认五轮壳
        self.assertEqual([s["hm"] for s in slots], ["11:00"] * 5)
    def test_buy_then_sell_fills_on_close_band_break(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        def px(i, hm):
            return _px_bts_confirm_then_rally(i, hm)

        mins = _am_mins(date, 14, px=px)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules=_slot_rules(),
            # 空 code：用开盘锚测破带 timing；有 code 时前缀 ĉ 贴价会推迟破带
            stock_code="",
            minute_bars=mins,
            scores={"y_tau": 0.5, "y_trade": 0.4, "y_eod": 0.3, "y_path": 0.8},
        )
        self.assertTrue(out.get("success"), out)
        trades = out.get("trades") or []
        buys = [t for t in trades if str(t.get("side") or "").endswith("buy")]
        self.assertTrue(buys, out)
        first_buy = buys[0]
        self.assertIn("09:35", str(first_buy.get("at") or ""), first_buy)
        self.assertAlmostEqual(float(first_buy.get("price") or 0), 100.2, places=2)
        self.assertEqual(int(first_buy.get("shares") or 0), 200)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(out.get("range_mode"), "close_band")

    def test_backtest_walk_uses_slots_and_keeps_round_qty(self):
        from core.t0.backtest import backtest_t0_on_bars

        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        mins = _am_mins(date, 18, px=_px_bts_confirm_then_rally)
        prev = _bar("2026-03-01", 99, 100, 98, 99.5)
        report = backtest_t0_on_bars(
            [prev, bar],
            initial_shares=1000,
            initial_cost=100,
            initial_cash=200000,
            rules=_slot_rules(),
            stock_code="",
            minute_by_date={date: mins},
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertTrue((report.get("rules") or {}).get("t0_slots_enabled"))
        self.assertEqual(len((report.get("rules") or {}).get("t0_slots") or []), 2)
        days = [d for d in (report.get("days") or []) if not d.get("skipped")]
        self.assertTrue(days, report)
        day = days[0]
        self.assertTrue(day.get("t0_slots_enabled"))
        self.assertTrue(day.get("t0_slot_results"))
        self.assertGreater(int(day.get("bought_qty") or 0), 0)
        # 前缀因果 ĉ 贴价后全日可混向；至少参与做 T
        self.assertGreaterEqual(int(report.get("t0_trade_days") or 0), 1)
        self.assertGreaterEqual(
            int(report.get("buy_then_sell_days") or 0)
            + int(report.get("mixed_days") or 0),
            1,
            report,
        )

    def test_close_band_break_opens_leg1_without_pullback(self):
        """v6：收价破带即在触发根收盘开第一腿，不要求回踩。"""
        date = "2026-03-02"
        bar = _bar(date, 100, 101, 99.5, 100.5)

        def px(i, hm):
            return (100.2, 100.8, 100.1, 100.4)

        mins = _am_mins(date, 14, px=px)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules=_slot_rules(direction="buy_then_sell"),
            stock_code="",
            minute_bars=mins,
            scores={"y_tau": 0.5, "y_trade": 0.4, "y_eod": 0.3, "y_path": 0.8},
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in (out.get("trades") or []) if str(t.get("side") or "").endswith("buy")]
        self.assertTrue(buys, out)
        self.assertIn("09:35", str(buys[0].get("at") or ""))
        reasons = " ".join(str(r.get("reason") or "") for r in (out.get("t0_slot_results") or []))
        self.assertNotIn("未开第一腿", reasons)

    def test_max_rounds_zero_skips_leg1(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 98, 101)
        mins = _am_mins(
            date,
            8,
            px=lambda i, hm: (100, 105, 98, 101) if i >= 3 else (100, 101, 99.5, 100.2),
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules={
                "t0_slots_max_rounds": 0,
                "direction": "sell_then_buy",
                "min_range_pct": 0.1,
                "t0_pm_degrade": "",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": False,
                "fill_mode": "trigger",
            },
            stock_code="600519",
            minute_bars=mins,
        )
        self.assertTrue(out.get("t0_slots_enabled"))
        self.assertFalse(out.get("trades"), out)

    def test_slot_dict_cannot_override_shared_strategy(self):
        from core.t0.config import load_t0_rules, normalize_t0_slots

        slots = normalize_t0_slots(
            [
                {
                    "id": "s1",
                    "hm": "09:30",
                    "prefix_bars": 0,
                    "ratio": 0.20,
                    "t0_stop_pct_buy_then_sell": 9.9,
                    "sell_trigger_pct_buy_then_sell": 8.0,
                }
            ]
        )
        self.assertEqual(set(slots[0].keys()), {"id", "hm", "prefix_bars", "ratio"})
        cfg = load_t0_rules(
            {
                "t0_stop_pct_buy_then_sell": 1.2,
                "t0_slots": [
                    {
                        "id": "s1",
                        "hm": "09:30",
                        "prefix_bars": 0,
                        "ratio": 0.20,
                        "t0_stop_pct_buy_then_sell": 9.9,
                    }
                ],
            }
        )
        self.assertAlmostEqual(float(cfg["t0_stop_pct_buy_then_sell"]), 1.2)
        self.assertNotIn("t0_leg1_hunt_pct_buy_then_sell", cfg)
        self.assertNotIn("t0_stop_pct_buy_then_sell", cfg["t0_slots"][0])

    def test_merge_keeps_uncover_exposure(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 99, 98)

        def px(i, hm):
            if i == 0:
                return (100.0, 100.5, 98.5, 99.0)
            return (99.0, 99.5, 98.0, 98.5)

        mins = _am_mins(date, 8, px=px)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules=_slot_rules(
                direction="sell_then_buy",
                t0_slots=[{"id": "s1", "hm": "09:30", "prefix_bars": 0, "ratio": 0.20}],
                must_cover_same_day_sell_then_buy=False,
                t0_stop_pct_sell_then_buy=0,
                y_path_strong=100.0,
                t0_y_τc_target_scale=10.0,
            ),
            stock_code="",
            minute_bars=mins,
            scores={
                "y_tau": -0.5,
                "y_tau_oc": -0.5,
                "y_trade": -0.1,
                "y_eod": -0.05,
                "y_τ30": 0.2,
                "y_τ45": 0.2,
                "y_τ60": 0.2,
                "y_τ75": 0.2,
                "y_τ90": 0.2,
            },
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("sold_qty") or 0), 0, out)
        self.assertGreater(int(out.get("uncovered_qty") or 0), 0)
        self.assertNotEqual(float(out.get("exposure_pnl") or 0), 0.0)
        ret = out.get("day_return_pct")
        if ret is not None:
            self.assertLess(abs(float(ret)), 20.0)

    def test_slot_tally_marks_mixed_without_dropping_pnl(self):
        from core.t0.backtest import _slot_round_tally

        tally = _slot_round_tally(
            {
                "t0_slots_enabled": True,
                "t0_slot_results": [
                    {
                        "skipped": False,
                        "direction": "sell_then_buy",
                        "sold_qty": 200,
                        "covered_qty": 200,
                        "bought_qty": 0,
                        "sold_back_qty": 0,
                        "pnl": 10,
                        "exit_reason": "cover",
                    },
                    {
                        "skipped": False,
                        "direction": "buy_then_sell",
                        "sold_qty": 0,
                        "covered_qty": 0,
                        "bought_qty": 200,
                        "sold_back_qty": 200,
                        "pnl": 20,
                        "exit_reason": "sold_back",
                    },
                ],
            }
        )
        self.assertTrue(tally)
        self.assertTrue(tally["mixed"])
        self.assertTrue(tally["saw_stb"])
        self.assertTrue(tally["saw_bts"])
        self.assertAlmostEqual(tally["stb_pnl"], 10)
        self.assertAlmostEqual(tally["bts_pnl"], 20)
        self.assertTrue(tally["all_complete"])

    def test_slot_attribution_uses_round_yhat_not_mixed_day(self):
        from core.t0.viz import build_t0_viz_payload, build_y_tau_attribution

        days = [
            {
                "date": "2026-03-02",
                "open": 100,
                "close": 101,
                "direction": "mixed",
                "sold_qty": 200,
                "bought_qty": 200,
                "pnl": 30,
                "exposure_pnl": 0,
                "t0_slots_enabled": True,
                "scores": {"y_tau": -9.9, "y_path": -9.9},
                "t0_slot_results": [
                    {
                        "id": "s1",
                        "hm": "10:00",
                        "skipped": False,
                        "direction": "buy_then_sell",
                        "bought_qty": 200,
                        "sold_qty": 0,
                        "pnl": 10,
                        "scores": {"y_tau": 0.8, "y_path": 1.2},
                    },
                    {
                        "id": "s2",
                        "hm": "10:30",
                        "skipped": False,
                        "direction": "sell_then_buy",
                        "sold_qty": 200,
                        "bought_qty": 0,
                        "pnl": 20,
                        "scores": {"y_tau": -0.6, "y_path": -1.1},
                    },
                    {
                        "id": "s3",
                        "hm": "11:00",
                        "skipped": True,
                        "reason": "正T确认根未开第一腿",
                    },
                ],
            }
        ]
        att = build_y_tau_attribution(days)
        self.assertEqual(att["traded_with_tau"], 2)
        self.assertEqual(att["hit"], 1)
        self.assertEqual(att["miss"], 1)
        viz = build_t0_viz_payload(days, stock_code="600519")
        self.assertEqual(viz.get("skip_scope"), "slot")
        self.assertEqual(viz.get("skip_round_count"), 1)
        cats = {c["id"]: c["count"] for c in (viz.get("skip_categories") or [])}
        self.assertEqual(cats.get("trigger_miss"), 1)
        dirs = {p.get("direction") for p in (viz.get("y_tau_scatter") or []) if p.get("outcome") == "traded"}
        self.assertEqual(dirs, {"buy_then_sell", "sell_then_buy"})

    def test_slot_public_scores_from_snap(self):
        from core.t0.slots import _slot_public_scores

        extra = _slot_public_scores(
            {
                "_t0_score_snap": {
                    "y_tau": 0.8,
                    "y_path": 1.2,
                },
                "direction_features": {
                    "y_tau": 0.8,
                    "gap_pct": 0.1,
                },
            }
        )
        self.assertAlmostEqual(extra["scores"]["y_tau"], 0.8)
        self.assertNotIn("y_hl", extra["scores"])
        self.assertAlmostEqual(extra["direction_features"]["gap_pct"], 0.1)

    def test_promote_causal_portrait_fields(self):
        from core.t0.slots import _promote_causal_portrait_fields

        out = _promote_causal_portrait_fields(
            {
                "_score_source": "prefix_causal",
                "y_tau_oc": 1.2,
                "y_path": -0.5,
            }
        )
        self.assertAlmostEqual(out["y_tau_portrait_oc"], 1.2)
        self.assertNotIn("y_hl_portrait", out)
        self.assertNotIn("y_path", out)

    def test_attach_slot_fit_portrait_from_causal_snap(self):
        """跳过轮也要写入该钟因果 ŷ_τ 画像分（拟合 by_tau 口径）。"""
        from core.t0.slots import attach_slot_fit_portrait_scores

        mins = []
        for i in range(6):
            hh = 9
            mm = 30 + i * 5
            if mm >= 60:
                hh = 10
                mm -= 60
            mins.append(
                {
                    "datetime": f"2026-03-01 {hh:02d}:{mm:02d}:00",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.0 + i * 0.01,
                }
            )
        out = attach_slot_fit_portrait_scores(
            {
                "skipped": True,
                "reason": "dual_y：横盘",
                "_t0_score_snap": {
                    "_score_source": "prefix_causal",
                    "y_tau_oc": 0.7,
                    "y_tau": 0.7,
                    "y_path": 1.1,
                    "y_eod": 0.5,
                },
            },
            slot={"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.2},
            minute_bars=mins,
            bar={"date": "2026-03-01", "open": 10.0, "close": 10.2, "prev_close": 10.0},
        )
        sc = out.get("scores") or {}
        self.assertIsNotNone(sc.get("y_tau_portrait_oc"))
        self.assertIsNone(sc.get("y_hl_portrait"))
        self.assertAlmostEqual(float(sc["y_tau_portrait_oc"]), 0.7, places=3)

    def test_backtest_all_miss_day_keeps_portrait_scores(self):
        """多轮均未成交时，日级仍须保留因果 ŷ 画像。"""
        from core.t0.backtest import backtest_t0_on_bars
        from unittest import mock

        date = "2026-03-02"
        bar = _bar(date, 100, 100.5, 99.5, 100.2)

        def px(i, hm):
            return (100.0, 100.15, 99.9, 100.05)

        mins = _am_mins(date, 30, px=px)
        prev = _bar("2026-03-01", 99, 100, 98, 99.5)
        rules = _slot_rules(
            direction="buy_then_sell",
            t0_close_band_delta_pct=10.0,
            y_enter_enabled=False,
            y_enter_alt_enabled=False,
            min_range_pct=0.0,
            min_range_pct_buy_then_sell=0.0,
            min_range_pct_sell_then_buy=0.0,
        )
        fake_scores = {
            "y_tau": 1.2,
            "y_tau_oc": 1.2,
            "y_path": 0.8,
            "y_eod": 0.5,
            "y_trade": 0.4,
            "_score_source": "prefix_causal",
        }
        with mock.patch(
            "core.t0.score_policy.resolve_scores_for_code",
            return_value=dict(fake_scores),
        ), mock.patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=dict(fake_scores),
        ):
            report = backtest_t0_on_bars(
                [prev, bar],
                initial_shares=1000,
                initial_cost=100,
                initial_cash=200000,
                rules=rules,
                stock_code="600519",
                minute_by_date={date: mins},
            )
        self.assertTrue(report.get("success"), report.get("error"))
        days = [d for d in (report.get("days") or []) if str(d.get("date") or "") == date]
        self.assertTrue(days, report.get("days"))
        day0 = days[0]
        self.assertTrue(day0.get("skipped"), day0)
        sc = day0.get("scores") or {}
        self.assertIsNotNone(sc.get("y_tau_portrait_oc"), sc)
        self.assertIsNone(sc.get("y_hl_portrait"), sc)

    def test_slot_meta_extra_stamps_hm(self):
        from core.t0.slots import _slot_meta_extra

        extra = _slot_meta_extra({"id": "s2", "hm": "10:30"})
        self.assertEqual(extra["t0_slot"], "s2")
        self.assertEqual(extra["t0_slot_hm"], "10:30")


    def test_settle_drops_later_round_keeps_first_buy(self):
        """正T三轮回补超过可卖时，丢掉最后一轮，09:40 第一笔留下。"""
        from core.t0.slots import _settle_tagged_slot_legs

        tagged = [
            ("2026-08-24 09:40:00", {"side": "buy", "shares": 4000, "net_cash_delta": -346000}, "r1"),
            ("2026-08-24 09:45:00", {"side": "buy", "shares": 5600, "net_cash_delta": -486000}, "r2"),
            ("2026-08-24 09:50:00", {"side": "buy", "shares": 3900, "net_cash_delta": -337000}, "r3"),
            ("2026-08-24 13:25:00", {"side": "sell", "shares": 3900, "net_cash_delta": 337000}, "r3"),
            ("2026-08-24 14:35:00", {"side": "sell", "shares": 5600, "net_cash_delta": 480000}, "r2"),
            ("2026-08-24 14:35:00", {"side": "sell", "shares": 4000, "net_cash_delta": 342000}, "r1"),
        ]
        dropped, _cash, _shares = _settle_tagged_slot_legs(
            tagged,
            cash=2_000_000,
            shares=10000,
            sellable_old=10000,
        )
        self.assertEqual(dropped, {"r3"})

    def test_merge_respects_sellable_below_shares(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        mins = _am_mins(date, 14, px=_px_stb_confirm_then_drop)
        rules = _slot_rules(
            t0_slots=[
                {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.50},
                {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.50},
            ],
            direction="sell_then_buy",
            must_cover_same_day_sell_then_buy=False,
            t0_stop_pct_sell_then_buy=0,
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=300,
            rules=rules,
            stock_code="600519",
            minute_bars=mins,
        )
        rows = {r["id"]: r for r in (out.get("t0_slot_results") or [])}
        sold_total = sum(int(r.get("sold_qty") or 0) for r in rows.values())
        self.assertLessEqual(sold_total, 300, rows)

    def test_cash_reserved_across_buy_rounds(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        def px(i, hm):
            if i < 5:
                return (99.8, 100.5, 99.0, 100.2)
            if i == 5:
                return (100.0, 100.6, 99.8, 100.4)
            if i == 11:
                return (99.8, 100.5, 99.5, 100.3)
            return (100.0, 100.15, 99.8, 100.1)

        mins = _am_mins(date, 14, px=px)
        rules = _slot_rules(
            t0_slots=[
                {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.50},
                {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.50},
            ],
            direction="buy_then_sell",
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=21000,
            sellable_shares=1000,
            rules=rules,
            stock_code="",
            minute_bars=mins,
            defer_eod=True,
            scores={
                "y_tau": 0.5,
                "y_τ30": 0.8, "y_t30": 0.8, "y_τ45": 0.8, "y_t45": 0.8,
                "y_τ60": 0.8, "y_t60": 0.8, "y_τ75": 0.8, "y_t75": 0.8,
                "y_τ90": 0.8, "y_t90": 0.8,
            },
        )
        rows = out.get("t0_slot_results") or []
        filled = [r for r in rows if int(r.get("bought_qty") or 0) > 0]
        blocked = [
            r
            for r in rows
            if r.get("skipped")
            and (
                "未落账" in str(r.get("reason") or "")
                or "缺现金" in str(r.get("reason") or "")
                or "现金" in str(r.get("reason") or "")
            )
        ]
        self.assertGreaterEqual(len(filled), 1, rows)
        self.assertGreaterEqual(len(blocked), 1, rows)
        self.assertLessEqual(int(out.get("bought_qty") or 0), 200, out)


if __name__ == "__main__":
    unittest.main()
