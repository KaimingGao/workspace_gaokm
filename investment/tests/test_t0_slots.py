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
        "y_prefix_segment_enabled": False,
        "y_prefix_segment_enabled_buy_then_sell": False,
        "y_tau_entry_price_skip": False,
        "min_range_pct": 0.1,
        "min_range_pct_buy_then_sell": 0.1,
        "t0_pm_degrade": "",
        "t0_pm_degrade_buy_then_sell": "",
        "t0_stop_pct_buy_then_sell": 0,
        "t0_stop_pct_sell_then_buy": 0,
        "must_cover_same_day_buy_then_sell": False,
        "fill_mode": "trigger",
        "lot_size": 100,
        "ref": "open",
        "path_mode": "first_touch",
        "use_atr": False,
    }
    base.update(kwargs)
    return base


class TestT0Slots(unittest.TestCase):
    def test_default_four_slots_until_1130(self):
        cfg = load_t0_rules()
        self.assertTrue(t0_slots_enabled(cfg))
        hms = [s["hm"] for s in cfg["t0_slots"]]
        self.assertEqual(hms, ["10:00", "10:30", "11:00", "11:30"])
        self.assertEqual(cfg["t0_slots"][-1]["prefix_bars"], 24)
        self.assertEqual(cfg["t0_slots"][0]["prefix_bars"], 6)
        self.assertAlmostEqual(float(cfg["t0_slots"][0]["ratio"]), 0.15)
        self.assertAlmostEqual(sum(float(s["ratio"]) for s in cfg["t0_slots"]), 0.60)

    def test_legacy_five_open_slots_migrate(self):
        cfg = load_t0_rules(
            {
                "t0_slots": [
                    {"id": "s1", "hm": "09:30", "prefix_bars": 0, "ratio": 0.20},
                    {"id": "s2", "hm": "10:00", "prefix_bars": 6, "ratio": 0.20},
                    {"id": "s3", "hm": "10:30", "prefix_bars": 12, "ratio": 0.20},
                    {"id": "s4", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
                    {"id": "s5", "hm": "11:30", "prefix_bars": 24, "ratio": 0.20},
                ]
            }
        )
        self.assertEqual(
            [s["hm"] for s in cfg["t0_slots"]],
            ["10:00", "10:30", "11:00", "11:30"],
        )
        self.assertAlmostEqual(float(cfg["t0_slots"][0]["ratio"]), 0.15)

    def test_legacy_six_pm_slots_migrate(self):
        cfg = load_t0_rules(
            {
                "t0_slots": [
                    {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.15},
                    {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.15},
                    {"id": "s3", "hm": "11:00", "prefix_bars": 18, "ratio": 0.15},
                    {"id": "s4", "hm": "11:30", "prefix_bars": 24, "ratio": 0.15},
                    {"id": "s5", "hm": "13:00", "prefix_bars": 25, "ratio": 0.15},
                    {"id": "s6", "hm": "14:00", "prefix_bars": 37, "ratio": 0.15},
                ]
            }
        )
        self.assertEqual(
            [s["hm"] for s in cfg["t0_slots"]],
            ["10:00", "10:30", "11:00", "11:30"],
        )

    def test_pm_slot_stripped_from_custom_list(self):
        from core.t0.config import normalize_t0_slots

        slots = normalize_t0_slots(
            [
                {"id": "s4", "hm": "11:30", "prefix_bars": 24, "ratio": 0.20},
                {"id": "s5", "hm": "13:00", "prefix_bars": 25, "ratio": 0.20},
            ]
        )
        self.assertEqual([s["hm"] for s in slots], ["11:30"])

    def test_buy_then_sell_fills_at_decision_confirm_close(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        def px(i, hm):
            # 0-4 前缀；5=10:00 确认根收 100 → 第一腿；之后冲高卖第二腿
            if i < 5:
                return (100.2, 100.5, 100.1, 100.3)
            if i == 5:
                return (100.3, 100.6, 100.0, 100.0)
            if i >= 8:
                return (99.5, 104.0, 99.0, 103.0)
            return (99.0, 100.0, 98.8, 99.5)

        mins = _am_mins(date, 14, px=px)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules=_slot_rules(),
            stock_code="600519",
            minute_bars=mins,
        )
        self.assertTrue(out.get("success"), out)
        trades = out.get("trades") or []
        buys = [t for t in trades if str(t.get("side") or "").endswith("buy")]
        self.assertTrue(buys, out)
        first_buy = buys[0]
        self.assertIn("10:00", str(first_buy.get("at") or ""), first_buy)
        self.assertAlmostEqual(float(first_buy.get("price") or 0), 100.0, places=2)
        self.assertEqual(int(first_buy.get("shares") or 0), 200)
        rows = {r["id"]: r for r in (out.get("t0_slot_results") or [])}
        # 09:30 轮无前缀：确认根=首根；本夹具首根未形成可卖/可买条件时可能跳过
        self.assertGreater(int(rows.get("s2", {}).get("trades") or 0), 0)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(str(first_buy.get("t0_slot") or ""), "s1")
        self.assertEqual(out.get("range_mode"), "slot_confirm")

    def test_backtest_walk_uses_slots_and_keeps_round_qty(self):
        from core.t0.backtest import backtest_t0_on_bars

        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        def px(i, hm):
            if i < 5:
                return (100.2, 100.5, 100.1, 100.3)
            if i == 5:
                return (100.3, 100.6, 100.0, 100.0)
            if i >= 8:
                return (99.5, 104.0, 99.0, 103.0)
            return (99.0, 100.0, 98.8, 99.5)

        mins = _am_mins(date, 18, px=px)
        prev = _bar("2026-03-01", 99, 100, 98, 99.5)
        report = backtest_t0_on_bars(
            [prev, bar],
            initial_shares=1000,
            initial_cost=100,
            initial_cash=200000,
            rules=_slot_rules(),
            stock_code="600519",
            minute_by_date={date: mins},
            compare_optimistic=False,
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
        self.assertGreaterEqual(int(report.get("buy_then_sell_days") or 0), 1)

    def test_confirm_fill_without_pullback_still_opens_leg1(self):
        """无 hunt：确认根收盘即可开第一腿，不要求回踩。"""
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
            stock_code="600519",
            minute_bars=mins,
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in (out.get("trades") or []) if str(t.get("side") or "").endswith("buy")]
        self.assertTrue(buys, out)
        self.assertIn("10:00", str(buys[0].get("at") or ""))
        reasons = " ".join(str(r.get("reason") or "") for r in (out.get("t0_slot_results") or []))
        self.assertNotIn("未开第一腿", reasons)

    def test_slots_off_keeps_confirm_close_single_round(self):
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
                "t0_slots_enabled": False,
                "direction": "sell_then_buy",
                "y_path_abandon_bars": 4,
                "y_prefix_downbar_ratio_sell_then_buy": 0.0,
                "min_range_pct": 0.1,
                "t0_pm_degrade": "",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": False,
                "fill_mode": "trigger",
            },
            stock_code="600519",
            minute_bars=mins,
        )
        self.assertFalse(out.get("t0_slots_enabled"))
        self.assertNotIn("t0_slot_results", out or {})

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

    def test_prefix_bars_are_open_to_clock(self):
        from core.t0.slots import _slot_prefix_bars

        date = "2026-03-02"
        mins = _am_mins(date, 24, px=lambda i, hm: (100 + i, 101 + i, 99, 100.5))
        win = _slot_prefix_bars(mins, 18)
        self.assertEqual(len(win), 18)
        self.assertEqual(win[0]["datetime"], mins[0]["datetime"])
        self.assertEqual(win[-1]["datetime"], mins[17]["datetime"])
        self.assertEqual(_slot_prefix_bars(mins, 6), mins[:6])
        self.assertEqual(_slot_prefix_bars(mins, 0), [])

    def test_slot_tau_entry_price_gate(self):
        """确认根买价未低于 open×(1+ŷ_τ×裕度) 则本轮跳过。"""
        date = "2026-03-02"
        bar = _bar(date, 100, 120, 80, 101)

        def px(i, hm):
            # 确认根 close=108；ŷ_τ=0.5、裕度=1 → bound=100.5
            if i < 23:
                return (100.0, 101.0, 99.0, 100.2)
            if i == 23:
                return (100.2, 109.0, 100.0, 108.0)
            return (108.0, 108.5, 107.0, 107.5)

        mins = _am_mins(date, 26, px=px)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            sellable_shares=1000,
            rules=_slot_rules(
                t0_slots=[
                    {"id": "s5", "hm": "11:30", "prefix_bars": 24, "ratio": 0.20},
                ],
                y_tau_entry_price_skip=True,
                y_tau_entry_price_mult=1.0,
                y_prefix_segment_enabled=False,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            stock_code="600519",
            minute_bars=mins,
            scores={"y_path": 5.0, "y_tau": 0.5, "y_trade": 0.4},
        )
        self.assertTrue(out.get("success"), out)
        rows = {r["id"]: r for r in (out.get("t0_slot_results") or [])}
        s5 = rows.get("s5") or {}
        reason = str(s5.get("reason") or "")
        self.assertTrue(
            s5.get("skipped") or "买价" in reason or "τ" in reason,
            reason,
        )

    def test_merge_keeps_uncover_exposure(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 99, 98)

        def px(i, hm):
            if i == 0:
                return (100.0, 101.5, 99.9, 101.2)
            return (101.0, 101.2, 100.5, 99.0)

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
                must_cover_same_day=False,
                t0_stop_pct_sell_then_buy=0,
            ),
            stock_code="600519",
            minute_bars=mins,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertGreater(int(out.get("uncovered_qty") or 0), 0)
        self.assertNotEqual(float(out.get("exposure_pnl") or 0), 0.0)
        # 未回补不得把卖出额记成已实现 PnL
        self.assertLess(abs(float(out.get("pnl") or 0)), 1.0)
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
        self.assertEqual(att["by_direction"]["buy_then_sell"]["n"], 1)
        self.assertEqual(att["by_direction"]["sell_then_buy"]["n"], 1)
        self.assertAlmostEqual(att["by_direction"]["buy_then_sell"]["pnl"], 10)
        self.assertAlmostEqual(att["by_direction"]["sell_then_buy"]["pnl"], 20)
        taus = [round(float(s["y_tau"]), 1) for s in att["samples"]]
        self.assertEqual(sorted(taus), [-0.6, 0.8])
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
                "_t0_score_snap": {"y_tau": 0.8, "y_path": 1.2},
                "direction_features": {"y_tau": 0.8, "gap_pct": 0.1},
            }
        )
        self.assertAlmostEqual(extra["scores"]["y_tau"], 0.8)
        self.assertAlmostEqual(extra["scores"]["y_path"], 1.2)
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
        self.assertAlmostEqual(out["y_path_portrait"], -0.5)

    def test_attach_slot_fit_portrait_from_causal_snap(self):
        """跳过轮也要写入该钟因果画像分（拟合 by_tau 口径）。"""
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
        self.assertIsNotNone(sc.get("y_path_portrait"))
        self.assertAlmostEqual(float(sc["y_tau_portrait_oc"]), 0.7, places=3)
        self.assertAlmostEqual(float(sc["y_path_portrait"]), 1.1, places=3)

    def test_backtest_skip_day_keeps_slot_scores(self):
        """跳过日也必须落下 t0_slot_results（含因果 ŷ），否则分槽画像覆盖≈0。"""
        from core.t0.backtest import backtest_t0_on_bars
        from unittest import mock

        date = "2026-03-02"
        bar = _bar(date, 100, 100.5, 99.5, 100.2)

        def px(i, hm):
            return (100.0, 100.15, 99.9, 100.05)

        mins = _am_mins(date, 30, px=px)
        prev = _bar("2026-03-01", 99, 100, 98, 99.5)
        rules = _slot_rules(
            direction="dual_y",
            # 高入场门槛 → 选向跳过，但前缀仍应算分
            y_tau_enter=50.0,
            y_path_enter=50.0,
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
                compare_optimistic=False,
            )
        self.assertTrue(report.get("success"), report.get("error"))
        skipped = [
            d
            for d in (report.get("days") or [])
            if d.get("skipped") and d.get("minute_path")
        ]
        self.assertTrue(skipped, report.get("days"))
        day0 = skipped[0]
        rows = day0.get("t0_slot_results") or []
        self.assertTrue(rows, "skip day must retain t0_slot_results")
        scored = [
            r
            for r in rows
            if isinstance((r or {}).get("scores"), dict)
            and (
                (r["scores"].get("y_tau_portrait_oc") is not None)
                or (r["scores"].get("y_tau_oc") is not None)
                or (r["scores"].get("y_path_portrait") is not None)
                or (r["scores"].get("y_path") is not None)
                or (r["scores"].get("y_tau") is not None)
            )
        ]
        self.assertTrue(scored, rows)

    def test_slot_meta_extra_stamps_hm(self):
        from core.t0.slots import _slot_meta_extra

        extra = _slot_meta_extra({"id": "s2", "hm": "10:30"})
        self.assertEqual(extra["t0_slot"], "s2")
        self.assertEqual(extra["t0_slot_hm"], "10:30")

    def test_allocate_slot_slices_redistributes_remainder(self):
        from core.t0.slots import allocate_slot_slices

        slots = [
            {"ratio": 0.20},
            {"ratio": 0.20},
            {"ratio": 0.20},
            {"ratio": 0.20},
        ]
        slices = allocate_slot_slices(1000, 350, slots, 100)
        self.assertEqual(sum(slices), 300.0)
        self.assertTrue(all(s >= 100 for s in slices if s > 0))

    def test_merge_respects_sellable_below_shares(self):
        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)

        def px(i, hm):
            if i < 5:
                return (100.2, 100.5, 100.1, 100.3)
            if i == 5:
                return (100.3, 100.6, 100.0, 100.0)
            if i >= 8:
                return (99.5, 104.0, 99.0, 103.0)
            return (99.0, 100.0, 98.8, 99.5)

        mins = _am_mins(date, 14, px=px)
        rules = _slot_rules(
            t0_slots=[
                {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.50},
                {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.50},
            ],
            direction="sell_then_buy",
            y_prefix_segment_enabled_sell_then_buy=False,
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
                return (100.2, 100.5, 100.1, 100.3)
            if i == 5:
                return (100.3, 100.6, 100.0, 100.0)
            # 第二腿触价抬高：10:00 买后不平，10:30 第二轮不应再占用现金
            return (100.0, 100.15, 99.8, 99.9)

        mins = _am_mins(date, 14, px=px)
        rules = _slot_rules(
            t0_slots=[
                {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.50},
                {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.50},
            ],
            direction="buy_then_sell",
            y_tau_entry_price_skip=False,
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=21000,
            sellable_shares=1000,
            rules=rules,
            stock_code="600519",
            minute_bars=mins,
            defer_eod=True,
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
            )
        ]
        self.assertEqual(len(filled), 1, rows)
        self.assertEqual(len(blocked), 1, rows)

    def test_missing_y_tau_blocks_leg1_when_required(self):
        from unittest import mock

        from core.t0.minute_path import tau_leg1_fill_price_ok

        gate = tau_leg1_fill_price_ok(
            fill_px=100.0,
            ref=100.0,
            y_tau=None,
            direction="buy_then_sell",
            cfg={"y_tau_entry_price_skip": True, "y_tau_require_for_leg1": True},
        )
        self.assertFalse(gate.get("ok"))
        self.assertIn("缺ŷ_τ", str(gate.get("reason") or ""))

        date = "2026-03-02"
        bar = _bar(date, 100, 105, 97, 101)
        mins = _am_mins(date, 14, px=lambda i, hm: (100.2, 100.5, 100.1, 100.3))
        with mock.patch("core.t0.minute_path._score_y_tau", return_value=None):
            out = simulate_t0_day(
                bar=bar,
                shares=1000,
                cost=100,
                cash=200000,
                sellable_shares=1000,
                rules=_slot_rules(
                    direction="buy_then_sell",
                    y_tau_require_for_leg1=True,
                    y_tau_entry_price_skip=True,
                    y_prefix_segment_enabled_buy_then_sell=False,
                ),
                stock_code="600519",
                minute_bars=mins,
            )
        reasons = " ".join(str(r.get("reason") or "") for r in (out.get("t0_slot_results") or []))
        self.assertIn("缺ŷ_τ", reasons, reasons)
        self.assertFalse(out.get("trades"), out)


if __name__ == "__main__":
    unittest.main()
