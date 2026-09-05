"""v6 收盘带宽选腿单元与路径烟测。"""

from __future__ import annotations

import unittest

from core.t0.close_band import (
    band_decision,
    band_delta_px,
    close_band_sign_skip_reason,
    day_open_prev_close,
    estimate_close_px,
    freeze_round,
    hm_allows_leg1,
    parse_bar_hm,
)
from core.t0.config import T0_LAST_LEG1_HM, load_t0_rules
from core.t0.slots import simulate_t0_day_slots


def _mins(closes, *, session_open=100.0):
    """合成分钟线；首根 open=session_open（与估 Ĉ 锚同源），各根 close 按序列。"""
    out = []
    h, m = 9, 35
    o0 = float(session_open)
    for i, c in enumerate(closes):
        px = float(c)
        o = o0 if i == 0 else px
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
        # ĉ := ĉ_τ only；trade/nc 不进均价
        self.assertAlmostEqual(est["close_px"], 101.0, places=4)
        self.assertAlmostEqual(est["c_tau"], 101.0, places=4)
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

    def test_freeze_opposite_band(self):
        fr = freeze_round(
            direction="sell_then_buy",
            leg1_px=100.6,
            close_px=100.0,
            delta_px=0.5,
            ratio=0.2,
            bar_index=0,
            hm="09:35",
        )
        self.assertEqual(fr.leg2_target, 99.5)
        fr2 = freeze_round(
            direction="buy_then_sell",
            leg1_px=99.4,
            close_px=100.0,
            delta_px=0.5,
            ratio=0.2,
            bar_index=1,
            hm="09:40",
        )
        self.assertEqual(fr2.leg2_target, 100.5)

    def test_hm_cutoff(self):
        self.assertEqual(parse_bar_hm({"datetime": "2025-01-02 09:35:00"}), "09:35")
        self.assertEqual(T0_LAST_LEG1_HM, "11:00")
        self.assertTrue(hm_allows_leg1("11:00"))
        self.assertFalse(hm_allows_leg1("11:05"))

    def test_sign_skip_trade_vs_tau(self):
        """v6：强 trade/eod 异号闸已下线；path 强闸另测。"""
        sc = {"y_tau": 1.0, "y_trade": -1.0, "y_eod": 0.0, "y_path": 0.05}
        self.assertIsNone(
            close_band_sign_skip_reason(
                sc, {"y_trade_strong": 0.2, "y_eod_strong": 5.0, "y_path_strong": 0.2}
            )
        )
        self.assertIsNone(
            close_band_sign_skip_reason(
                {"y_tau": 1.0, "y_trade": 1.0, "y_eod": 0.05, "y_path": 0.05},
                {"y_trade_strong": 0.2, "y_eod_strong": 0.2, "y_path_strong": 0.2},
            )
        )

    def test_sign_skip_eod_vs_tau(self):
        self.assertIsNone(
            close_band_sign_skip_reason(
                {"y_tau": 1.0, "y_trade": 0.0, "y_eod": -1.0, "y_path": 0.05},
                {"y_trade_strong": 5.0, "y_eod_strong": 0.2, "y_path_strong": 0.2},
            )
        )

    def test_sign_skip_path_vs_tau(self):
        # |path|≤强阈：允许异号
        weak = close_band_sign_skip_reason(
            {"y_tau": 1.0, "y_path": -0.1},
            {"y_path_strong": 0.2, "y_use_path": True},
        )
        self.assertIsNone(weak)
        # |path|>强阈且异号 → 跳过
        strong_dis = close_band_sign_skip_reason(
            {"y_tau": 1.0, "y_path": -0.5},
            {"y_path_strong": 0.2, "y_use_path": True},
        )
        self.assertIsNotNone(strong_dis)
        self.assertIn("y_path", strong_dis)
        self.assertIn("异号", strong_dis)
        # 同号放行
        strong_ok = close_band_sign_skip_reason(
            {"y_tau": -1.0, "y_path": -0.5},
            {"y_path_strong": 0.2, "y_use_path": True},
        )
        self.assertIsNone(strong_ok)
        # 关用path：不强拦
        off = close_band_sign_skip_reason(
            {"y_tau": 1.0, "y_path": -0.5},
            {"y_path_strong": 0.2, "y_use_path": False},
        )
        self.assertIsNone(off)

    def test_enter_skip_tau_and_path(self):
        from core.t0.close_band import close_band_enter_skip_reason

        weak_tau = close_band_enter_skip_reason(
            {"y_tau": 0.005, "y_path": 1.0},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01},
        )
        self.assertIsNotNone(weak_tau)
        self.assertIn("y_τ", weak_tau)
        weak_path = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 0.005},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01},
        )
        self.assertIsNotNone(weak_path)
        self.assertIn("y_path", weak_path)
        ok = close_band_enter_skip_reason(
            {"y_tau": -0.5, "y_path": 0.8},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01},
        )
        self.assertIsNone(ok)
        # 缺 path → 跳过（y_use_path 默认开）
        no_path = close_band_enter_skip_reason(
            {"y_tau": 1.0},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01},
        )
        self.assertIsNotNone(no_path)
        self.assertIn("y_path", no_path)
        # 开盘-only 尚未有分钟小包：path 闸暂不适用（避免第一根真破带被误杀）
        open_only = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path_status": "minute_feats_missing"},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01},
        )
        self.assertIsNone(open_only)
        # 非开盘：缺分钟小包 = 数据缺失，硬跳过
        data_miss = close_band_enter_skip_reason(
            {
                "y_tau": 1.0,
                "y_path_status": "minute_data_missing",
                "_minute_data_missing": True,
            },
            {"y_tau_enter": 0.01, "y_path_enter": 0.01, "y_use_path": False},
        )
        self.assertIsNotNone(data_miss)
        self.assertIn("分钟数据缺失", data_miss)
        # 关 path 闸：缺 path 不挡
        path_off = close_band_enter_skip_reason(
            {"y_tau": 1.0},
            {"y_tau_enter": 0.01, "y_path_enter": 0.01, "y_use_path": False},
        )
        self.assertIsNone(path_off)
        # enter=0 仅关幅度；缺 path 仍跳过
        miss_with_enter_off = close_band_enter_skip_reason(
            {"y_tau": 0.0, "y_path": None},
            {"y_tau_enter": 0.0, "y_path_enter": 0.0},
        )
        self.assertIsNotNone(miss_with_enter_off)
        self.assertIn("y_path", miss_with_enter_off)
        off = close_band_enter_skip_reason(
            {"y_tau": 0.0, "y_path": 1.0},
            {"y_tau_enter": 0.0, "y_path_enter": 0.0},
        )
        self.assertIsNone(off)

    def test_enter_skip_cx_max(self):
        from core.t0.close_band import close_band_enter_skip_reason

        cfg = {
            "y_tau_enter": 0.0,
            "y_path_enter": 0.0,
            "y_use_path": False,
            "y_complexity_max": 0.5,
        }
        too = close_band_enter_skip_reason(
            {"y_tau": 1.0, "predicted_score_complexity": 0.8},
            cfg,
        )
        self.assertIsNotNone(too)
        self.assertIn("太折", too)
        ok = close_band_enter_skip_reason(
            {"y_tau": 1.0, "predicted_score_complexity": 0.4},
            cfg,
        )
        self.assertIsNone(ok)
        miss = close_band_enter_skip_reason({"y_tau": 1.0}, cfg)
        self.assertIsNone(miss)
        old_key = close_band_enter_skip_reason(
            {"y_tau": 1.0, "predicted_score_cx": 0.8},
            {**cfg, "y_complexity_max": None, "y_cx_max": 0.5},
        )
        self.assertIsNotNone(old_key)
        self.assertIn("太折", old_key)
        old_scale = close_band_enter_skip_reason(
            {"y_tau": 1.0, "predicted_score_complexity": 80.0},
            cfg,
        )
        self.assertIsNotNone(old_scale)
        cap = close_band_enter_skip_reason(
            {"y_tau": 1.0, "predicted_score_complexity": 0.99},
            {**cfg, "y_complexity_max": 1.0},
        )
        self.assertIsNone(cap)

    def test_enter_skip_r_tau(self):
        from core.t0.close_band import close_band_enter_skip_reason

        cfg = {
            "y_tau_enter": 0.0,
            "y_path_enter": 0.0,
            "y_use_path": False,
            "r_tau_enter": 0.5,
        }
        weak = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0}, cfg, r_pct=0.2
        )
        self.assertIsNotNone(weak)
        self.assertIn("超额不足", weak)
        ok = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0}, cfg, r_pct=0.6
        )
        self.assertIsNone(ok)
        off = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0},
            {**cfg, "r_tau_enter": 0.0},
            r_pct=0.01,
        )
        self.assertIsNone(off)
        miss = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0}, cfg
        )
        self.assertIsNotNone(miss)
        self.assertIn("R̂_τ 缺失", miss)
        # r_tau_enter=0 且省略 r_pct：不挡（与既有调用兼容）
        omitted = close_band_enter_skip_reason(
            {"y_tau": 1.0, "y_path": 1.0},
            {"y_tau_enter": 0.0, "y_path_enter": 0.0, "y_use_path": False},
        )
        self.assertIsNone(omitted)

    def test_band_uses_minute_close_not_daily(self):
        """破带 p=本根 5m close；日线 close 不同也不得替代。"""
        from core.t0.slots import simulate_t0_day_slots

        # 分钟首根收 100.6 上破 ĉ=100+δ；日线 close=99.4 若误用会走下破
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
        cfg = load_t0_rules(
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
                "y_tau_leg1_prior_mode": "off",
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
            score_snap={"y_tau": 0.0, "y_tau_oc": 0.0, "y_path": 1.0},
        )
        self.assertEqual(out.get("direction_used"), "sell_then_buy", out)
        cb = (out.get("t0_slot_results") or [{}])[0].get("close_band") or {}
        self.assertAlmostEqual(float(cb.get("band_r_pct") or 0), 0.6, places=2)

    def test_tau_prior_blocks_against_leg(self):
        from core.t0.close_band import (
            close_band_pick_direction,
            close_band_tau_prior_skip_reason,
            normalize_y_tau_leg1_prior_mode,
            resolve_close_band_thresholds_pct,
        )

        # skip 已下线：旧 skip 配置不硬跳过（归一成 score）
        bear_long = close_band_tau_prior_skip_reason(
            {"y_tau": -0.5},
            {"y_tau_leg1_prior_mode": "skip"},
            direction="buy_then_sell",
        )
        self.assertIsNone(bear_long)
        bull_short = close_band_tau_prior_skip_reason(
            {"y_tau": 0.5},
            {"y_tau_leg1_prior_mode": "skip"},
            direction="sell_then_buy",
        )
        self.assertIsNone(bull_short)
        self.assertEqual(normalize_y_tau_leg1_prior_mode("skip"), "score")
        self.assertEqual(normalize_y_tau_leg1_prior_mode("hard"), "score")
        self.assertIsNone(
            close_band_tau_prior_skip_reason(
                {"y_tau": -0.5},
                {"y_tau_leg1_prior_mode": "off"},
                direction="buy_then_sell",
            )
        )
        # score：s=clip(k·y_τ, ±α·δ)；0<α<1 → upper=δ+s，lower=−δ+s
        up, lo = resolve_close_band_thresholds_pct(
            0.5, 0.3, mode="score", risk_k=1.0, shift_scale=0.9
        )
        self.assertAlmostEqual(up, 0.8, places=6)  # δ+0.3
        self.assertAlmostEqual(lo, -0.2, places=6)
        # 强看空：s 触 −αδ，upper=δ(1−α)>0，lower=−δ(1+α)
        up_b, lo_b = resolve_close_band_thresholds_pct(
            0.5, -1.24, mode="score", risk_k=1.0, shift_scale=0.9
        )
        self.assertAlmostEqual(up_b, 0.05, places=6)  # 0.5 − 0.45
        self.assertAlmostEqual(lo_b, -0.95, places=6)  # −0.5 − 0.45
        self.assertGreater(up_b, 0.0)
        self.assertLess(lo_b, 0.0)
        # 08-21 类：贴 ĉ 微幅上穿；α=0.9 时 upper=0.05 > r≈0.012% → 不开反T
        d0, meta0 = close_band_pick_direction(
            84.950,
            84.940,
            0.5,
            {"y_tau": -1.24},
            {
                "y_tau_leg1_prior_mode": "score",
                "y_tau_leg1_prior_risk": 1.0,
                "y_tau_leg1_prior_shift_scale": 0.9,
            },
        )
        self.assertIsNone(d0)
        self.assertAlmostEqual(meta0["upper_pct"], 0.05, places=6)
        # r=0.6% 对称会反T；y_τ=0.4 → upper=0.9，不开
        d1, meta1 = close_band_pick_direction(
            100.6,
            100.0,
            0.5,
            {"y_tau": 0.4},
            {
                "y_tau_leg1_prior_mode": "score",
                "y_tau_leg1_prior_risk": 1.0,
                "y_tau_leg1_prior_shift_scale": 0.9,
            },
        )
        self.assertIsNone(d1)
        self.assertAlmostEqual(meta1["upper_pct"], 0.9, places=6)
        # 同向：小幅下破即可正T
        d2, meta2 = close_band_pick_direction(
            99.7,
            100.0,
            0.5,
            {"y_tau": 0.4},
            {
                "y_tau_leg1_prior_mode": "score",
                "y_tau_leg1_prior_risk": 1.0,
                "y_tau_leg1_prior_shift_scale": 0.9,
            },
        )
        self.assertEqual(d2, "buy_then_sell")
        self.assertGreater(meta2["lower_pct"], -0.5)
        self.assertLess(meta2["lower_pct"], 0.0)
        # α≥1 钳到上限 1.0：|s|≤δ，同侧可贴 0
        up_s, lo_s = resolve_close_band_thresholds_pct(
            0.5, 1.0, mode="score", risk_k=1.0, shift_scale=2.0
        )
        self.assertAlmostEqual(up_s, 1.0, places=6)  # δ + 1.0δ
        self.assertAlmostEqual(lo_s, 0.0, places=6)
        from core.t0.close_band import clamp_y_tau_leg1_prior_shift_scale

        self.assertAlmostEqual(clamp_y_tau_leg1_prior_shift_scale(0.05), 0.1, places=6)
        self.assertAlmostEqual(clamp_y_tau_leg1_prior_shift_scale(0.95), 0.95, places=6)
        self.assertAlmostEqual(clamp_y_tau_leg1_prior_shift_scale(1.2), 1.0, places=6)
        self.assertAlmostEqual(clamp_y_tau_leg1_prior_shift_scale(None), 0.1, places=6)
        # k 与 α 分工：同等 raw 下 α 只抬 cap，不放大未触顶的 s
        up_a, lo_a = resolve_close_band_thresholds_pct(
            0.5, 0.2, mode="score", risk_k=1.0, shift_scale=0.9
        )
        up_k, lo_k = resolve_close_band_thresholds_pct(
            0.5, 0.2, mode="score", risk_k=2.0, shift_scale=0.9
        )
        self.assertAlmostEqual(up_a, 0.7, places=6)  # s=0.2 未触 α·δ=0.45
        self.assertAlmostEqual(up_k, 0.9, places=6)  # s=min(0.4, 0.45)=0.4
        self.assertNotAlmostEqual(up_a, up_k, places=6)

    def test_price_space_scale_and_gate(self):
        from core.t0.close_band import (
            band_decision_return,
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
        # 收益空间破带
        self.assertEqual(band_decision_return(0.6, 0.0, 0.5), "sell_then_buy")
        self.assertEqual(band_decision_return(-0.6, 0.0, 0.5), "buy_then_sell")
        self.assertIsNone(band_decision_return(0.1, 0.0, 0.5))
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
        cfg = load_t0_rules(
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
        cfg = load_t0_rules(
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
                # 本用例测价空间映射；ŷ=0 时关掉入场闸
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        scores = {
            "y_tau": 0.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
            "nowcast_vs": "open",
            "predicted_score_blend_vs": "open",
        }

        def _fake_rescore(**kwargs):
            snap = dict(kwargs.get("open_snap") or {})
            snap["_score_source"] = "prefix_causal"
            ft = dict(snap.get("features_tau") or {})
            ft.setdefault("ret_open_to_tau", 0.0)
            snap["features_tau"] = ft
            snap["y_path_status"] = "ok"
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
        cfg = load_t0_rules(
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

    def test_bar_prefix_c_hat_uses_per_bar_y_tau(self):
        """Ĉ 与该根前缀 ŷ_τ 同源；前缀变则 ĉ/y_τ 随之变。"""
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
        cfg = load_t0_rules(
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
                "y_tau_leg1_prior_mode": "off",
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
        scan = out.get("close_band_scan") or []
        self.assertTrue(scan, out)
        for row in scan[1:4]:
            if row.get("y_tau") is None or row.get("r_pct") is None:
                continue
            self.assertLess(abs(float(row["r_pct"])), 0.15, row)

        def _live_by_bar(**kwargs):
            prefix = [b for b in (kwargs.get("minute_prefix") or []) if isinstance(b, dict)]
            idx = max(0, len(prefix) - 1)
            yt = 0.2 + idx * 0.4
            snap = dict(kwargs.get("open_snap") or {})
            snap["y_tau"] = yt
            snap["y_tau_oc"] = yt
            snap.setdefault("y_path", 1.0)
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
        self.assertGreaterEqual(len(filled2), 2, filled2)
        yts = {round(float((s.get("close_band") or {}).get("y_tau")), 2) for s in filled2}
        self.assertGreater(len(yts), 1, filled2)
        for s in filled2:
            cb = s.get("close_band") or {}
            self.assertEqual(cb.get("c_hat_score_source"), "bar_prefix")
            sc = s.get("scores") or {}
            self.assertAlmostEqual(float(sc.get("y_tau")), float(cb.get("y_tau")), places=4)

    def test_close_band_scan_trace_for_debug(self):
        """落盘 close_band_scan：11:00 前每根 OLHC + ĉ/y。"""
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
        cfg = load_t0_rules(
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
            score_snap={"y_tau": 0.0, "y_tau_oc": 0.0, "y_path": 1.0},
        )
        scan = out.get("close_band_scan") or []
        self.assertGreaterEqual(len(scan), 2, out)
        first = scan[0]
        self.assertIn("hm", first)
        self.assertIn("c", first)
        self.assertIn("c_tau", first)
        self.assertIn("y_tau", first)
        self.assertIn("y_complexity", first)
        self.assertIn("y_cx", first)
        for row in scan:
            hm = str(row.get("hm") or "")
            self.assertLessEqual(hm, "11:00", row)


class TestCloseBandDayPath(unittest.TestCase):
    def test_reverse_t_opens_and_covers(self):
        # ĉ=100, δ=0.5 → 上破卖、下破买回
        closes = [100.6] + [100.0] * 8 + [99.4] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "t0_stop_pct_buy_then_sell": 0,
                "must_cover_same_day": True,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        scores = {
            "y_tau": 0.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
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
        self.assertEqual((first.get("close_band") or {}).get("leg2_target"), 99.5)
        self.assertGreaterEqual(int(first.get("sold_qty") or 0), 100)
        n_tr = first.get("trades")
        if isinstance(n_tr, int):
            self.assertGreaterEqual(n_tr, 1)
        else:
            self.assertGreaterEqual(len(n_tr or []), 1)

    def test_strong_trade_tau_disagree_no_longer_blocks(self):
        """破带 + 强 y_trade↔y_τ 异号：v6 仍可开轮（异号闸已下线）。"""
        closes = [100.6] + [100.0] * 8 + [99.4] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "y_trade_strong": 0.2,
                "y_eod_strong": 5.0,
                "y_tau_leg1_prior": False,
                "y_tau_leg1_prior_mode": "off",
                "y_use_path": False,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        # y_τ=-1 → ĉ=99；收 100.6 破上沿 → 反T（先验 off）
        scores = {
            "y_tau": -1.0,
            "y_trade": 1.0,
            "y_eod": 0.05,
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
        self.assertGreaterEqual(len(rows), 1)
        self.assertFalse(out.get("close_band_last_sign_skip"))
        first = rows[0]
        self.assertEqual(first.get("direction") or first.get("direction_used"), "sell_then_buy")
        cb = first.get("close_band") or {}
        self.assertAlmostEqual(float(cb.get("close_px") or 0), 99.0, places=4)

    def test_tau_prior_blocks_reverse_t_on_bullish_day(self):
        """看多日：score 抬反T门槛，温和上破不开反T。"""
        # ĉ_τ=100.2（y_τ=0.2）；100.75 → r≈0.55%：off 破 ±0.5，score upper≈0.7 仍挡
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
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "y_tau_enter": 0.01,
                "y_path_enter": 0.0,
                "y_tau_leg1_prior_mode": "score",
                "y_tau_leg1_prior_risk": 1.0,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        # y_τ=0.2 → ĉ=100.2；upper≈0.7；r≈0.55% < upper → 不开
        scores = {
            "y_tau": 0.2,
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
        self.assertEqual(rows, [])
        self.assertTrue(out.get("skipped"))
        # 对照：关先验后同价应能开反T
        cfg_off = load_t0_rules(
            {
                **{
                    "t0_close_band_delta_pct": 0.5,
                    "t0_round_ratio": 0.2,
                    "y_tau_enter": 0.01,
                    "y_path_enter": 0.0,
                    "y_tau_leg1_prior_mode": "off",
                    "fill_mode": "trigger",
                    "t0_stop_pct_sell_then_buy": 0,
                    "must_cover_same_day_sell_then_buy": True,
                    "t0_pm_degrade_sell_then_buy": "15:30",
                }
            }
        )
        out_off = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=1000,
            cost=100,
            sellable_shares=1000,
            cfg=cfg_off,
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
        rows_off = [
            r
            for r in (out_off.get("t0_slot_results") or [])
            if not r.get("skipped") and int(r.get("sold_qty") or 0) + int(r.get("bought_qty") or 0) > 0
        ]
        self.assertGreater(len(rows_off), 0)

    def test_weak_tau_enter_blocks_band_open(self):
        """破带但 |y_τ|<enter → 不开轮。"""
        closes = [100.6] + [100.0] * 8 + [99.4] + [100.0] * 20
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "y_tau_enter": 0.5,
                "y_path_enter": 0.0,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
            }
        )
        scores = {
            "y_tau": 0.05,
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
        self.assertEqual(rows, [])
        self.assertTrue(out.get("skipped"))
        reason = str(out.get("close_band_last_enter_skip") or out.get("reason") or "")
        self.assertIn("入场", reason)
        self.assertIn("y_τ", reason)

    def test_weak_r_tau_enter_blocks_band_open(self):
        """破带但 |R̂_τ|<r_tau_enter → 不开轮；0=关闸，同价仍开。"""
        # open=100, y_τ=1% → ĉ=101；δ=0.5% 对称；C=101.6 → r≈0.594% 上破但不到 0.8
        closes = [101.6] + [101.0] * 30
        mins = _mins(closes, session_open=100.0)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "prev_close": 100,
        }
        scores = {
            "y_tau": 1.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
            "y_path": 1.0,
            "nowcast_vs": "open",
        }

        def _run(r_enter):
            cfg = load_t0_rules(
                {
                    "t0_close_band_delta_pct": 0.5,
                    "t0_round_ratio": 0.2,
                    "y_tau_enter": 0.0,
                    "y_path_enter": 0.0,
                    "y_use_path": False,
                    "y_tau_leg1_prior_mode": "off",
                    "r_tau_enter": r_enter,
                    "fill_mode": "trigger",
                    "t0_stop_pct_sell_then_buy": 0,
                    "must_cover_same_day_sell_then_buy": True,
                    "t0_pm_degrade_sell_then_buy": "15:30",
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
        self.assertEqual(_filled(blocked), [])
        self.assertTrue(blocked.get("skipped"))
        reason = str(blocked.get("close_band_last_enter_skip") or blocked.get("reason") or "")
        self.assertIn("超额不足", reason)
        self.assertIn("R̂_τ", reason)

        opened = _run(0.0)
        self.assertGreater(len(_filled(opened)), 0)
        self.assertFalse(opened.get("skipped"))

    def test_frozen_leg2_beats_tau_exit_target(self):
        """有 ŷ_τ 时第二腿仍须触冻结 ĉ−δ，不能被 τ 出场价抢走。"""
        closes = [100.6] + [100.0] * 5 + [99.4] + [100.0] * 10
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 1.0,
                "t0_slots_max_rounds": 1,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
                "y_tau_exit_price_skip_sell_then_buy": True,
                "y_tau_exit_price_mult_sell_then_buy": 1.0,
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
            leg2_target_px=99.5,
            y_tau=2.0,
        )
        self.assertGreaterEqual(int(path.get("sold_qty") or 0), 100)
        buys = [
            t
            for t in (path.get("trades") or [])
            if isinstance(t, dict) and str(t.get("side") or "").endswith("buy")
        ]
        self.assertTrue(buys, path)
        # trigger 价应贴冻结 99.5（非 τ≈102）
        self.assertAlmostEqual(float(buys[0].get("trigger") or 0), 99.5, places=2)

    def test_multi_round_scan_does_not_restore_sellable_from_future_cover(self):
        """反T 第一轮卖光后，扫描态不可因午后回补提前恢复可卖再开第二轮。"""
        # 两根早盘上破 + 午后下破回补
        closes = [100.6, 100.7] + [100.0] * 10 + [99.4] + [100.0] * 8
        mins = _mins(closes)
        bar = {
            "date": "2025-01-02",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "prev_close": 100,
        }
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 1.0,
                "t0_slots_max_rounds": 2,
                "t0_max_position_pct": 1.0,
                "fill_mode": "trigger",
                "t0_stop_pct_sell_then_buy": 0,
                "must_cover_same_day_sell_then_buy": True,
                "t0_pm_degrade_sell_then_buy": "15:30",
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        scores = {
            "y_tau": 0.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
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
        # 全部在 11:05 后才破带 → 不开
        closes = [100.0] * 20  # ~到 11:10
        mins = _mins(closes)
        # 强制最后几根破上带但时间 >11:00
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
        cfg = load_t0_rules(
            {
                "t0_close_band_delta_pct": 0.5,
                "t0_round_ratio": 0.2,
                "y_tau_enter": 0.0,
                "y_path_enter": 0.0,
                "y_use_path": False,
            }
        )
        scores = {
            "y_tau": 0.0,
            "y_trade": 0.0,
            "y_nowcast": 0.0,
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
        """满仓：反T卖开供正T买，反T买回失败则两轮都丢，现金不变负。"""
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
        self.assertEqual(merged.get("trades") or [], [])
        by_id = {str(r.get("t0_slot")): r for r in (merged.get("t0_slot_results") or [])}
        self.assertTrue(by_id["r1"].get("skipped"))
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
