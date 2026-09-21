"""v6 收盘带宽选腿单元与路径烟测。"""

from __future__ import annotations

import unittest

from core.t0.close_band import (
    band_decision,
    band_delta_px,
    day_open_prev_close,
    estimate_close_px,
    freeze_round,
    hm_allows_leg1,
    parse_bar_hm,
)
from core.t0.config import T0_LAST_LEG1_HM, load_t0_rules
from core.t0.slots import simulate_t0_day_slots


def _ytw_heads(up=True):
    p = 0.8 if up else 0.2
    return {
        "y_τ30": p,
        "y_t30": p,
        "y_τ45": p,
        "y_t45": p,
        "y_τ60": p,
        "y_t60": p,
        "y_τ75": p,
        "y_t75": p,
        "y_τ90": p,
        "y_t90": p,
    }


def _snap(y_tau, **extra):
    """选腿夹具：ŷ_τw 随 y_tau 符号定正/反 T。"""
    up = float(y_tau) >= 0
    out = {
        "y_tau": float(y_tau),
        "y_tau_oc": float(y_tau),
        "y_trade": 0.0,
        "y_nowcast": 0.0,
        "y_path": 1.0,
        "nowcast_vs": "open",
    }
    out.update(_ytw_heads(up=up))
    out.update(extra)
    return out


def _cfg(overrides=None):
    """路径单测关掉 TPD 入场闸；生产默认 y_tpd_max=1.00≈关。"""
    d = dict(overrides or {})
    d.setdefault("y_tpd_max", 1.0)
    d.setdefault("t0_lock_win_pct_buy_then_sell", 0)
    d.setdefault("t0_lock_win_pct_sell_then_buy", 0)
    d.setdefault("y_oc_enter", 0)
    d.setdefault("y_oc_strong", 0)
    d.setdefault("y_oc_enter_amount", 0)
    d.setdefault("y_oc_strong_amount", 0)
    return load_t0_rules(d)


def _mins(closes, *, session_open=100.0):
    """合成分钟线；首根 open=session_open，其后 open=前收。"""
    out = []
    h, m = 9, 35
    o0 = float(session_open)
    prev = o0
    for i, c in enumerate(closes):
        px = float(c)
        o = o0 if i == 0 else prev
        out.append(
            {
                "datetime": f"2025-01-02 {h:02d}:{m:02d}:00",
                "open": o,
                "high": max(o, px) + 0.25,
                "low": min(o, px) - 0.25,
                "close": px,
                "prev_close": o0,
            }
        )
        prev = px
        m += 5
        if m >= 60:
            h += 1
            m -= 60
        if h == 11 and m > 30:
            h, m = 13, 0
    return out


class TestCloseBandCore(unittest.TestCase):
    def test_anchors_prefer_minute_session(self):
        """做 T 锚：分钟首 open / 末 close 优先于日线（同价空间）。"""
        day = {"open": 10.0, "close": 11.0, "prev_close": 9.5}
        mins = [
            {"datetime": "2025-01-02 09:35:00", "open": 10.2, "close": 10.3, "prev_close": 9.8},
            {"datetime": "2025-01-02 14:55:00", "open": 10.8, "close": 10.9},
        ]
        a = day_open_prev_close(day, mins)
        self.assertAlmostEqual(a["open_px"], 10.2, places=4)
        self.assertAlmostEqual(a["session_close"], 10.9, places=4)
        self.assertAlmostEqual(a["prev_close"], 9.8, places=4)
        # 无分钟 → 日线
        b = day_open_prev_close(day, [])
        self.assertAlmostEqual(b["open_px"], 10.0, places=4)
        self.assertAlmostEqual(b["session_close"], 11.0, places=4)
        self.assertAlmostEqual(b["prev_close"], 9.5, places=4)

    def test_estimate_mean_and_band(self):
        sc = {
            "y_tau": 1.0,
            "y_trade": -1.0,
            "y_nowcast": 0.0,
            "nowcast_vs": "open",
        }
        est = estimate_close_px(sc, open_px=100.0, prev_close=100.0)
        self.assertTrue(est["ok"])
        # C_τ = 100×(1+clip(1×2, ±20)/100) = 102
        self.assertAlmostEqual(est["close_px"], 102.0, places=4)
        self.assertAlmostEqual(est["c_tau"], 102.0, places=4)
        self.assertAlmostEqual(est["c_oc"], 101.0, places=4)
        self.assertIsNone(est["c_trade"])
        self.assertIsNone(est["c_nowcast"])
        self.assertEqual(est["n_sources"], 1)
        d = band_delta_px(100.0, 0.5)
        self.assertAlmostEqual(d, 0.5, places=4)
        self.assertEqual(band_decision(100.6, 100.0, d), "sell_then_buy")
        self.assertEqual(band_decision(99.4, 100.0, d), "buy_then_sell")
        self.assertIsNone(band_decision(100.0, 100.0, d))

    def test_estimate_tau_only_ignores_other_heads(self):
        """trade/nc 即使很大也不进 ĉ。"""
        sc = {
            "y_tau": 0.0,
            "y_trade": 50.0,
            "y_nowcast": 10.0,
            "nowcast_vs": "prev_close",
            "predicted_score_blend_vs": "prev_close",
        }
        est = estimate_close_px(sc, open_px=100.0, prev_close=90.0)
        self.assertAlmostEqual(est["c_tau"], 100.0, places=4)
        self.assertAlmostEqual(est["close_px"], 100.0, places=4)
        self.assertEqual(est["n_sources"], 1)

    def test_estimate_requires_y_tau(self):
        est = estimate_close_px({"y_trade": 1.0}, open_px=100.0, prev_close=90.0)
        self.assertFalse(est["ok"])
        self.assertEqual(est["n_sources"], 0)

    def test_freeze_c_tau_as_leg2_target(self):
        fr = freeze_round(
            direction="buy_then_sell",
            leg1_px=100.0,
            ratio=0.2,
            bar_index=0,
            hm="09:35",
            y_tw=2.0,
        )
        self.assertIsNone(fr.leg2_target)
        self.assertAlmostEqual(fr.leg1_px, 100.0, places=4)
        fr2 = freeze_round(
            direction="sell_then_buy",
            leg1_px=100.0,
            close_px=101.0,
            delta_px=0.5,
            ratio=0.2,
            bar_index=1,
            hm="09:40",
        )
        self.assertAlmostEqual(fr2.leg2_target, 101.0, places=4)
        self.assertAlmostEqual(fr2.close_px, 101.0, places=4)
        self.assertAlmostEqual(fr2.delta_px, 0.5, places=4)

    def test_c_tau_clip_formula_and_multiplicative_band(self):
        from core.t0.close_band import close_band_pick_direction, clip_y_oc_target_pct

        # ŷ_oc=-1.12% ×2 = −2.24，未顶到 ±20；旧 l/u 入参忽略
        self.assertAlmostEqual(clip_y_oc_target_pct(-1.12), -2.24, places=6)
        self.assertAlmostEqual(
            clip_y_oc_target_pct(-1.12, y_oc_l=-3.0, y_oc_u=3.0), -2.24, places=6
        )
        est = estimate_close_px({"y_tau": -1.12}, open_px=25.300)
        self.assertAlmostEqual(est["c_tau"], 25.300 * (1.0 - 0.0224), places=4)
        # ŷ_oc=0.1% ×2 = 0.2 → 未饱和
        est2 = estimate_close_px({"y_tau": 0.1}, open_px=100.0)
        self.assertAlmostEqual(est2["c_tau"], 100.2, places=4)
        # 硬顶 ±20：−3×10 = −30 → −20
        self.assertAlmostEqual(clip_y_oc_target_pct(-3.0, scale=10.0), -20.0, places=6)
        c_tau = 24.541
        d, meta = close_band_pick_direction(25.276, c_tau, 3.0, {"y_tau": -1.12}, {})
        self.assertIsNone(d)
        self.assertAlmostEqual(meta["upper_pct"], 3.0, places=4)
        self.assertAlmostEqual(meta["lower_pct"], -3.0, places=4)
        d_up, _ = close_band_pick_direction(25.300, c_tau, 3.0, {"y_tau": -1.12}, {})
        self.assertEqual(d_up, "sell_then_buy")
        fr = freeze_round(
            direction="sell_then_buy",
            leg1_px=25.060,
            close_px=c_tau,
            delta_px=0.736,
            ratio=0.2,
            bar_index=0,
            hm="09:35",
        )
        self.assertAlmostEqual(fr.leg2_target, c_tau, places=4)

    def test_hm_cutoff(self):
        self.assertEqual(parse_bar_hm({"datetime": "2025-01-02 09:35:00"}), "09:35")
        self.assertEqual(T0_LAST_LEG1_HM, "11:00")
        self.assertTrue(hm_allows_leg1("11:00"))
        self.assertFalse(hm_allows_leg1("11:05"))

    def test_y_tc_sidecar_skip_reason(self):
        from core.t0.close_band import y_tc_band_agree
        from core.t0.config import load_t0_rules

        self.assertTrue(y_tc_band_agree("sell_then_buy", -0.8))
        self.assertFalse(y_tc_band_agree("sell_then_buy", 0.8))
        self.assertTrue(y_tc_band_agree("buy_then_sell", 0.8))
        self.assertFalse(y_tc_band_agree("buy_then_sell", -0.8))
        self.assertIsNone(y_tc_band_agree("sell_then_buy", 0.01))
        self.assertIsNone(y_tc_band_agree(None, -1.0))

        dropped = load_t0_rules({"y_tc_validate": False, "y_tc_strong": 0.5})
        self.assertNotIn("y_tc_strong", dropped)
        self.assertNotIn("y_tc_validate", dropped)
        self.assertNotIn("y_tc_enter", load_t0_rules({}))

    def test_bar_ytw_uses_minute_ohlc_not_daily(self):
        """选腿看本根前缀 ŷ_τw；日线 close 不同也不得替代成交价。"""
        from core.t0.slots import simulate_t0_day_slots

        closes = [100.6] + [100.0] * 30
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100.0,
            "high": 102,
            "low": 99,
            "close": 99.4,
            "prev_close": 100.0,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_buy_then_sell": 0,
                "must_cover_same_day_buy_then_sell": False,
                "t0_pm_degrade_buy_then_sell": "15:30",
            }
        )
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=1e6,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=[],
            score_snap=_snap(1.0),
        )
        self.assertEqual(out.get("direction_used"), "buy_then_sell", out)

    def test_bar_ytw_pick_direction(self):
        from core.t0.close_band import bar_ytw_pick_direction

        up = _ytw_heads(up=True)
        down = _ytw_heads(up=False)
        d0, meta0 = bar_ytw_pick_direction(100.0, 101.0, up, {})
        self.assertEqual(d0, "buy_then_sell")
        self.assertGreater(float(meta0["y_tw"]), 0)

        d1, meta1 = bar_ytw_pick_direction(100.0, 99.0, down, {})
        self.assertEqual(d1, "sell_then_buy")
        self.assertLess(float(meta1["y_tw"]), 0)

        # 收在极值也不拦：形状闸已下线
        d2, _ = bar_ytw_pick_direction(
            100.0, 100.0, up, {}, bar_low=100.0, bar_high=100.0
        )
        self.assertEqual(d2, "buy_then_sell")

        d3, _ = bar_ytw_pick_direction(100.0, 101.0, down, {})
        self.assertEqual(d3, "sell_then_buy")

        d4, meta4 = bar_ytw_pick_direction(100.0, 101.0, {}, {})
        self.assertIsNone(d4)
        self.assertIn("缺 ŷ_τw", str(meta4.get("skip") or ""))

        # 共用门槛 3 不被分侧 0 盖掉：ŷ_τw=-2 不开反T
        weak_down = {
            "y_τ30": 0.2,
            "y_τ45": 0.2,
            "y_τ60": 0.50,
            "y_τ75": 0.50,
            "y_τ90": 0.50,
        }
        d5, meta5 = bar_ytw_pick_direction(
            100.0,
            99.0,
            weak_down,
            {"y_tw_enter": 3, "y_tw_enter_sell_then_buy": 0.0},
        )
        self.assertIsNone(d5)
        self.assertIn("未过反T入场", str(meta5.get("skip") or ""))

    def test_ytw_round_shares_strong_vs_enter(self):
        from core.t0.close_band import close_band_y_oc_round_shares

        self.assertIsNone(
            close_band_y_oc_round_shares({}, y_oc=3, price=10)
        )
        self.assertIsNone(
            close_band_y_oc_round_shares(
                {"y_oc_enter_amount": 0, "y_oc_strong_amount": 0},
                y_oc=3,
                price=10,
            )
        )
        cfg = {
            "y_oc_enter": 0.5,
            "y_oc_strong": 1.0,
            "y_oc_enter_amount": 4000,
            "y_oc_strong_amount": 8000,
        }
        self.assertEqual(
            close_band_y_oc_round_shares(cfg, y_oc=0.6, price=10),
            400,
        )
        self.assertEqual(
            close_band_y_oc_round_shares(cfg, y_oc=1.2, price=10),
            800,
        )
        self.assertEqual(
            close_band_y_oc_round_shares(cfg, y_oc=0.6, price=80),
            100,
        )

    def test_y_oc_enter_skip_reason(self):
        from core.t0.close_band import (
            bar_close_band_pick_direction,
            close_band_y_oc_skip_reason,
        )

        self.assertIsNone(close_band_y_oc_skip_reason(0.2, {"y_oc_enter": 0}))
        skip = close_band_y_oc_skip_reason(0.2, {"y_oc_enter": 0.5})
        self.assertIsNotNone(skip)
        self.assertIn("未过入场", skip)
        self.assertIsNone(close_band_y_oc_skip_reason(0.5, {"y_oc_enter": 0.5}))
        up = _ytw_heads(up=True)
        d, meta = bar_close_band_pick_direction(
            100.0,
            100.0,
            {**up, "y_tau": 0.2},
            {
                "y_tw_enter": 0,
                "y_oc_enter": 0.5,
                "t0_close_band_delta_pct": 0.5,
                "t0_y_oc_target_scale": 10,
            },
            open_px=100.0,
            scale=1.0,
        )
        self.assertIsNone(d)
        self.assertIn("未过入场", str(meta.get("skip") or ""))

    def test_bar_oc_gate_no_longer_blocks(self):
        """阴阳门槛已下线：阴线 + 正 ŷ_τw 仍可开正T。"""
        from core.t0.close_band import (
            bar_ytw_pick_direction,
            blend_y_tw_realized,
        )
        from core.t0.config import load_t0_rules
        from core.t0.viz import classify_t0_skip_reason

        self.assertNotIn("t0_bar_oc_gate", load_t0_rules())
        up = _ytw_heads(up=True)
        down = _ytw_heads(up=False)
        leftover = {"t0_bar_oc_gate": True}

        d_yin, _ = bar_ytw_pick_direction(100.0, 99.0, up, leftover)
        self.assertEqual(d_yin, "buy_then_sell")
        d_yang, _ = bar_ytw_pick_direction(100.0, 101.0, down, leftover)
        self.assertEqual(d_yang, "sell_then_buy")
        d_doji, _ = bar_ytw_pick_direction(100.0, 100.0, up, leftover)
        self.assertEqual(d_doji, "buy_then_sell")

        self.assertEqual(classify_t0_skip_reason("阴阳门槛：正T须收>开"), "bar_oc")
        self.assertAlmostEqual(blend_y_tw_realized(0.2, 0.1, 0.3, 0.05, 0.08), 5.0)
        self.assertAlmostEqual(blend_y_tw_realized(-0.2, 0.1, -0.3), -1.0)
        self.assertIsNone(blend_y_tw_realized(None, None, None))

    def test_ytw_prefix_confirm_dropped(self):
        from core.t0.close_band import (
            bar_close_band_pick_direction,
            bar_ytw_pick_direction,
            blend_y_tw,
        )
        from core.t0.config import load_t0_rules

        cfg0 = load_t0_rules()
        self.assertNotIn("t0_ytw_prefix_confirm", cfg0)
        self.assertAlmostEqual(float(cfg0["y_tw_midpoint"]), 47.0)
        self.assertAlmostEqual(float(cfg0["t0_close_band_delta_pct"]), 0.5)

        up = _ytw_heads(up=True)
        leftover = {
            "t0_ytw_prefix_confirm": True,
            "t0_ytw_prefix_lookback": 3,
            "t0_ytw_prefix_min_hit_pct": 67,
            "y_tw_enter": 0,
        }
        d_off, _ = bar_ytw_pick_direction(100.0, 101.0, up, leftover)
        self.assertEqual(d_off, "buy_then_sell")

        # 中位点 47%：0.48 弃权（|0.48-0.47|<5pp），0.53 投票 +
        self.assertAlmostEqual(blend_y_tw(0.53, 0.53, 0.53, 0.53, 0.53), 5.0)
        self.assertAlmostEqual(blend_y_tw(0.50, 0.50, 0.50, 0.50, 0.50), 0.0)

        # ŷ_oc 破带：open=100, ŷ_oc=+1%×默认 2 → C_τ=102；C=100 < lower → 正T
        d_band, meta_band = bar_close_band_pick_direction(
            100.0,
            100.0,
            {**up, "y_tau": 1.0},
            {"y_tw_enter": 0, "t0_close_band_delta_pct": 0.5},
            open_px=100.0,
            scale=1.0,
        )
        self.assertEqual(d_band, "buy_then_sell")
        self.assertAlmostEqual(float(meta_band["c_tau"]), 102.0, places=4)

    def test_leg1_close_extreme_dropped(self):
        from core.t0.close_band import bar_close_band_pick_direction
        from core.t0.config import load_t0_rules
        from core.t0.viz import classify_t0_skip_reason

        up = _ytw_heads(up=True)
        down = _ytw_heads(up=False)
        leftover = {
            "y_tw_enter": 0,
            "t0_close_band_delta_pct": 0.5,
            "t0_leg1_close_extreme": True,
        }
        d_pos, meta_pos = bar_close_band_pick_direction(
            100.0,
            100.0,
            {**up, "y_tau": 1.0},
            leftover,
            open_px=100.0,
            scale=1.0,
            bar_high=100.5,
            bar_low=99.5,
        )
        self.assertEqual(d_pos, "buy_then_sell")
        self.assertNotIn("须收在最高", str(meta_pos.get("skip") or ""))

        d_rev, meta_rev = bar_close_band_pick_direction(
            100.0,
            100.0,
            {**down, "y_tau": -1.0},
            leftover,
            open_px=100.0,
            scale=1.0,
            bar_high=100.5,
            bar_low=99.5,
        )
        self.assertEqual(d_rev, "sell_then_buy")
        self.assertNotIn("须收在最低", str(meta_rev.get("skip") or ""))

        self.assertNotIn("t0_leg1_close_extreme", load_t0_rules())
        self.assertNotIn("t0_leg1_close_extreme", load_t0_rules({"t0_leg1_close_extreme": True}))
        self.assertEqual(classify_t0_skip_reason("正T须收在最高"), "bar_shape")
        self.assertEqual(classify_t0_skip_reason("反T须收在最低"), "bar_shape")

    def test_score_portrait_y_tw_hit(self):
        from core.t0.viz import _build_score_portrait_from_units

        units = [
            {
                "date": "2026-03-02",
                "scores": {"y_τ30": 0.8, "y_τ45": 0.7, "y_τ60": 0.65, "y_τ75": 0.6, "y_τ90": 0.55},
                "y_t30_realized": 0.4,
                "y_t45_realized": 0.3,
                "y_t60_realized": 0.2,
                "y_t75_realized": 0.1,
                "y_t90_realized": 0.05,
            },
            {
                "date": "2026-03-03",
                "scores": {"y_τ30": 0.8, "y_τ45": 0.7, "y_τ60": 0.65, "y_τ75": 0.6, "y_τ90": 0.55},
                "y_t30_realized": -0.4,
                "y_t45_realized": -0.3,
                "y_t60_realized": -0.2,
                "y_t75_realized": -0.1,
                "y_t90_realized": -0.05,
            },
        ]
        port = _build_score_portrait_from_units(units)
        self.assertEqual(port["y_tw_hit"]["hit"], 1)
        self.assertEqual(port["y_tw_hit"]["miss"], 1)
        self.assertAlmostEqual(float(port["y_tw_hit"]["hit_rate_pct"]), 50.0)

    def test_price_space_scale_and_gate(self):
        from core.t0.close_band import (
            map_close_px_to_minute,
            price_space_scale,
            resolve_t0_price_space,
        )

        self.assertAlmostEqual(price_space_scale(101.0, 100.0), 1.01, places=6)
        day = {"open": 101.0, "prev_close": 100.0, "close": 102.0}
        mins = [
            {"datetime": "2025-01-02 09:35:00", "open": 100.0, "close": 100.5, "prev_close": 99.01},
        ]
        # 开盘差/昨收差均在阈内 → 过闸
        ok = resolve_t0_price_space(
            day, mins, {"t0_price_space_max_dev_pct": 2.0, "t0_price_space_prev_dev_pct": 2.0}
        )
        self.assertIsNone(ok.get("skip_reason"))
        self.assertAlmostEqual(ok["scale"], 1.01, places=4)
        self.assertEqual(ok["estimate_mode"], "daily_anchor")
        # |日昨/分昨−1| 过大（开盘对齐）→ 跳过
        bad_prev = resolve_t0_price_space(
            {"open": 100.0, "prev_close": 110.0},
            [
                {
                    "datetime": "2025-01-02 09:35:00",
                    "open": 100.0,
                    "close": 100.5,
                    "prev_close": 100.0,
                }
            ],
            {"t0_price_space_max_dev_pct": 2.0, "t0_price_space_prev_dev_pct": 0.25},
        )
        self.assertIn("P_d/P_m", str(bad_prev.get("skip_reason") or ""))
        # |S−1| 过大 → 跳过
        bad = resolve_t0_price_space(
            {"open": 110.0, "prev_close": 100.0},
            mins,
            {"t0_price_space_max_dev_pct": 0.25},
        )
        self.assertIn("price_space_mismatch", str(bad.get("skip_reason") or ""))
        # ĉ_d → 分钟：101/1.01 = 100
        self.assertAlmostEqual(map_close_px_to_minute(101.0, scale=1.01), 100.0, places=4)
        from core.t0.close_band import map_close_components_to_minute

        mapped = map_close_components_to_minute(
            {"c_tau": 101.0, "c_trade": None, "c_nowcast": 102.01, "close_px": 101.0},
            scale=1.01,
        )
        self.assertAlmostEqual(mapped["c_tau"], 100.0, places=4)
        self.assertAlmostEqual(mapped["close_px"], 100.0, places=4)
        self.assertAlmostEqual(mapped["close_px_daily"], 101.0, places=4)
        self.assertIsNone(mapped["c_trade"])
        from core.t0.viz import classify_t0_skip_reason

        self.assertEqual(
            classify_t0_skip_reason("price_space_mismatch：|O_d/O_m−1|=1.000% > 0.25%"),
            "price_space_mismatch",
        )

    def test_slots_skip_on_price_space_mismatch(self):
        closes = [100.6] + [100.0] * 30
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 110.0,  # 与分钟开差 10% → 闸
            "prev_close": 100.0,
            "close": 100.0,
        }
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_price_space_max_dev_pct": 0.25,
                "t0_price_space_gate": True,
            }
        )
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=1e6,
            stock_code="600000",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=[],
            score_snap={"y_tau": 0.0, "y_trade": 0.0, "y_nowcast": 0.0},
        )
        self.assertTrue(out.get("skipped"))
        self.assertIn("price_space_mismatch", str(out.get("reason") or ""))

    def test_slots_scale_maps_daily_chat_to_minute_band(self):
        """O_d≠O_m 但在阈内：日线估 ĉ 映回分钟后破带与 S=1 同向。"""
        from unittest.mock import patch

        # 分钟开 100；日线开 100.1（0.1%）；ŷ=0 → ĉ_d=100.1 → ĉ_m=100
        closes = [100.6] + [100.0] * 8 + [99.4] + [100.0] * 20
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100.1,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100.1,  # |日昨/分昨−1| 小
        }
        for m in mins:
            m["prev_close"] = 100.0
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "t0_stop_pct_buy_then_sell": 0,
                "must_cover_same_day": True,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
                "t0_price_space_max_dev_pct": 0.5,
                "t0_price_space_prev_dev_pct": 0.5,
                # 本用例测价空间映射；R<0 开反T
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        scores = _snap(-0.5, predicted_score_blend_vs="open")

        def _fake_rescore(**kwargs):
            snap = dict(kwargs.get("open_snap") or {})
            snap["_score_source"] = "prefix_causal"
            ft = dict(snap.get("features_tau") or {})
            ft.setdefault("ret_open_to_tau", 0.0)
            snap["features_tau"] = ft
            snap["y_path_status"] = "ok"
            if not any(snap.get(k) is not None for k in ("y_τ30", "y_t30", "y_τ90", "y_t90")):
                snap.update(_ytw_heads(up=float(snap.get("y_tau") or 0) >= 0))
            return snap

        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            side_effect=_fake_rescore,
        ):
            out = simulate_t0_day_slots(
                bar=bar,
                minute_bars=mins,
                shares=1000,
                cost=100,
                sellable_shares=1000,
                cfg=cfg,
                cash=1e6,
                stock_code="600000",
                lot=100,
                cost_model="none",
                cost_params={},
                atr_pct=None,
                hist_bars=[],
                score_snap=scores,
            )
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertAlmostEqual(float(out.get("price_space_scale") or 0), 1.001, places=4)
        legs = [
            t
            for t in (out.get("trade_legs") or out.get("trades") or [])
            if isinstance(t, dict)
        ]
        self.assertGreater(len(legs), 0, out)
        self.assertIn("sell_then_buy", str(out))

    def test_slots_skips_when_minute_data_missing_after_open(self):
        """非 09:30：前缀重算标数据缺失则不得开腿。"""
        from unittest.mock import patch

        closes = [100.6] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )

        def _missing_rescore(**kwargs):
            snap = dict(kwargs.get("open_snap") or {})
            snap["y_path_status"] = "minute_data_missing"
            snap["_minute_data_missing"] = True
            snap["_score_source"] = "prefix_minute_missing"
            return snap

        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            side_effect=_missing_rescore,
        ):
            out = simulate_t0_day_slots(
                bar=bar,
                minute_bars=mins,
                shares=1000,
                cost=100,
                sellable_shares=1000,
                cfg=cfg,
                cash=1e6,
                stock_code="600000",
                lot=100,
                cost_model="none",
                cost_params={},
                atr_pct=None,
                hist_bars=[],
                score_snap={"y_tau": 1.0, "y_trade": 0.0, "y_nowcast": 0.0},
            )
        legs = [
            t
            for t in (out.get("trade_legs") or out.get("trades") or [])
            if isinstance(t, dict)
        ]
        self.assertEqual(legs, [], out)
        self.assertIn("分钟数据缺失", str(out.get("reason") or ""))
        self.assertIn(
            "分钟数据缺失",
            str(out.get("close_band_last_enter_skip") or ""),
        )

    def test_bar_prefix_ytw_uses_per_bar_heads(self):
        """ŷ_τw 与该根前缀窗头同源；前缀变则 ŷ_τw / 选向随之变。"""
        from unittest.mock import patch

        closes = [100.6, 101.2, 101.8] + [101.5] * 20
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100.0,
            "high": 102,
            "low": 99,
            "close": 101,
            "prev_close": 100.0,
        }
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": False,
                "t0_pm_degrade_sell_then_buy": "15:30",
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )

        def _live_tracks_price(**kwargs):
            prefix = [b for b in (kwargs.get("minute_prefix") or []) if isinstance(b, dict)]
            close = float(prefix[-1].get("close") or 100.0)
            open_px = float(prefix[0].get("open") or 100.0)
            yt = (close / open_px - 1.0) * 100.0
            snap = dict(kwargs.get("open_snap") or {})
            snap["y_tau"] = yt
            snap["y_tau_oc"] = yt
            snap.setdefault("y_path", 1.0)
            snap.update(_ytw_heads(up=yt >= 0))
            snap["y_path_status"] = "ok"
            snap["_score_source"] = "prefix_causal"
            return snap

        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            side_effect=_live_tracks_price,
        ):
            out = simulate_t0_day_slots(
                bar=bar,
                minute_bars=mins,
                shares=1000,
                cost=100,
                sellable_shares=1000,
                cfg=cfg,
                cash=1e6,
                stock_code="600000",
                lot=100,
                cost_model="none",
                cost_params={},
                atr_pct=None,
                hist_bars=[],
                score_snap={"y_tau": 0.0, "y_tau_oc": 0.0, "y_path": 1.0},
            )
        from core.t0.close_band import blend_y_tw_from_scores

        scan = out.get("close_band_scan") or []
        self.assertTrue(scan, out)
        for row in scan[1:4]:
            if row.get("y_tw") is None:
                continue
            want = blend_y_tw_from_scores(
                {**_ytw_heads(up=float(row.get("y_tau") or 0) >= 0)},
                cfg,
            )
            self.assertIsNotNone(want)
            self.assertAlmostEqual(float(row["y_tw"]), float(want), places=3, msg=row)

        def _live_by_bar(**kwargs):
            prefix = [b for b in (kwargs.get("minute_prefix") or []) if isinstance(b, dict)]
            idx = max(0, len(prefix) - 1)
            yt = 0.2 + idx * 0.4
            snap = dict(kwargs.get("open_snap") or {})
            snap["y_tau"] = yt
            snap["y_tau_oc"] = yt
            snap.setdefault("y_path", 1.0)
            snap.update(_ytw_heads(up=True))
            snap["y_path_status"] = "ok"
            snap["_score_source"] = "prefix_causal"
            return snap

        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            side_effect=_live_by_bar,
        ):
            out2 = simulate_t0_day_slots(
                bar=bar,
                minute_bars=mins,
                shares=1000,
                cost=100,
                sellable_shares=1000,
                cfg=cfg,
                cash=1e6,
                stock_code="600000",
                lot=100,
                cost_model="none",
                cost_params={},
                atr_pct=None,
                hist_bars=[],
                score_snap={"y_tau": 0.2, "y_tau_oc": 0.2, "y_path": 1.0},
            )
        filled2 = [
            s
            for s in (out2.get("t0_slot_results") or [])
            if isinstance(s, dict) and not s.get("skipped")
        ]
        self.assertGreaterEqual(len(filled2), 1, filled2)
        for s in filled2:
            cb = s.get("close_band") or {}
            self.assertEqual(cb.get("c_hat_score_source"), "bar_prefix")
            self.assertIsNotNone(cb.get("leg2_target"))
            sc = s.get("scores") or {}
            if sc.get("y_tau") is not None and cb.get("y_tau") is not None:
                self.assertAlmostEqual(float(sc.get("y_tau")), float(cb.get("y_tau")), places=4)

    def test_close_band_scan_trace_for_debug(self):
        """落盘 close_band_scan：11:00 前每根 OLHC + ŷ_τw。"""
        closes = [100.6, 101.2] + [101.0] * 24
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100.0,
            "high": 102,
            "low": 99,
            "close": 101,
            "prev_close": 100.0,
        }
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=1e6,
            stock_code="600000",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=[],
            score_snap=_snap(1.0),
        )
        scan = out.get("close_band_scan") or []
        self.assertGreaterEqual(len(scan), 2, out)
        first = scan[0]
        self.assertIn("hm", first)
        self.assertIn("c", first)
        self.assertIn("y_tw", first)
        self.assertIn("y_tau", first)
        self.assertIn("y_tw_skip", first)
        self.assertIn("pick", first)
        for row in scan:
            hm = str(row.get("hm") or "")
            self.assertLessEqual(hm, "11:00", row)

    def test_scan_row_carries_prefix_features_tau(self):
        """扫描行带该钟 features_tau，不把日级 10:40 因子贴到 09:45 tip。"""
        from core.t0.slots import _attach_scan_row_tip_fields

        row = {"hm": "09:45"}
        _attach_scan_row_tip_fields(
            row,
            {
                "y_tau": -0.7,
                "features_tau": {
                    "ret_open_to_tau": -0.596,
                    "sector_ret_to_tau": -0.026,
                    "ret_vs_sector": -0.57,
                },
                "formula_terms_tau": {"intercept": 0.1, "terms": []},
                "formula_terms_on": {"intercept": 9, "terms": [{"key": "gap_pct"}]},
                "as_of_tau": "09:45",
            },
        )
        self.assertAlmostEqual(row["features_tau"]["ret_vs_sector"], -0.57, places=4)
        self.assertAlmostEqual(row["features_tau"]["sector_ret_to_tau"], -0.026, places=4)
        self.assertEqual(row.get("as_of_tau"), "09:45")
        self.assertNotIn("formula_terms_on", row)
        self.assertNotIn("y_tau", row)
        later = {"hm": "10:40"}
        _attach_scan_row_tip_fields(
            later,
            {
                "features_tau": {
                    "ret_open_to_tau": 0.589,
                    "sector_ret_to_tau": -0.779,
                    "ret_vs_sector": 1.368,
                },
                "as_of_tau": "10:40",
            },
        )
        self.assertAlmostEqual(later["features_tau"]["ret_vs_sector"], 1.368, places=4)
        self.assertNotAlmostEqual(
            float(row["features_tau"]["ret_vs_sector"]),
            float(later["features_tau"]["ret_vs_sector"]),
            places=3,
        )


class TestCloseBandDayPath(unittest.TestCase):
    def test_reverse_t_opens_and_covers(self):
        """阴线 + ŷ_τw<0 → 反T 现价卖；不预设买回价，靠回补完成第二腿。"""
        closes = [99.4] + [100.0] * 8 + [99.0] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "t0_stop_pct_buy_then_sell": 0,
                "must_cover_same_day": True,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        scores = _snap(-0.5)
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        self.assertFalse(out.get("skipped"))
        rows = out.get("t0_slot_results") or []
        self.assertGreaterEqual(len(rows), 1)
        first = next(
            (r for r in rows if str(r.get("direction_used") or r.get("direction") or "") == "sell_then_buy"),
            rows[0],
        )
        self.assertEqual(
            str(first.get("direction_used") or first.get("direction") or ""),
            "sell_then_buy",
        )
        self.assertIsNotNone((first.get("close_band") or {}).get("leg2_target"))
        self.assertGreaterEqual(int(first.get("sold_qty") or 0), 100)
        n_tr = first.get("trades")
        if isinstance(n_tr, int):
            self.assertGreaterEqual(n_tr, 1)
        else:
            self.assertGreaterEqual(len(n_tr or []), 1)

    def test_y_tc_sidecar_no_longer_blocks(self):
        """ŷ_τc 旁路已下线：阴线 + 负 ŷ_τw 且 ŷ_τc 强正仍开反T。"""
        closes = [99.4] + [100.0] * 28
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        base = {
            "t0_round_ratio": 0.2,
            "fill_mode": "trigger",
            "t0_stop_pct_sell_then_buy": 0,
            "t0_stop_pct_buy_then_sell": 0,
            "must_cover_same_day": True,
            "must_cover_same_day_sell_then_buy": True,
            "t0_pm_degrade_sell_then_buy": "15:30",
        }
        scores = _snap(-0.5, **{"y_τc": 2.0, "y_tc": 2.0})
        kwargs = dict(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        off = simulate_t0_day_slots(cfg=_cfg(base), **kwargs)
        self.assertFalse(off.get("skipped"), off.get("reason"))
        on = simulate_t0_day_slots(
            cfg=_cfg({**base, "y_tc_strong": 0.5}),
            **kwargs,
        )
        self.assertFalse(on.get("skipped"), on.get("reason"))

    def test_strong_trade_tau_disagree_no_longer_blocks(self):
        """阴线 + 负 ŷ_τw：强 y_trade↔y_τ 异号仍可开反T。"""
        closes = [99.4] + [100.0] * 8 + [99.0] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 0.2,
                "y_trade_strong": 0.2,
                "y_eod_strong": 5.0,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        scores = _snap(-1.0, y_trade=1.0, y_eod=0.05)
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        rows = [
            r
            for r in (out.get("t0_slot_results") or [])
            if not r.get("skipped") and int(r.get("sold_qty") or 0) + int(r.get("bought_qty") or 0) > 0
        ]
        self.assertGreaterEqual(len(rows), 1)
        first = rows[0]
        self.assertEqual(first.get("direction") or first.get("direction_used"), "sell_then_buy")
        cb = first.get("close_band") or {}
        self.assertIsNotNone(cb.get("leg2_target"))

    def test_green_bar_ytw_opens_long_t(self):
        """阳线 + ŷ_τw≥0 → 正T（现价买；不预设卖价）。"""
        closes = [100.75] + [100.0] * 28
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "y_tau_enter": 0.01,
                "y_path_enter": 0.0,
                "fill_mode": "trigger",
                "t0_stop_pct_buy_then_sell": 0,
                "must_cover_same_day_buy_then_sell": False,
            }
        )
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=_snap(2.0),
            defer_eod=False,
        )
        rows = [
            r
            for r in (out.get("t0_slot_results") or [])
            if not r.get("skipped") and int(r.get("sold_qty") or 0) + int(r.get("bought_qty") or 0) > 0
        ]
        self.assertGreater(len(rows), 0, out)
        self.assertEqual(rows[0].get("direction") or rows[0].get("direction_used"), "buy_then_sell")

    def test_missing_ytw_blocks_open(self):
        """阳线但缺 ŷ_τw 窗头 → 不开轮。"""
        closes = [100.6, 99.4] * 12
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        scores = {
            "y_tau": 1.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
            "y_path": 1.0,
            "nowcast_vs": "open",
        }
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        rows = [
            r
            for r in (out.get("t0_slot_results") or [])
            if not r.get("skipped") and int(r.get("sold_qty") or 0) + int(r.get("bought_qty") or 0) > 0
        ]
        self.assertEqual(len(rows), 0)
        reason = str(out.get("close_band_last_y_tw_skip") or out.get("reason") or "")
        self.assertIn("ŷ_τw", reason)

    def test_legacy_r_tau_enter_does_not_block_open(self):
        """旧 r_tau_enter overlay 已下线，阳线+正 ŷ_τw 仍开正T。"""
        closes = [100.6] + [100.0] * 30
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "prev_close": 100,
        }
        scores = _snap(1.0)

        def _run(r_enter):
            cfg = _cfg(
                {
                    "t0_round_ratio": 0.2,
                    "r_tau_enter": r_enter,
                    "r_tau_enter_alt": r_enter,
                    "fill_mode": "trigger",
                    "t0_stop_pct_buy_then_sell": 0,
                    "must_cover_same_day_buy_then_sell": False,
                    "t0_pm_degrade_buy_then_sell": "15:30",
                }
            )
            return simulate_t0_day_slots(
                bar=bar,
                minute_bars=mins,
                shares=1000,
                cost=100,
                sellable_shares=1000,
                cfg=cfg,
                cash=200000,
                stock_code="",
                lot=100,
                cost_model="none",
                cost_params={},
                atr_pct=None,
                hist_bars=None,
                score_snap=scores,
                defer_eod=False,
            )

        def _filled(out):
            return [
                r
                for r in (out.get("t0_slot_results") or [])
                if not r.get("skipped")
                and int(r.get("sold_qty") or 0) + int(r.get("bought_qty") or 0) > 0
            ]

        blocked = _run(0.8)
        self.assertGreater(len(_filled(blocked)), 0)
        self.assertFalse(blocked.get("skipped"))

        opened = _run(0.0)
        self.assertGreater(len(_filled(opened)), 0)
        self.assertFalse(opened.get("skipped"))

    def test_no_preset_leg2_covers_by_eod(self):
        """不预设第二腿目标价：反T 靠当日回补完成买回。"""
        closes = [99.4] + [100.0] * 5 + [99.0] + [100.0] * 10
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 1.0,
                "t0_slots_max_rounds": 1,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "13:00",
            }
        )
        from core.t0.minute_path import _first_touch_sell_then_buy

        def _gate(i, ci=0):
            return i == ci

        path = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg=cfg,
            cost_model="none",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=0.0,
            t0_ratio=1.0,
            cash=200000,
            session_bars=mins,
            session_bar=bar,
            defer_eod=False,
            leg1_gate_at=_gate,
            leg2_target_px=None,
            y_tau=-1.0,
        )
        self.assertGreaterEqual(int(path.get("sold_qty") or 0), 100)
        cb_note = " ".join(str(t.get("note") or "") for t in (path.get("trades") or []) if isinstance(t, dict))
        self.assertIn("反T", cb_note)

    def test_multi_round_scan_does_not_restore_sellable_from_future_cover(self):
        """反T 第一轮卖光后，扫描态不可因午后回补提前恢复可卖再开第二轮。"""
        # 一根早盘阴线卖光 + 午后回补
        closes = [99.4, 99.3] + [100.0] * 10 + [99.0] + [100.0] * 8
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 1.0,
                "t0_slots_max_rounds": 2,
                "t0_max_position_pct": 1.0,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        scores = _snap(-0.5)
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        rows = [
            r
            for r in (out.get("t0_slot_results") or [])
            if not r.get("skipped")
            and int(r.get("sold_qty") or 0) > 0
        ]
        # 第一轮已用满日初可卖；不得因未来回补再开一轮反T
        self.assertEqual(len(rows), 1, rows)
        self.assertGreaterEqual(int(rows[0].get("sold_qty") or 0), 1000)
        # 槽位腿列表在 trade_legs，trades 为计数
        self.assertIsInstance(rows[0].get("trade_legs"), list)
        self.assertGreaterEqual(len(rows[0].get("trade_legs") or []), 1)
        self.assertEqual(int(rows[0].get("trades") or 0), len(rows[0].get("trade_legs") or []))

    def test_norm_trade_at_blocks_space_vs_t_lexicographic_leak(self):
        from core.t0.slots import _norm_trade_at, _slot_trade_legs, _try_apply_slot_trades

        # 未归一化时 "15:00" 空格串字典序会 ≤ "T09:35"
        eod = "2025-01-02 15:00:00"
        morning_t = "2025-01-02T09:35:00"
        self.assertGreater(_norm_trade_at(eod), _norm_trade_at(morning_t))
        legs = _slot_trade_legs({"trades": 2, "trade_legs": [{"at": eod, "side": "t0_buy"}]})
        self.assertEqual(len(legs), 1)
        self.assertEqual(_slot_trade_legs({"trades": 2}), [])
        # 落账排序须用归一化键（T 早于空格午后）
        ok, cash, shares, sellable = _try_apply_slot_trades(
            [
                {
                    "at": eod,
                    "side": "t0_buy",
                    "shares": 100,
                    "amount": 1000,
                    "fees": 0,
                },
                {
                    "at": morning_t,
                    "side": "t0_sell",
                    "shares": 100,
                    "amount": 1000,
                    "fees": 0,
                },
            ],
            cash_now=0.0,
            shares_now=1000.0,
            sellable_old=1000.0,
        )
        # 先卖后买：现金够买；若按原始字典序会先买失败
        self.assertTrue(ok)
        self.assertAlmostEqual(cash, 0.0, places=4)
        self.assertAlmostEqual(shares, 1000.0, places=4)
        self.assertAlmostEqual(sellable, 900.0, places=4)

    def test_no_leg1_after_1100(self):
        # 11:00 后出阳线也不开 leg1
        closes = [100.0] * 20  # ~到 11:10
        mins = _mins(closes)
        for mb in mins:
            if parse_bar_hm(mb) > "11:00":
                mb["close"] = 101.0
                mb["high"] = 101.2
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = _cfg(
            {
                "t0_round_ratio": 0.2,
            }
        )
        scores = _snap(1.0)
        out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg,
            cash=200000,
            stock_code="",
            lot=100,
            cost_model="none",
            cost_params={},
            atr_pct=None,
            hist_bars=None,
            score_snap=scores,
            defer_eod=False,
        )
        rows = [r for r in (out.get("t0_slot_results") or []) if not r.get("skipped")]
        for r in rows:
            hm = str(r.get("t0_slot_hm") or "")
            if hm:
                self.assertLessEqual(hm, "11:00")


class TestMergeSlotDay(unittest.TestCase):
    def _leg(self, side, shares, price, at):
        amt = float(shares) * float(price)
        delta = amt if str(side).endswith("sell") else -amt
        return {
            "side": side,
            "shares": shares,
            "price": price,
            "at": at,
            "amount": amt,
            "fees": 0,
            "net_cash_delta": delta,
        }

    def _slot(self, sid, hm, direction, legs, **extra):
        row = {
            "t0_slot": sid,
            "t0_slot_hm": hm,
            "hm": hm,
            "direction_used": direction,
            "skipped": False,
            "trades": list(legs),
            "trade_legs": list(legs),
            "pnl": 0,
            "exposure_pnl": 0,
            "sold_qty": 0,
            "covered_qty": 0,
            "bought_qty": 0,
            "sold_back_qty": 0,
        }
        row.update(extra)
        return row

    def test_rerun_drops_cash_dependent_round(self):
        """满仓：后轮正T占用反T卖开款时，丢掉后轮、留下先开的反T，现金不变负。"""
        from core.t0.slots import _merge_slot_day

        bar = {"date": "2025-01-02", "open": 10, "close": 9}
        r1 = self._slot(
            "r1",
            "10:00",
            "sell_then_buy",
            [
                self._leg("t0_sell", 200, 10, "2025-01-02 10:00:00"),
                self._leg("t0_buy", 200, 9, "2025-01-02 14:55:00"),
            ],
            sold_qty=200,
            covered_qty=200,
            close_band={"hm": "10:00"},
        )
        r2 = self._slot(
            "r2",
            "10:30",
            "buy_then_sell",
            [self._leg("t0_buy", 200, 10, "2025-01-02 10:30:00")],
            bought_qty=200,
        )
        merged = _merge_slot_day(
            slot_outs=[r1, r2],
            bar=bar,
            shares=1000,
            cash=0,
            sellable_shares=1000,
            minute_bars=[],
        )
        cash0 = 0.0
        self.assertGreaterEqual(cash0 + float(merged.get("cash_delta") or 0), -1e-6)
        self.assertAlmostEqual(float(merged["shares_end"]), 1000.0)
        trades = merged.get("trades") or []
        self.assertEqual(len(trades), 2)
        self.assertTrue(all(t.get("side", "").endswith(("sell", "buy")) for t in trades))
        by_id = {str(r.get("t0_slot")): r for r in (merged.get("t0_slot_results") or [])}
        self.assertFalse(by_id["r1"].get("skipped"))
        self.assertTrue(by_id["r2"].get("skipped"))
        self.assertEqual(by_id["r1"].get("t0_slot_hm"), "10:00")
        self.assertEqual((by_id["r1"].get("close_band") or {}).get("hm"), "10:00")

    def test_rerun_keeps_both_when_cash_covers(self):
        from core.t0.slots import _merge_slot_day

        bar = {"date": "2025-01-02", "open": 10, "close": 9}
        r1 = self._slot(
            "r1",
            "10:00",
            "sell_then_buy",
            [
                self._leg("t0_sell", 200, 10, "2025-01-02 10:00:00"),
                self._leg("t0_buy", 200, 9, "2025-01-02 14:55:00"),
            ],
            sold_qty=200,
            covered_qty=200,
        )
        r2 = self._slot(
            "r2",
            "10:30",
            "buy_then_sell",
            [self._leg("t0_buy", 200, 10, "2025-01-02 10:30:00")],
            bought_qty=200,
        )
        merged = _merge_slot_day(
            slot_outs=[r1, r2],
            bar=bar,
            shares=1000,
            cash=5000,
            sellable_shares=1000,
            minute_bars=[],
        )
        self.assertFalse(merged.get("skipped"))
        self.assertEqual(len(merged.get("trades") or []), 3)
        self.assertAlmostEqual(float(merged["shares_end"]), 1200.0)
        # 5000 +2000 -2000 -1800 = 3200
        self.assertAlmostEqual(5000.0 + float(merged.get("cash_delta") or 0), 3200.0)

    def test_uncovered_reverse_t_can_fund_buy(self):
        """反T未把买回写进成交时，正T仍可用卖开款（无回滚链）。"""
        from core.t0.slots import _merge_slot_day

        bar = {"date": "2025-01-02", "open": 10, "close": 10}
        r1 = self._slot(
            "r1",
            "10:00",
            "sell_then_buy",
            [self._leg("t0_sell", 200, 10, "2025-01-02 10:00:00")],
            sold_qty=200,
            uncovered_qty=200,
        )
        r2 = self._slot(
            "r2",
            "10:30",
            "buy_then_sell",
            [self._leg("t0_buy", 200, 10, "2025-01-02 10:30:00")],
            bought_qty=200,
        )
        merged = _merge_slot_day(
            slot_outs=[r1, r2],
            bar=bar,
            shares=1000,
            cash=0,
            sellable_shares=1000,
            minute_bars=[],
        )
        self.assertEqual(len(merged.get("trades") or []), 2)
        self.assertAlmostEqual(float(merged["shares_end"]), 1000.0)
        self.assertAlmostEqual(float(merged.get("cash_delta") or 0), 0.0)


if __name__ == "__main__":
    unittest.main()
