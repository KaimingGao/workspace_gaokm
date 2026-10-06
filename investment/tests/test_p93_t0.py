import os
import sys
import json
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.t0.backtest import backtest_t0_on_bars
from core.t0.config import DEFAULT_T0_RULES, load_t0_rules
from core.t0.rules import simulate_t0_day, simulate_t0_on_holdings
from quant.skill.engine import AVAILABLE_TASKS, QuantEngine
from agent.routing import infer_quant_task


def _bar(date, o, h, l, c):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _mins(date, points):
    """points: list of (hhmm, open, high, low, close)."""
    out = []
    for hhmm, o, h, l, c in points:
        hh, mm = divmod(int(hhmm), 100)
        out.append(
            {
                "datetime": f"{date} {hh:02d}:{mm:02d}:00",
                "date": date,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )
    return out


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


def _scores_flat():
    """无窗头 → 缺 ŷ_τw 不开轮。需方向时用 _scores_r。"""
    return {
        "y_tau": 0.0,
        "y_trade": 0.0,
        "y_nowcast": 0.0,
        "nowcast_vs": "open",
        "y_path": 0.05,
    }


def _scores_r(sign):
    """ŷ_τw 符号选向：>0 正T，<0 反T。"""
    s = float(sign)
    return {**_scores_flat(), "y_tau": s, "y_tau_oc": s, **_ytw_heads(up=s >= 0)}


def _mins_hl(date=None, o=100, high=105, low=98, close=101, bar=None):
    """反T：首根阴线 + 负 ŷ_τw → leg1 卖；午后更低下探，15:00 回补。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    dn = o - max(o * 0.0005, 0.05)
    return _mins(
        str(date),
        [
            (935, o, o + 0.02, dn - 0.01, dn),
            (940, dn, dn + 0.02, dn - 0.04, dn - 0.02),
            (945, dn - 0.02, dn, dn - 0.08, dn - 0.05),
            (950, dn - 0.05, dn - 0.02, dn - 0.10, dn - 0.08),
            (1400, dn - 0.08, o, low, min(close, dn - 0.06)),
            (1500, min(close, dn - 0.06), o, low, min(close, low + 0.1)),
        ],
    )


def _mins_lh(date=None, o=100, high=105, low=96, close=101, bar=None):
    """正T：全程阳线 + 正 ŷ_τw → leg1 买；午后冲高，15:00 可回补。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    up = o + max(o * 0.0005, 0.05)
    return _mins(
        str(date),
        [
            (935, o, up + 0.05, o - 0.02, up),
            (940, up, up + 0.08, up - 0.02, up + 0.05),
            (945, up + 0.05, up + 0.12, up, up + 0.10),
            (950, up + 0.10, high, up + 0.05, max(up + 0.12, min(high, o + 1.5))),
            (1400, up + 0.12, high, low, max(close, up + 0.15)),
            (1500, max(close, up + 0.15), high, low, max(close, up + 0.15)),
        ],
    )


def _rules(**kwargs):
    """单测默认 first_touch；关中点追价以免干扰路径用例。"""
    base = {
        "path_mode": "first_touch",
        "t0_pm_degrade_sell_then_buy": "",
        "t0_pm_degrade_buy_then_sell": "",
        # 路径用例默认关止损/锁赢，避免夹具回踩/冲高误触；止损/锁赢单测显式打开
        "t0_stop_pct_buy_then_sell": 0,
        "t0_stop_pct_sell_then_buy": 0,
        "t0_lock_win_pct_buy_then_sell": 0,
        "t0_lock_win_pct_sell_then_buy": 0,
        "t0_slots_enabled": True,
        "t0_close_band_delta_pct": 0.01,
        "t0_round_ratio": 1.0,
        "t0_slots_max_rounds": 1,
        "t0_max_position_pct": 1.0,
        # 0=走旧比例仓，避免路径夹具被生产默认股数改掉
        "y_τc_enter": 0,
        "y_τc_strong": 0,
        "y_τc_enter_amount": 0,
        "y_τc_strong_amount": 0,
        # 路径用例默认零价偏，避免与生产 ±默认纠缠断言
        "y_tau_exit_price_bias_buy_then_sell": 0.0,
        "y_tau_exit_price_bias_sell_then_buy": 0.0,
        # 路径/撮合单测不绑 ŷ_τ / path / TPD 入场；生产默认见 overlay
        "y_tau_enter": 0.0,
        "y_path_enter": 0.0,
        "y_use_path": False,
        "y_tpd_max": 1.0,
        "y_enter_alt_enabled": False,
        "y_t30_strong": 1.0,
        "y_τ30_strong": 1.0,
        "y_t60_strong": 1.0,
        "y_τ60_strong": 1.0,
        "y_t90_strong": 1.0,
        "y_τ90_strong": 1.0,
        "t0_y_τc_target_scale": 1.0,
        # 路径用例与生产「反T当日回补」解耦
        "must_cover_same_day_sell_then_buy": False,
    }
    base.update(kwargs)
    base["path_mode"] = "first_touch"
    if "t0_ratio" in kwargs and "t0_round_ratio" not in kwargs:
        base["t0_round_ratio"] = float(kwargs["t0_ratio"])
    if "min_range_pct" in kwargs:
        if "min_range_pct_sell_then_buy" not in kwargs:
            base["min_range_pct_sell_then_buy"] = kwargs["min_range_pct"]
        if "min_range_pct_buy_then_sell" not in kwargs:
            base["min_range_pct_buy_then_sell"] = kwargs["min_range_pct"]
    return base


def _rules_path(**kwargs):
    """路径/追价用例：关 τ 出场价闸（配置 skip=False → 闸关）。"""
    return _rules(
        y_tau_exit_price_skip_buy_then_sell=False,
        y_tau_exit_price_skip_sell_then_buy=False,
        **kwargs,
    )


class TestT0Core(unittest.TestCase):
    def test_default_fill_mode_trigger(self):
        self.assertEqual(DEFAULT_T0_RULES["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["fill_mode"], "trigger")
        self.assertEqual(load_t0_rules()["path_mode"], "first_touch")

    def test_t0_dir_label(self):
        from core.t0.config import t0_dir_label

        self.assertEqual(t0_dir_label("buy_then_sell"), "正T")
        self.assertEqual(t0_dir_label("sell_then_buy"), "反T")
        self.assertEqual(t0_dir_label(""), "—")

    def test_resolve_fuse_intraday_by_window(self):
        from core.t0.score_policy import resolve_fuse_intraday

        self.assertTrue(resolve_fuse_intraday({}))
        self.assertTrue(resolve_fuse_intraday({"dual_score_window": "intraday"}))
        self.assertFalse(resolve_fuse_intraday({"dual_score_window": "eod_next"}))
        self.assertFalse(resolve_fuse_intraday({"y_score_window": "eod"}))

    def test_resolve_score_as_of_prefers_hist_prior(self):
        from core.t0.score_policy import resolve_score_as_of

        self.assertEqual(
            resolve_score_as_of(
                hist_bars=[{"date": "2026-01-09"}, {"date": "2026-01-10"}],
                day_bar={"date": "2026-01-11"},
            ),
            "2026-01-10",
        )
        self.assertEqual(
            resolve_score_as_of(hist_bars=[], day_bar={"date": "2026-01-11"}),
            "2026-01-11",
        )


    def test_unsuffixed_must_cover_is_ignored(self):
        """无后缀 must_cover 不再改分侧默认。"""
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"must_cover_same_day": False})
        self.assertTrue(cfg["must_cover_same_day_buy_then_sell"])
        self.assertTrue(cfg["must_cover_same_day_sell_then_buy"])
        self.assertNotIn("must_cover_same_day", cfg)

    def test_atr_pct_requires_min_bars(self):
        from core.t0.rules import atr_pct_from_bars

        short = [_bar(f"2024-01-{i:02d}", 100, 102, 98, 101) for i in range(1, 5)]
        self.assertIsNone(atr_pct_from_bars(short, 14))
        long = [_bar(f"2024-01-{i:02d}", 100, 102, 98, 101) for i in range(1, 10)]
        self.assertIsNotNone(atr_pct_from_bars(long, 14))

    def test_reverse_defer_prefix_does_not_book_exposure(self):
        """P2-3：must_cover + defer 盘中前缀未到收盘窗 → 不误记敞口（等后续 K）。"""
        from core.t0.minute_path import _first_touch_buy_then_sell, _touch_path_complete

        d = "2024-02-05"
        bar = _bar(d, 100, 103, 96, 99)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),
                (940, 100, 100.0, 98.4, 98.7),
                (1040, 98.7, 99.0, 98.5, 98.8),
            ],
        )
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            cash=50000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "y_prefix_upbar_ratio_buy_then_sell": 0.0,
                "y_prefix_downbar_ratio_sell_then_buy": 0.0,
                "t0_confirm_dev_pct": 0.0,
                "t0_confirm_mom_bars": 1,
                "t0_env_min_range_pct": 0.0,
                "t0_env_min_path_abs": 0.0,
                "t0_env_one_sided_tau_abs": 0.0,
                "t0_env_one_sided_path_abs": 0.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=2.0,
            session_bars=mins,
            session_bar=bar,
            defer_eod=True,
            y_tau=0.5,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), 0)
        self.assertEqual(out.get("exit_reason"), "defer_eod_pending")
        self.assertEqual(float(out.get("exposure_pnl") or 0), 0.0)
        self.assertFalse(_touch_path_complete(out, "buy_then_sell"))

    def test_long_defer_prefix_marks_pending_without_exposure(self):
        """反T defer：已卖未买回且未到 14:55 → defer_eod_pending，不记敞口。"""
        from core.t0.minute_path import _first_touch_sell_then_buy, _touch_path_complete

        d = "2024-02-05"
        bar = _bar(d, 100, 103, 96, 99)
        mins = _mins(
            d,
            [
                (935, 100, 101.5, 99.9, 101.2),  # 触卖
                (940, 101.2, 101.3, 100.8, 101.0),
                (1040, 101.0, 101.2, 100.5, 100.8),  # 买回触不到
            ],
        )
        out = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": False,
                "lot_size": 100,
                "t0_pm_degrade": "",
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=2.0,
            t0_ratio=0.4,
            session_bars=mins,
            session_bar=bar,
            defer_eod=True,
            y_tau=-0.5,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertEqual(int(out.get("covered_qty") or 0), 0)
        self.assertEqual(out.get("exit_reason"), "defer_eod_pending")
        self.assertEqual(float(out.get("exposure_pnl") or 0), 0.0)
        self.assertFalse(_touch_path_complete(out, "sell_then_buy"))

    def test_touch_path_complete_abandon_cover_cap(self):
        """正T must_cover 但可卖旧=0 的防御出口：记敞口且视为完成。"""
        from core.t0.minute_path import _touch_path_complete

        out = {
            "bought_qty": 100,
            "sold_back_qty": 0,
            "exit_reason": "abandon_cover_cap",
            "exposure_pnl": 12.5,
        }
        self.assertTrue(_touch_path_complete(out, "buy_then_sell"))

    def test_bts_lock_win_sells_when_close_beats_pct(self):
        """正T：收盘涨过锁赢%则提前卖旧仓（不必触 C_τ）。"""
        from core.t0.minute_path import _first_touch_buy_then_sell

        d = "2024-03-01"
        bar = _bar(d, 100, 103, 99, 102.1)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),  # leg1 买 100
                (940, 100.0, 100.5, 100.0, 100.4),  # 延迟根
                (945, 101.0, 102.3, 101.0, 102.1),  # +2.1% ≥ 2%，高点未触 C_τ
            ],
        )
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            cash=50000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "t0_stop_pct_buy_then_sell": 1.2,
                "t0_stop_arm_bars": 1,
                "t0_lock_win_arm_bars": 1,
                "t0_lock_win_pct_buy_then_sell": 2.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=2.0,
            session_bars=mins,
            session_bar=bar,
            y_tau=3.0,
            leg2_target_px=103.0,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), int(out.get("bought_qty") or 0))
        self.assertEqual(out.get("exit_reason"), "lock_win", out)
        trades = out.get("trades") or []
        sell = next(t for t in trades if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "lock_win")
        self.assertAlmostEqual(sell.get("price") or 0, 102.1, places=3)

    def test_bts_lock_win_uses_own_arm_bars(self):
        """正T：锁赢延迟根与止损独立；arm=3 时第二根不锁赢。"""
        from core.t0.minute_path import _first_touch_buy_then_sell

        d = "2024-03-01"
        bar = _bar(d, 100, 103, 99, 102.1)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),
                (940, 100.0, 100.5, 100.0, 100.4),
                (945, 101.0, 102.3, 101.0, 102.1),
            ],
        )
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            cash=50000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": False,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "t0_stop_pct_buy_then_sell": 0,
                "t0_stop_arm_bars": 0,
                "t0_lock_win_arm_bars": 3,
                "t0_lock_win_pct_buy_then_sell": 2.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=2.0,
            session_bars=mins,
            session_bar=bar,
            y_tau=3.0,
            leg2_target_px=103.0,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), 0)
        self.assertNotEqual(out.get("exit_reason"), "lock_win")

    def test_bts_lock_win_does_not_steal_tau_target(self):
        """正T：本根高点已触 C_τ 时走目标腿，不抢锁赢。"""
        from core.t0.minute_path import _first_touch_buy_then_sell

        d = "2024-03-01"
        bar = _bar(d, 100, 103, 99, 103)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),
                (940, 100.0, 100.5, 100.0, 100.4),
                (945, 101.0, 103.2, 101.0, 102.1),  # close 过锁赢，high 触 C_τ
            ],
        )
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            cash=50000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "t0_stop_pct_buy_then_sell": 1.2,
                "t0_stop_arm_bars": 1,
                "t0_lock_win_pct_buy_then_sell": 2.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=3.0,
            session_bars=mins,
            session_bar=bar,
            y_tau=3.0,
            leg2_target_px=103.0,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out.get("exit_reason"), "trigger", out)
        sell = next(t for t in (out.get("trades") or []) if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "trigger")
        self.assertAlmostEqual(float(sell.get("price") or 0), 103.0, places=3)

    def test_bts_lock_win_below_pct_does_not_sell(self):
        """正T：涨幅未过锁赢%时不提前卖。"""
        from core.t0.minute_path import _first_touch_buy_then_sell

        d = "2024-03-01"
        bar = _bar(d, 100, 101.6, 99.8, 101.5)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),
                (940, 100.0, 100.5, 100.0, 100.4),
                (945, 100.4, 101.6, 100.2, 101.5),  # +1.5% < 2%
            ],
        )
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            cash=50000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": False,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "t0_stop_pct_buy_then_sell": 1.2,
                "t0_stop_arm_bars": 1,
                "t0_lock_win_pct_buy_then_sell": 2.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=0.5,
            session_bars=mins,
            session_bar=bar,
            y_tau=3.0,
            leg2_target_px=103.0,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), 0)
        self.assertNotEqual(out.get("exit_reason"), "lock_win")

    def test_stb_lock_win_buys_when_close_beats_pct(self):
        """反T：收盘跌过锁赢%则提前买回。"""
        from core.t0.minute_path import _first_touch_sell_then_buy

        d = "2024-03-01"
        bar = _bar(d, 100, 101, 97, 97.8)
        mins = _mins(
            d,
            [
                (935, 100, 101.0, 99.9, 100.0),  # leg1 卖 100
                (940, 99.8, 100.0, 99.5, 99.6),  # 延迟根
                (945, 99.0, 98.5, 97.7, 97.8),  # −2.2% ≤ −2%，低点未触 C_τ
            ],
        )
        out = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=100.0,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "t0_stop_pct_sell_then_buy": 1.2,
                "t0_stop_arm_bars": 1,
                "t0_lock_win_arm_bars": 1,
                "t0_lock_win_pct_sell_then_buy": 2.0,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=3.0,
            t0_ratio=0.4,
            cash=50000,
            session_bars=mins,
            session_bar=bar,
            y_tau=-3.0,
            leg2_target_px=97.0,
            leg1_gate_at=lambda i: i == 0,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertEqual(int(out.get("covered_qty") or 0), int(out.get("sold_qty") or 0))
        self.assertEqual(out.get("exit_reason"), "lock_win", out)
        buy = next(t for t in (out.get("trades") or []) if t.get("side") == "t0_buy")
        self.assertEqual(buy.get("leg_kind"), "lock_win")
        self.assertAlmostEqual(float(buy.get("price") or 0), 97.8, places=3)

    def test_holdings_shared_cash_order_is_sorted_by_code(self):
        """共享现金池：持仓按 stock_code 排序执行，与 paper 插入序无关。"""
        from unittest.mock import patch

        paper = {
            "cash": 15000,
            "holdings": [
                {"stock_code": "600519", "stock_name": "B", "shares": 1000, "cost": 100},
                {"stock_code": "000001", "stock_name": "A", "shares": 1000, "cost": 10},
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 1.0,
                    "direction": "buy_then_sell",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.1,
                    "must_cover_same_day": True,
                    "y_tpd_max": 1.0,
                    "y_t30_strong": 1.0,
                    "y_t60_strong": 1.0,
                    "y_t90_strong": 1.0,
                    "y_path_abandon_bars": 2,
                    "y_prefix_upbar_ratio_buy_then_sell": 0.0,
                    "y_prefix_downbar_ratio_sell_then_buy": 0.0,
                    "t0_confirm_dev_pct": 0.0,
                    "t0_confirm_mom_bars": 1,
                    "t0_env_min_range_pct": 0.0,
                    "t0_env_min_path_abs": 0.0,
                    "t0_env_one_sided_tau_abs": 0.0,
                    "t0_env_one_sided_path_abs": 0.0,
                    "t0_pm_degrade": "",
                    "t0_slots_max_rounds": 0,
                }
            },
        }
        d = "2024-03-01"
        bar_a = _bar(d, 10, 11, 9, 10.5)
        bar_b = _bar(d, 100, 110, 90, 105)
        mins_a = _mins(
            d,
            [
                (935, 10, 10.1, 9.9, 10.0),
                (940, 10.0, 10.05, 9.7, 9.75),
                (1500, 9.75, 10.2, 9.7, 10.1),
            ],
        )
        mins_b = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 100.0),
                (940, 100.0, 100.05, 97.0, 97.5),
                (1500, 97.5, 102, 97.0, 101),
            ],
        )
        seen = []

        real_sim = simulate_t0_day

        def _track(**kwargs):
            seen.append(str(kwargs.get("stock_code") or ""))
            return real_sim(**kwargs)

        with patch("core.t0.rules.simulate_t0_day", side_effect=_track):
            with patch("core.paper.tplus1.session_date", return_value=d):
                out = simulate_t0_on_holdings(
                    paper,
                    bars_by_code={"600519": bar_b, "000001": bar_a},
                    minute_bars_by_code={"600519": mins_b, "000001": mins_a},
                    dry_run=True,
                )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(seen, ["000001", "600519"])
        codes = [r.get("stock_code") for r in (out.get("results") or [])]
        self.assertEqual(codes, ["000001", "600519"])

    def test_default_t0_rules_match_paper_overlay(self):
        d = load_t0_rules()
        self.assertNotIn("must_cover_same_day", d)
        self.assertTrue(d["must_cover_same_day_sell_then_buy"])
        self.assertTrue(d["must_cover_same_day_buy_then_sell"])
        self.assertEqual(d["t0_pm_degrade_sell_then_buy"], "13:00")
        self.assertEqual(d["t0_pm_degrade_buy_then_sell"], "13:00")
        self.assertNotIn("t0_pm_degrade", d)
        self.assertEqual(d["t0_pm_chase_interval_min_sell_then_buy"], 5)
        self.assertEqual(d["t0_pm_chase_interval_min_buy_then_sell"], 5)
        self.assertAlmostEqual(d["t0_stop_pct_buy_then_sell"], 1.2)
        self.assertAlmostEqual(d["t0_stop_pct_sell_then_buy"], 1.2)
        self.assertEqual(d["t0_stop_arm_bars"], 6)
        self.assertTrue(d["t0_stop_on_close"])
        self.assertEqual(d["t0_lock_win_arm_bars"], 6)
        self.assertAlmostEqual(d["t0_lock_win_pct_buy_then_sell"], 2.0)
        self.assertAlmostEqual(d["t0_lock_win_pct_sell_then_buy"], 2.0)
        self.assertAlmostEqual(d["y_tw_enter"], 2.0)
        self.assertAlmostEqual(d["y_τc_enter"], 0.5)
        self.assertAlmostEqual(d["y_τc_strong"], 1.0)
        self.assertEqual(int(d["y_τc_enter_amount"]), 20000)
        self.assertEqual(int(d["y_τc_strong_amount"]), 40000)
        self.assertAlmostEqual(d["y_tw_midpoint"], 47.0)
        self.assertAlmostEqual(d["y_tw_vote_margin"], 5.0)
        self.assertEqual(d["t0_slots_max_rounds"], 5)
        self.assertAlmostEqual(float(d.get("t0_close_band_delta_pct") or 0), 0.5)
        self.assertAlmostEqual(float(d.get("t0_round_ratio") or 0), 0.4)
        self.assertAlmostEqual(float(d.get("t0_price_space_max_dev_pct") or 0), 0.0)
        self.assertAlmostEqual(float(d.get("t0_price_space_prev_dev_pct") or 0), 5.0)
        self.assertAlmostEqual(float(d.get("t0_y_τc_target_scale") or 0), 2.0)
        self.assertAlmostEqual(
            float(load_t0_rules({"t0_y_τc_target_scale": 150})["t0_y_τc_target_scale"]),
            100.0,
        )
        self.assertEqual(d["y_tau_exit_price_mult_buy_then_sell"], 1.0)
        self.assertTrue(d["y_tau_exit_price_skip_buy_then_sell"])
        self.assertEqual(d["y_tau_exit_price_mult_sell_then_buy"], 1.0)
        self.assertTrue(d["y_tau_exit_price_skip_sell_then_buy"])
        self.assertEqual(d["y_tau_exit_price_bias_buy_then_sell"], 1.0)
        self.assertEqual(d["y_tau_exit_price_bias_sell_then_buy"], -1.0)
        self.assertEqual(d["y_tau_exit_price_move_max_buy_then_sell"], 100.0)
        self.assertTrue(d["t0_slots_enabled"])
        self.assertEqual(len(d["t0_slots"]), 5)
        self.assertEqual(d["t0_slots"][0]["hm"], "11:00")
        self.assertEqual(d["t0_slots"][0]["prefix_bars"], 18)
        self.assertAlmostEqual(float(d["t0_slots"][0]["ratio"]), 0.20)
        self.assertEqual(d["t0_slots"][-1]["hm"], "11:00")
        self.assertEqual(d["t0_slots"][-1]["prefix_bars"], 18)
        self.assertTrue(d["t0_pm_chase_cap_leg1_sell_then_buy"])
        self.assertTrue(d["t0_pm_chase_cap_leg1_buy_then_sell"])
        self.assertAlmostEqual(d["residual_w_τc"], 0.5)
        self.assertAlmostEqual(d["residual_w_oc"], 0.5)
        self.assertTrue(d["enabled"])

    def test_sell_and_cover_same_day(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                fill_mode="optimistic",
                direction="sell_then_buy",
                ref="cost",
                min_range_pct=1.0,
                must_cover_same_day_sell_then_buy=True,
            ),
            stock_code="",
            scores=_scores_r(-0.5),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 400)
        self.assertEqual(out["shares_end"], 1000)
        self.assertEqual(len(out["trades"]), 2)
        self.assertGreater(out["pnl"], 0)

    def test_trigger_fill_more_conservative_than_optimistic(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        common = _rules(
            t0_ratio=0.4,
            direction="sell_then_buy",
            min_range_pct=1.0,
        )
        opt = simulate_t0_day(
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "optimistic"},
        minute_bars=_mins_hl(bar=bar))
        trg = simulate_t0_day(
            bar=bar, shares=1000, cost=100, rules={**common, "fill_mode": "trigger"},
        minute_bars=_mins_hl(bar=bar))
        self.assertGreaterEqual(opt["pnl"], trg["pnl"])



    def test_no_trigger_when_range_ok_but_levels_miss(self):
        """缺 ŷ_τw 即使日振幅够也不开 leg1。"""
        bar = _bar("2026-01-10", 100, 105, 98.5, 100)
        mins = _mins(
            "2026-01-10",
            [
                (935, 100, 100.2, 99.0, 99.5),  # C 破下沿；无窗头 → 缺 ŷ_τw
                (940, 100, 100.1, 99.9, 100.0),
                (945, 100, 100.1, 99.9, 100.0),
                (950, 100, 100.1, 99.9, 100.0),
                (1400, 100, 104.5, 98.5, 100.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(),
            minute_bars=mins,
            scores=_scores_flat(),
        )
        self.assertTrue(out["success"])
        self.assertEqual(out.get("sold_qty") or 0, 0)
        self.assertFalse(out.get("trades") or [])
        self.assertTrue(out.get("skipped"))
        blob = str(out.get("reason") or "") + str(out.get("close_band_last_y_tw_skip") or "")
        self.assertIn("缺 ŷ_τw", blob)

    def test_tplus1_sellable_cap(self):
        bar = _bar("2026-01-10", 100, 110, 90, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            sellable_shares=100,
            rules=_rules(
                t0_ratio=1.0,
                direction="sell_then_buy",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar),
            scores=_scores_r(-0.5),
        )
        self.assertEqual(out["sold_qty"], 100)

    def test_small_holding_ratio_bumps_to_one_lot(self):
        """100 股 × 40% 取整为 0 时，应抬到 1 手，避免小仓静默无成交。"""
        from core.t0.rules import _t0_qty_lots

        self.assertEqual(_t0_qty_lots(100, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(200, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(250, 0.4, 100), 100)
        self.assertEqual(_t0_qty_lots(300, 0.4, 100), 100)
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=100,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                t0_round_ratio=1.0,
                fill_mode="optimistic",
                direction="sell_then_buy",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar),
            scores=_scores_r(-0.5),
        )
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["sold_qty"], 100)

    def test_buy_then_sell_sells_old(self):
        """正T：低吸加仓 + 卖旧底仓；完成往返后仓位不变。"""
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                t0_ratio=0.4,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            scores=_scores_r(0.5),
            stock_code="",
            minute_bars=_mins_lh(bar=bar),
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["direction_used"], "buy_then_sell")
        self.assertGreater(out["bought_qty"], 0)
        self.assertEqual(out["sold_back_qty"], out["bought_qty"])
        self.assertEqual(out["shares_end"], 1000)
        self.assertGreater(out["pnl"], 0)
        notes = " ".join(str(t.get("note") or "") for t in out.get("trades") or [])
        self.assertIn("旧底仓", notes)
        self.assertNotIn("卖回加的", notes)

    def test_buy_then_sell_requires_sellable_old(self):
        """无可卖旧仓时正T不能靠卖当日新买股完成。"""
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            sellable_shares=0,
            rules=_rules(
                t0_ratio=0.4,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar),
            scores=_scores_r(0.5),
        )
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("bought_qty") or 0, 0)
        slots = out.get("t0_slot_results") or []
        reason = str((slots[0].get("reason") if slots else None) or out.get("reason") or "")
        self.assertIn("T+1", reason)
        self.assertIn("可卖旧仓", reason)
        self.assertNotIn("动仓不足", reason)

    def test_buy_then_sell_sellable_caps_round_trip(self):
        bar = _bar("2026-01-10", 99, 103, 97, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            sellable_shares=100,
            rules=_rules(
                t0_ratio=0.4,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar),
            scores=_scores_r(0.5),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["bought_qty"], 100)
        self.assertEqual(out["sold_back_qty"], 100)
        self.assertEqual(out["shares_end"], 1000)

    def test_exposure_when_uncovered(self):
        bar = _bar("2026-01-10", 100, 105, 103, 104.5)
        o = 100.0
        dn = o - max(o * 0.0001 + 0.04, 0.05)
        mins = _mins(
            "2026-01-10",
            [
                (935, o, o + 0.02, dn - 0.01, dn),
                (940, dn, dn + 0.02, dn - 0.04, dn - 0.02),
                (945, dn - 0.02, dn, dn - 0.08, dn - 0.05),
                (950, dn - 0.05, dn - 0.02, dn - 0.10, dn - 0.08),
                (1400, 104, 104.8, 103.2, 104.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                direction="sell_then_buy",
                fill_mode="trigger",
                must_cover_same_day=False,
                must_cover_same_day_sell_then_buy=False,
                min_range_pct=0.5,
            ),
            minute_bars=mins,
            scores=_scores_r(-0.5),
        )
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 0)
        self.assertNotEqual(out["exposure_pnl"], 0)

    def test_backtest_runs_and_quality_metrics(self):
        bars = [
            _bar(f"d{i}", 100 + i * 0.1, 110, 90, 100 + i * 0.05) for i in range(20)
        ]
        mins = {b["date"]: _mins_hl(bar=b) for b in bars}
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(direction="sell_then_buy", min_range_pct=1.0),
            minute_by_date=mins,
            require_minute=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(report["t0_trade_days"], 1)
        self.assertIn("t0_pnl_total", report)
        self.assertNotIn("optimistic_compare", report)
        self.assertIn("exposure_pnl_total", report)
        self.assertIn("cover_rate_pct", report)
        self.assertIn("participate_rate_pct", report)
        self.assertIn("avg_pnl_per_trade_day", report)
        self.assertIn("t0_pnl_with_exposure", report)
        self.assertEqual(
            report["t0_pnl_with_exposure"],
            round(report["t0_pnl_total"] + report["exposure_pnl_total"], 2),
        )
        self.assertNotIn("round_trip_profit_rate_pct", report)
        sample = report.get("trade_days_sample") or []
        if report["t0_trade_days"] > 0 and sample:
            self.assertTrue(any((d.get("trades") or []) for d in sample))
            legs = next(d.get("trades") for d in sample if d.get("trades"))
            self.assertIn("price", legs[0])
            row = next(d for d in sample if d.get("trades"))
            self.assertIn("sell_price", row)


    def test_backtest_eval_window_decoupled_from_score_warmup(self):
        from datetime import date, timedelta

        base = date(2025, 1, 2)
        bars_all = [
            _bar((base + timedelta(days=i)).isoformat(), 100, 106, 96, 101)
            for i in range(45)
        ]
        eval_10 = bars_all[-10:]
        mins = {b["date"]: _mins_hl(bar=b) for b in eval_10}
        captured: list = []

        def _fake_scores(code, *, hist_bars, **kw):
            captured.append(len(hist_bars or []))
            return {
                "y_eod": 0.5,
                "y_tau": 0.35,
                "y_trade": 0.4,
                "y_on": 0.2,
                "y_nowcast": None,
                "y_check": None,
                "eod_trust": None,
            }

        rules = _rules(direction="dual_y", use_atr=False, min_range_pct=0.5)
        with patch("core.t0.score_policy.resolve_scores_for_code", side_effect=_fake_scores):
            report = backtest_t0_on_bars(
                eval_10,
                bars_history=bars_all,
                eval_lookback=10,
                initial_shares=1000,
                initial_cost=100,
                rules=rules,
                minute_by_date=mins,
                require_minute=True,
                stock_code="600519",
            )
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("score_warmup_bars"), 35)
        self.assertTrue(captured)
        self.assertEqual(captured[-1], 44)

        captured.clear()
        with patch("core.t0.score_policy.resolve_scores_for_code", side_effect=_fake_scores):
            backtest_t0_on_bars(
                eval_10,
                initial_shares=1000,
                initial_cost=100,
                rules=rules,
                minute_by_date=mins,
                require_minute=True,
                stock_code="600519",
            )
        self.assertEqual(captured[-1], 9)


    def test_backtest_resolve_defaults_dual_y(self):
        from core.execution import resolve_t0_rules, strip_execution_meta

        cfg = strip_execution_meta(
            resolve_t0_rules(rules={"use_atr": False}, channel="backtest", has_minute=False)
        )
        self.assertEqual(cfg.get("direction"), "dual_y")
        self.assertEqual(cfg.get("path_mode"), "first_touch")


    def test_minute_first_touch_covers_after_sell(self):
        bar = _bar("2024-01-03", 100, 105, 97, 100)
        minutes = _mins_hl(bar=bar)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day_sell_then_buy=True,
            ),
            scores=_scores_r(-0.5),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertEqual(int(out.get("covered_qty") or 0), int(out.get("sold_qty") or 0))
        self.assertGreater(float(out.get("pnl") or 0), 0)
        trades = out.get("trades") or []
        self.assertTrue(all(t.get("at") for t in trades), trades)
        self.assertEqual(trades[0].get("leg_kind"), "trigger")
        self.assertIn(trades[1].get("leg_kind"), ("trigger", "eod_cover", "pm_chase", "lock_win", "stop"))
        self.assertIn("09:35", str(trades[0].get("at") or ""))
        self.assertGreater(len(trades), 1)

    def test_minute_forced_eod_cover_stamps_at(self):
        """正T：must_cover 时收盘强制卖旧仓，at 落在 15:00。"""
        bar = _bar("2024-01-03", 100, 105, 95, 100)
        minutes = _mins(
            "2024-01-03",
            [
                (935, 100, 100.2, 99.5, 99.6),
                (940, 99.6, 99.7, 95.0, 95.5),
                (945, 95.5, 96.2, 95.3, 96.0),  # 阳
                (950, 96.0, 96.8, 95.8, 96.5),  # 阳 → 买
                (1500, 96.5, 97.0, 96.0, 96.2),  # 够不到卖触发 → eod
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=200000,
            minute_bars=minutes,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="",
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out["success"], out)
        sells = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("sell")]
        self.assertTrue(sells, out)
        eod = [t for t in sells if t.get("leg_kind") == "eod_cover"]
        self.assertEqual(len(eod), 1, out)
        self.assertIn("15:00", str(eod[0].get("at") or ""))

    def test_defer_eod_skips_midday_force_cover(self):
        """盘中 defer_eod：半日分钟末根不到 14:55 不强平。"""
        d = "2026-08-27"
        bar = _bar(d, 34.15, 34.59, 33.85, 34.52)
        o = 34.15
        dn = o - max(o * 0.0001 + 0.04, 0.05)
        mins = _mins(
            d,
            [
                (935, o, o + 0.05, dn - 0.01, dn),
                (940, dn, dn + 0.02, dn - 0.02, dn + 0.01),
                (945, dn + 0.01, dn + 0.02, dn - 0.01, dn),
                (1040, dn, dn + 0.02, dn - 0.02, dn + 0.005),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=10000,
            cost=34.15,
            cash=500000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.5,
                must_cover_same_day=True,
                t0_pm_degrade="",
                t0_ratio=1.0,
            ),
            scores=_scores_r(0.5),
            defer_eod=True,
        )
        self.assertTrue(out.get("success"), out.get("reason"))
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), 0)
        slots = out.get("t0_slot_results") or []
        exit_reason = out.get("exit_reason") or (slots[0].get("exit_reason") if slots else None)
        self.assertIn(exit_reason, ("defer_eod_pending", "incomplete_session"), out)
        sells = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("sell")]
        self.assertEqual(len(sells), 0)

    def test_buy_then_sell_truncated_minutes_no_daily_close_lookahead(self):
        """5m 午前截断：不强平到日线收盘（禁前视）；记 incomplete_session。"""
        bar = _bar("2026-08-27", 34.15, 34.59, 33.85, 34.52)
        o = 34.15
        dn = o - max(o * 0.0001 + 0.04, 0.05)
        mins = _mins(
            "2026-08-27",
            [
                (935, o, o + 0.05, dn - 0.01, dn),
                (940, dn, dn + 0.02, dn - 0.02, dn + 0.01),
                (945, dn + 0.01, dn + 0.02, dn - 0.01, dn),
                (1040, dn, dn + 0.02, dn - 0.02, dn + 0.005),
                (1100, dn + 0.005, dn + 0.02, dn - 0.01, dn + 0.01),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=10000,
            cost=34.15,
            cash=500000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.5,
                must_cover_same_day=True,
                t0_pm_degrade="",
                t0_ratio=1.0,
            ),
            scores=_scores_r(0.5),
            defer_eod=False,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        eod = [
            t
            for t in (out.get("trades") or [])
            if t.get("leg_kind") == "eod_cover"
        ]
        self.assertEqual(len(eod), 0, out)
        slots = out.get("t0_slot_results") or []
        exit_reason = out.get("exit_reason") or (slots[0].get("exit_reason") if slots else None)
        self.assertEqual(exit_reason, "incomplete_session", out)
        buy_px = next(
            float(t["price"])
            for t in (out.get("trades") or [])
            if str(t.get("side")).endswith("buy")
        )
        qty = float(out.get("bought_qty") or 0)
        last_close = 34.11
        exp_last = round((last_close - buy_px) * qty, 2)
        exp_daily = round((34.52 - buy_px) * qty, 2)
        self.assertAlmostEqual(float(out.get("exposure_pnl") or 0), exp_last, places=2)
        self.assertNotAlmostEqual(exp_last, exp_daily, places=2)


    def test_sell_then_buy_abandons_cover_when_buyback_misses(self):
        """反T：卖出后买不回 → 放弃回补（减仓落袋），不收盘强买。"""
        bar = _bar("2024-01-03", 100, 105, 99, 100)
        o = 100.0
        dn = o - max(o * 0.0001 + 0.04, 0.05)
        minutes = _mins(
            "2024-01-03",
            [
                (935, o, o + 0.05, dn - 0.01, dn),
                (940, dn, dn + 0.02, dn - 0.04, dn - 0.02),
                (945, dn - 0.02, dn, dn - 0.08, dn - 0.05),
                (950, dn - 0.05, 105.0, dn - 0.10, dn - 0.08),
                (1500, 104, 104, 103.5, 100),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                must_cover_same_day_sell_then_buy=False,
                t0_pm_degrade="",
            ),
            scores=_scores_r(-0.5),
        )
        self.assertTrue(out["success"])
        self.assertGreater(out.get("sold_qty") or 0, 0)
        self.assertEqual(out.get("covered_qty") or 0, 0)
        self.assertEqual(out.get("uncovered_qty"), out.get("sold_qty"))
        self.assertNotEqual(out.get("exposure_pnl"), 0)

    def test_deferred_eod_allows_afternoon_trigger_buy(self):
        """阴线开反T后不预设买回价；午后中点追价完成第二腿。"""
        d = "2024-01-03"
        bar = _bar(d, 100, 106, 99, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 99.7),  # 阴 → 反T；C_τ≈99.5，早盘低点不触
                (940, 99.7, 99.8, 99.65, 99.7),
                (945, 99.7, 99.8, 99.65, 99.7),
                (950, 99.7, 99.8, 99.65, 99.7),
                (1100, 99.7, 99.8, 99.65, 99.7),
                (1430, 101, 101.2, 99.6, 99.8),  # 午后中点追价抬到可成交
                (1500, 100, 100, 99.5, 100),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=mins,
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                t0_pm_degrade="13:00",
                t0_pm_degrade_sell_then_buy="13:00",
                must_cover_same_day_sell_then_buy=False,
            ),
            scores=_scores_r(-0.5),
        )
        self.assertTrue(out["success"], out.get("reason"))
        trades = out.get("trades") or []
        buys = [t for t in trades if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1, trades)
        self.assertEqual(buys[0].get("leg_kind"), "pm_chase")
        self.assertIn("14:30", str(buys[0].get("at") or ""))
        self.assertEqual(out.get("range_mode"), "close_band")






    def test_tau_exit_price_signed_bounds(self):
        """第二腿：正T卖价>bound / 反T买价<bound。"""
        from core.t0.minute_path import tau_leg2_fill_price_ok

        z_b = {
            "y_tau_exit_price_bias_buy_then_sell": 0.0,
            "y_tau_exit_price_skip_buy_then_sell": True,
            "y_tau_exit_price_mult_buy_then_sell": 1.0,
        }
        z_s = {
            "y_tau_exit_price_bias_sell_then_buy": 0.0,
            "y_tau_exit_price_skip_sell_then_buy": True,
            "y_tau_exit_price_mult_sell_then_buy": 1.0,
        }
        sell_ok = tau_leg2_fill_price_ok(
            fill_px=100.6,
            ref=100.0,
            y_tau=0.5,
            direction="buy_then_sell",
            mult=1.0,
            cfg=z_b,
        )
        self.assertTrue(sell_ok["ok"], sell_ok)
        self.assertAlmostEqual(sell_ok["bound_px"], 100.5, places=4)

        sell_bad = tau_leg2_fill_price_ok(
            fill_px=100.4,
            ref=100.0,
            y_tau=0.5,
            direction="buy_then_sell",
            mult=1.0,
            cfg=z_b,
        )
        self.assertFalse(sell_bad["ok"], sell_bad)

        buy_ok = tau_leg2_fill_price_ok(
            fill_px=99.4,
            ref=100.0,
            y_tau=-0.5,
            direction="sell_then_buy",
            mult=1.0,
            cfg=z_s,
        )
        self.assertTrue(buy_ok["ok"], buy_ok)
        self.assertAlmostEqual(buy_ok["bound_px"], 99.5, places=4)

        buy_bad = tau_leg2_fill_price_ok(
            fill_px=99.6,
            ref=100.0,
            y_tau=-0.5,
            direction="sell_then_buy",
            mult=1.0,
            cfg=z_s,
        )
        self.assertFalse(buy_bad["ok"], buy_bad)

    def test_tau_price_gate_bias_and_clamp(self):
        """出场价闸：move=clamp(ŷ_τ×裕度,min,max)+price_bias。"""
        from core.t0.minute_path import tau_exit_bound_px, tau_leg2_fill_price_ok

        cfg_bias = {
            "y_tau_exit_price_skip_buy_then_sell": True,
            "y_tau_exit_price_mult_buy_then_sell": 2.0,
            "y_tau_exit_price_bias_buy_then_sell": 0.15,
            "y_tau_exit_price_move_min_buy_then_sell": -5.0,
            "y_tau_exit_price_move_max_buy_then_sell": 5.0,
        }
        bound = tau_exit_bound_px(
            ref=100.0,
            y_tau=0.62,
            direction="buy_then_sell",
            cfg=cfg_bias,
        )
        # clamp(0.62×2, -5, 5)+0.15 = 1.24+0.15 = 1.39 → 101.39
        self.assertAlmostEqual(bound, 101.39, places=4)

        cfg_clamp = {
            "y_tau_exit_price_skip_buy_then_sell": True,
            "y_tau_exit_price_mult_buy_then_sell": 1.0,
            "y_tau_exit_price_bias_buy_then_sell": 0.0,
            "y_tau_exit_price_move_min_buy_then_sell": 0.0,
            "y_tau_exit_price_move_max_buy_then_sell": 1.0,
        }
        exit_bound = tau_exit_bound_px(
            ref=100.0,
            y_tau=2.0,
            direction="buy_then_sell",
            cfg=cfg_clamp,
        )
        # clamp(2.0×1, 0, 1)+0 = 1.0 → 101.0
        self.assertAlmostEqual(exit_bound, 101.0, places=4)

        gate = tau_leg2_fill_price_ok(
            fill_px=101.1,
            ref=100.0,
            y_tau=2.0,
            direction="buy_then_sell",
            cfg=cfg_clamp,
        )
        self.assertTrue(gate["ok"], gate)
        self.assertAlmostEqual(gate["price_bias"], 0.0, places=4)
        self.assertAlmostEqual(gate["move_pct"], 1.0, places=4)

    def test_reverse_t_bias_algebraic_signed(self):
        """反T价偏代数可正可负；默认负偏压低 bound。"""
        from core.t0.config import load_t0_rules
        from core.t0.minute_path import tau_exit_bound_px, _tau_price_gate_bias

        d = load_t0_rules({"y_tau_exit_price_bias_sell_then_buy": -1.0})
        self.assertAlmostEqual(d["y_tau_exit_price_bias_sell_then_buy"], -1.0)
        applied = _tau_price_gate_bias(
            {"y_tau_exit_price_bias_sell_then_buy": -1.0},
            direction="sell_then_buy",
        )
        self.assertAlmostEqual(applied, -1.0)
        # ŷ_τ=-1.7, mult=1 → clamp(-1.7)+(-1)=−2.7 → bound 97.3
        bound = tau_exit_bound_px(
            ref=100.0,
            y_tau=-1.7,
            direction="sell_then_buy",
            cfg={
                "y_tau_exit_price_skip_sell_then_buy": True,
                "y_tau_exit_price_mult_sell_then_buy": 1.0,
                "y_tau_exit_price_bias_sell_then_buy": -1.0,
            },
        )
        self.assertAlmostEqual(bound, 97.3, places=4)

    def test_attach_y_path_clears_retired_y_hl(self):
        """ŷ_hl 已下线：_attach_y_path_to_item 清掉旧字段，不再写分。"""
        from core.t0.score_policy import _attach_y_path_to_item

        item = {
            "features_tau": {"gap_pct": 1.0, "yclose_loc": 0.5},
            "gap_pct": 1.0,
            "y_hl": 0.05,
            "predicted_score_hl": 0.05,
            "y_hl_status": "ok",
        }
        _attach_y_path_to_item(item, hist_bars=[])
        self.assertIsNone(item.get("y_hl"))
        self.assertIsNone(item.get("predicted_score_hl"))
        self.assertIsNone(item.get("y_hl_status"))

    def test_attach_portrait_y_path_fills_for_score_portrait(self):
        """画像补因果 ŷ_τ_oc；ŷ_hl 已下线不再写入。"""
        from core.t0.score_policy import attach_portrait_dual_scores
        from core.t0.viz import build_score_portrait, extract_scores

        d = "2026-08-01"
        day = {
            "date": d,
            "open": 100.0,
            "close": 101.0,
            "skipped": True,
            # 开盘-only 决策分故意反号：画像须用 portrait 字段
            "scores": {"y_tau": -0.8, "y_tau_oc": -0.8, "gap_pct": 1.0, "y_hl_portrait": 9.9},
            "tau_realized": 1.0,
            "path_realized": 2.0,
        }
        mins = _mins(
            d,
            [
                (935, 100, 100.5, 99.5, 100.2),
                (940, 100.2, 100.8, 100.0, 100.5),
                (1000, 100.5, 101.0, 100.2, 100.8),
                (1030, 100.8, 101.2, 100.5, 101.0),
            ],
        )
        with patch(
            "core.t0.score_policy.predict_tau_oc_from_prefix_minutes",
            return_value=0.9,
        ):
            out = attach_portrait_dual_scores(
                day,
                {"y_tau": -0.8, "y_tau_oc": -0.8, "gap_pct": 1.0, "features_tau": {"gap_pct": 1.0}},
                minute_bars=mins,
                day_bar=_bar(d, 100, 102, 99, 101),
                prefix_bars=4,
            )
        self.assertIsNone(out.get("y_hl_portrait"))
        self.assertIsNone(out.get("y_hl"))
        self.assertAlmostEqual(float(out.get("y_tau_portrait_oc")), 0.9, places=4)
        self.assertEqual(int(out.get("portrait_prefix_bars") or 0), 4)
        sc = extract_scores(out)
        port = build_score_portrait([out])
        self.assertIsNone(sc.get("y_τc"))
        self.assertEqual(port.get("tau_hit", {}).get("hit"), 0)

    def test_leg2_no_preset_target_covers(self):
        """不预设第二腿目标价：反T 靠回补完成买回。"""
        d = "2024-02-03"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins_hl(bar=bar)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            scores=_scores_r(-0.5),
            rules=_rules_path(
                fill_mode="trigger",
                t0_round_ratio=1.0,
                t0_slots_max_rounds=1,
                must_cover_same_day_sell_then_buy=True,
                t0_pm_degrade_sell_then_buy="13:00",
            ),
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in out.get("trades") or [] if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1, out)
        frozen = (out.get("t0_slot_results") or [{}])[0].get("close_band") or {}
        self.assertIsNotNone(frozen.get("leg2_target"))

    def test_backtest_minute_by_date_sets_first_touch(self):
        bars = []
        for i in range(8):
            b = _bar(f"2024-01-{i+1:02d}", 100, 106, 96, 101)
            bars.append(b)
        # 仅最后一天给分钟：先高后低
        d = bars[-1]["date"]
        mins = {
            d: [
                {
                    "datetime": f"{d} 10:00:00",
                    "date": d,
                    "open": 100,
                    "high": 106,
                    "low": 101,
                    "close": 105,
                },
                {
                    "datetime": f"{d} 14:00:00",
                    "date": d,
                    "open": 105,
                    "high": 105,
                    "low": 96,
                    "close": 100,
                },
            ]
        }
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={
                "use_atr": False,
                "min_range_pct": 1.0,
                "direction": "sell_then_buy",
                "path_mode": "first_touch",
                "fill_mode": "trigger",
                "t0_slots_max_rounds": 0,
            },
            minute_by_date=mins,
            require_minute=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(int(report.get("minute_path_days") or 0), 1)
        self.assertNotIn("daily_compare", report)
        self.assertGreaterEqual(int(report.get("missing_minute_days") or 0), 1)

    def test_backtest_day_carries_forward_trace(self):
        bars = [_bar(f"2024-01-{i+1:02d}", 100, 103, 94, 100) for i in range(8)]
        d = bars[-1]["date"]
        mins = {d: _mins_lh(bar=bars[-1])}
        with patch(
            "core.t0.score_policy.resolve_scores_for_code",
            return_value=_scores_r(0.5),
        ), patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=_scores_r(0.5),
        ):
            report = backtest_t0_on_bars(
                bars,
                initial_shares=1000,
                initial_cost=100,
                initial_cash=50000,
                rules=_rules(
                    direction="buy_then_sell",
                    fill_mode="trigger",
                    min_range_pct=1.0,
                    y_tau_enter=0.0,
                    y_path_enter=0.0,
                    y_use_path=False,
                ),
                minute_by_date=mins,
                stock_code="600519",
            )
        traded = [
            x
            for x in (report.get("days") or [])
            if int(x.get("bought_qty") or 0) > 0 or int(x.get("sold_qty") or 0) > 0
        ]
        self.assertEqual(len(traded), 1, report)
        ft = traded[0].get("forward_trace") or []
        self.assertGreaterEqual(len(ft), 4, traded[0])
        self.assertTrue(
            any(r.get("leg1_fill") or r.get("buy_fill") for r in ft),
            ft,
        )
        self.assertTrue(
            any(r.get("leg2_fill") or r.get("sell_fill") for r in ft),
            ft,
        )

    def test_simulate_day_matches_intraday_setup_trades(self):
        """回测 simulate 与 Worker _intraday_setup 全日分钟应同腿同价。"""
        from core.t0.intraday import _intraday_setup

        d = "2024-01-07"
        bar = _bar(d, 100, 103, 94, 100)
        mins = _mins_lh(bar=bar)
        rules = _rules(
            direction="buy_then_sell",
            fill_mode="trigger",
            min_range_pct=1.0,
        )
        cfg = load_t0_rules(rules)
        day_out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=rules,
            stock_code="",
            scores=_scores_r(0.5),
        )
        setup = _intraday_setup(
            code="",
            holding={"shares": 1000, "cost": 100, "stock_name": "测"},
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=1000,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=_scores_r(0.5),
            stance_code="hold",
            coupling_mode="independent",
            force_session_close=True,
        )
        self.assertTrue(day_out.get("success"), day_out)
        self.assertTrue(setup.get("ready"), setup)

        def _norm_trades(trades):
            return [
                (
                    str(t.get("side") or ""),
                    int(t.get("shares") or 0),
                    round(float(t.get("price") or 0), 4),
                    str(t.get("at") or "")[11:16],
                )
                for t in (trades or [])
            ]

        self.assertEqual(
            _norm_trades(day_out.get("trades")),
            _norm_trades(setup.get("trades")),
            (day_out.get("trades"), setup.get("trades")),
        )


    def test_require_minute_rejects_daily_only(self):
        bars = [_bar(f"2024-01-{i+1:02d}", 100, 106, 96, 101) for i in range(5)]
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules={
                "use_atr": False,
                "min_range_pct": 1.0,
                "direction": "sell_then_buy",
                "path_mode": "first_touch",
                "t0_slots_max_rounds": 0,
            },
            minute_by_date=None,
            require_minute=True,
        )
        self.assertFalse(report.get("success"))
        self.assertTrue("分钟" in (report.get("error") or "") or "日线" in (report.get("error") or ""))

    def test_paper_tplus1_uses_session_not_stale_bar_date(self):
        """凌晨预演：日线仍停昨收时，T+1 应按会话日解冻昨日买入。"""
        paper = {
            "cash": 200000,
            "holdings": [
                {
                    "stock_code": "000938",
                    "stock_name": "紫光股份",
                    "shares": 500,
                    "cost": 20,
                    "lots": [
                        {
                            "shares": 500,
                            "bought_date": "2026-08-25",
                            "bought_at": "2026-08-25T10:00:00",
                        }
                    ],
                }
            ],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.9,
                    "direction": "buy_then_sell",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.5,
                    "must_cover_same_day": True,
                    "t0_slots_max_rounds": 0,
                    "y_tpd_max": 1.0,
                    "y_t30_strong": 1.0,
                    "y_t60_strong": 1.0,
                    "y_t90_strong": 1.0,
                }
            },
        }
        # 昨收 bar（会话已是次日）
        bar = _bar("2026-08-25", 20, 21, 19, 20.5)
        mins = _mins_lh(bar=bar)
        with patch("core.paper.tplus1.session_date", return_value="2026-08-26"):
            out = simulate_t0_on_holdings(
                paper,
                bars_by_code={"000938": bar},
                minute_bars_by_code={"000938": mins},
                dry_run=True,
            )
        row = (out.get("results") or [None])[0]
        self.assertIsNotNone(row)
        reason = str(row.get("reason") or "")
        self.assertNotIn("可卖旧仓 0", reason)
        self.assertNotIn("可卖0", reason)
        # 可卖已解冻；是否成交取决于路径，但不应因 T+1=0 跳过
        if row.get("skipped"):
            self.assertNotIn("T+1", reason)

    def test_paper_holdings_dry_run(self):
        paper = {
            "cash": 50000,
            "holdings": [
                {"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 100}
            ],
            "trades": [],
            "rules": {
                "t0": _rules(
                    t0_ratio=0.4,
                    t0_round_ratio=0.4,
                    t0_close_band_delta_pct=0.01,
                    t0_slots_max_rounds=1,
                    direction="sell_then_buy",
                    fill_mode="optimistic",
                    use_atr=False,
                    min_range_pct=1.0,
                )
            },
        }
        bars = {"600519": _bar("2026-01-09", 100, 105, 98, 101)}
        mins = {"600519": _mins_hl(bar=bars["600519"])}
        scores = {"600519": _scores_r(-0.5)}
        with patch(
            "core.t0.score_policy.resolve_scores_for_code",
            return_value=_scores_r(-0.5),
        ), patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=_scores_r(-0.5),
        ):
            out = simulate_t0_on_holdings(
                paper,
                bars_by_code=bars,
                minute_bars_by_code=mins,
                scores_by_code=scores,
                dry_run=True,
            )
            self.assertTrue(out["success"])
            self.assertTrue(out["dry_run"])
            self.assertGreater(len(out["trades"]), 0)
            self.assertEqual(len(paper["trades"]), 0)
            self.assertEqual(float(paper["holdings"][0]["shares"]), 1000)

            out2 = simulate_t0_on_holdings(
                paper,
                bars_by_code=bars,
                minute_bars_by_code=mins,
                scores_by_code=scores,
                dry_run=False,
            )
        self.assertFalse(out2["dry_run"])
        self.assertGreater(len(paper["trades"]), 0)
        t0_logs = [
            e
            for e in paper.get("operation_log") or []
            if (e.get("meta") or {}).get("origin") == "t0"
        ]
        self.assertGreater(len(t0_logs), 0)

    def test_skip_codes_blocks_holdings_replay(self):
        """盘中已落账腿：整单回放跳过该票，不重复成交。"""
        paper = {
            "cash": 100000,
            "holdings": [{"stock_code": "600519", "stock_name": "茅台", "shares": 1000, "cost": 100}],
            "trades": [],
            "rules": {
                "t0": {
                    "t0_ratio": 0.4,
                    "direction": "sell_then_buy",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 1.0,
                    "must_cover_same_day_sell_then_buy": False,
                    "t0_slots_max_rounds": 0,
                    "y_tpd_max": 1.0,
                    "y_t30_strong": 1.0,
                    "y_t60_strong": 1.0,
                    "y_t90_strong": 1.0,
                }
            },
        }
        bars = {"600519": _bar("2026-01-09", 100, 105, 98, 101)}
        mins = {"600519": _mins_hl(bar=bars["600519"])}
        out = simulate_t0_on_holdings(
            paper,
            bars_by_code=bars,
            minute_bars_by_code=mins,
            dry_run=True,
            skip_codes={"600519"},
        )
        self.assertTrue(out["success"])
        self.assertEqual(len(out.get("trades") or []), 0)
        row = (out.get("results") or [None])[0]
        self.assertTrue(row.get("skipped"))
        self.assertEqual(row.get("skip_category"), "intraday_legs_open")

    def test_overlay_booked_t0_preview_shows_auto_fills(self):
        """dry_run 不回放已落账票，但预演表挂上盘中成交快照。"""
        from core.t0.intraday import overlay_booked_t0_on_preview

        result = {
            "success": True,
            "trades": [],
            "pnl_total": 0.0,
            "exposure_pnl_total": 0.0,
            "skip_count": 2,
            "results": [
                {
                    "stock_code": "600875",
                    "stock_name": "东方电气",
                    "skipped": True,
                    "skip_category": "intraday_legs_open",
                    "trades": [],
                },
                {
                    "stock_code": "600660",
                    "stock_name": "福耀玻璃",
                    "skipped": True,
                    "reason": "正T：可卖旧仓 0 股",
                    "trades": [],
                },
            ],
        }
        stocks_st = {
            "600875": {
                "stock_name": "东方电气",
                "legs_written": 2,
                "direction": "sell_then_buy",
                "day_snapshot": {
                    "date": "2026-09-16",
                    "direction": "sell_then_buy",
                    "pnl": -42.18,
                    "exposure_pnl": 0.0,
                    "open": 23.5,
                    "close": 23.8,
                    "scores": {"y_tau": 0.12, "y_oc": 0.12, "y_τ30": -0.4},
                    "t0_slot_results": [
                        {
                            "id": "r1",
                            "hm": "09:35",
                            "sold_qty": 100,
                            "covered_qty": 100,
                            "trades": 2,
                            "close_band": {"c_tau": 23.55},
                        }
                    ],
                    "close_band_scan": [{"hm": "09:35", "o": 23.52, "c": 23.48}],
                    "trades": [
                        {"side": "t0_sell", "shares": 100, "price": 23.66},
                        {"side": "t0_buy", "shares": 100, "price": 23.97},
                    ],
                },
            }
        }
        out = overlay_booked_t0_on_preview(
            result, stocks_st=stocks_st, session_date="2026-09-16"
        )
        self.assertEqual(out.get("booked_preview_count"), 1)
        self.assertEqual(out.get("skip_count"), 1)
        self.assertEqual(len(out.get("trades") or []), 2)
        booked = (out.get("results") or [])[0]
        self.assertTrue(booked.get("already_booked"))
        self.assertFalse(booked.get("skipped"))
        self.assertEqual(len(booked.get("trades") or []), 2)
        self.assertEqual(booked.get("open"), 23.5)
        self.assertEqual(booked.get("close"), 23.8)
        self.assertIsNone(booked.get("scores"))
        self.assertFalse(booked.get("close_band_scan"))
        self.assertEqual(len(booked.get("t0_slot_results") or []), 1)
        still_skip = (out.get("results") or [])[1]
        self.assertTrue(still_skip.get("skipped"))

    def test_overlay_prefers_fresh_preview_scan_over_booked_snapshot(self):
        """已落账预演：本轮重扫 ŷ 盖掉早盘快照（生益科技 09:45 y_oc 漂）。"""
        from core.t0.intraday import overlay_booked_t0_on_preview

        result = {
            "success": True,
            "trades": [],
            "pnl_total": 0.0,
            "exposure_pnl_total": 0.0,
            "skip_count": 1,
            "results": [
                {
                    "stock_code": "600183",
                    "stock_name": "生益科技",
                    "skipped": True,
                    "skip_category": "intraday_legs_open",
                    "trades": [],
                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.62}],
                    "scores": {"y_oc": -0.62, "tau_realized": -4.56},
                    "close": 146.03,
                    "tau_realized": -4.56,
                }
            ],
        }
        stocks_st = {
            "600183": {
                "stock_name": "生益科技",
                "legs_written": 2,
                "direction": "sell_then_buy",
                "day_snapshot": {
                    "date": "2026-09-16",
                    "direction": "sell_then_buy",
                    "pnl": -12.5,
                    "close": 152.6,
                    "tau_realized": -1.30,
                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                    "trades": [
                        {"side": "t0_sell", "shares": 200, "price": 150.06},
                        {"side": "t0_buy", "shares": 200, "price": 152.07},
                    ],
                },
            }
        }
        out = overlay_booked_t0_on_preview(
            result, stocks_st=stocks_st, session_date="2026-09-16"
        )
        booked = (out.get("results") or [])[0]
        self.assertTrue(booked.get("already_booked"))
        self.assertEqual(len(booked.get("trades") or []), 2)
        self.assertEqual(booked.get("close_band_scan")[0]["y_oc"], -0.62)
        self.assertEqual((booked.get("scores") or {}).get("y_oc"), -0.62)
        self.assertEqual((booked.get("scores") or {}).get("tau_realized"), -4.56)
        self.assertEqual(booked.get("close"), 146.03)
        self.assertAlmostEqual(float(booked.get("tau_realized") or 0), -4.56)
        self.assertAlmostEqual(float(booked.get("pnl") or 0), -12.5)

    def test_refresh_stamps_tau_realized_from_daily_close_not_last_minute(self):
        """重扫 OHLC 可用分钟末价；y_oc 真实值必须用日 K 收（对照回测）。"""
        from core.t0.slots import refresh_close_band_scan

        daily = {
            "date": "2026-09-17",
            "open": 153.0,
            "close": 146.03,
            "prev_close": 154.5,
        }
        mins = [
            {
                "datetime": "2026-09-17 09:35:00",
                "open": 153.0,
                "high": 156.0,
                "low": 152.8,
                "close": 154.49,
            },
            {
                "datetime": "2026-09-17 11:00:00",
                "open": 152.24,
                "high": 152.3,
                "low": 151.59,
                "close": 151.01,
            },
        ]
        day = {
            "date": "2026-09-17",
            "open": 153.0,
            "close": 151.01,
            "tau_realized": -1.30,
            "t0_slot_results": [],
        }
        with patch("core.t0.slots._attach_close_band_scan", side_effect=lambda d, **_k: d):
            out = refresh_close_band_scan(
                day, minute_bars=mins, bar=daily, daily_bar=daily
            )
        self.assertAlmostEqual(float(out["close"]), 146.03)
        self.assertAlmostEqual(float(out["tau_realized"]), (146.03 / 153.0 - 1) * 100, places=3)
        ps = out.get("price_space") or {}
        self.assertAlmostEqual(float(ps.get("minute_close")), 151.01)
        self.assertAlmostEqual(float(ps.get("daily_close")), 146.03)

    def test_overlay_keeps_simulated_scan_when_row_not_skipped(self):
        """预演不跳过已落账票时：成交用快照，ŷ 用本轮 simulate。"""
        from core.t0.intraday import overlay_booked_t0_on_preview

        result = {
            "success": True,
            "trades": [{"side": "t0_sell", "price": 1}],
            "pnl_total": 9.0,
            "exposure_pnl_total": 0.0,
            "skip_count": 0,
            "results": [
                {
                    "stock_code": "600183",
                    "stock_name": "生益科技",
                    "skipped": False,
                    "trades": [{"side": "t0_sell", "price": 1}],
                    "pnl": 9.0,
                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.62}],
                    "close": 154.48,
                }
            ],
        }
        stocks_st = {
            "600183": {
                "legs_written": 2,
                "day_snapshot": {
                    "date": "2026-09-16",
                    "pnl": -12.5,
                    "close": 152.6,
                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                    "trades": [
                        {"side": "t0_sell", "shares": 200, "price": 150.06},
                        {"side": "t0_buy", "shares": 200, "price": 152.07},
                    ],
                },
            }
        }
        out = overlay_booked_t0_on_preview(
            result, stocks_st=stocks_st, session_date="2026-09-16"
        )
        booked = (out.get("results") or [])[0]
        self.assertTrue(booked.get("already_booked"))
        self.assertEqual(len(booked.get("trades") or []), 2)
        self.assertEqual(booked.get("close_band_scan")[0]["y_oc"], -0.62)
        self.assertEqual(booked.get("close"), 154.48)
        self.assertAlmostEqual(float(booked.get("pnl") or 0), -12.5)
        self.assertEqual(len(out.get("trades") or []), 2)

    def test_overlay_booked_sources_fills_from_results(self):
        """Worker 只写 results 时，预演仍能贴已落账腿。"""
        from core.t0.intraday import overlay_booked_sources, overlay_booked_t0_on_preview

        ist = {
            "session_date": "2026-09-16",
            "stocks": {},
            "results": [
                {
                    "stock_code": "600183",
                    "stock_name": "生益科技",
                    "direction": "sell_then_buy",
                    "date": "2026-09-16",
                    "pnl": -424.12,
                    "trades": [
                        {"side": "t0_sell", "shares": 200, "price": 150.10},
                        {"side": "t0_buy", "shares": 200, "price": 152.07},
                    ],
                }
            ],
        }
        src = overlay_booked_sources(ist)
        self.assertIn("600183", src)
        self.assertEqual(
            len((src["600183"].get("day_snapshot") or {}).get("trades") or []), 2
        )
        result = {
            "success": True,
            "trades": [],
            "pnl_total": 0.0,
            "exposure_pnl_total": 0.0,
            "skip_count": 0,
            "results": [
                {
                    "stock_code": "600183",
                    "stock_name": "生益科技",
                    "skipped": False,
                    "trades": [],
                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.70}],
                    "close": 154.48,
                }
            ],
        }
        out = overlay_booked_t0_on_preview(
            result, stocks_st=src, session_date="2026-09-16"
        )
        booked = (out.get("results") or [])[0]
        self.assertTrue(booked.get("already_booked"))
        self.assertEqual(len(booked.get("trades") or []), 2)
        self.assertEqual(booked.get("close_band_scan")[0]["y_oc"], -0.70)

    def test_routing_and_skill_task(self):
        self.assertEqual(infer_quant_task("茅台底仓做T回测一下"), "t0_backtest")
        self.assertIn("t0_backtest", AVAILABLE_TASKS)

    def test_engine_offline_backtest(self):
        bars = [_bar(f"d{i}", 100, 108, 95, 101) for i in range(30)]
        with patch(
            "core.data.facade.bars_and_source", return_value=(bars, "mock")
        ), patch(
            "adapters.market.quote_api.StockAPI.query",
            return_value={"success": True, "stock_code": "600519", "stock_name": "茅台"},
        ):
            out = QuantEngine().run(
                {
                    "task": "t0_backtest",
                    "stock_code": "600519",
                    "lookback": 40,
                    "initial_shares": 1000,
                    "direction": "sell_then_buy",
                    "path_mode": "first_touch",
                    "use_atr": False,
                }
            )
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out.get("task"), "t0_backtest")


class TestDualYDirection(unittest.TestCase):
    def test_load_dual_y_alias(self):
        cfg = load_t0_rules({"direction": "yhat"})
        self.assertEqual(cfg["direction"], "dual_y")
        self.assertEqual(cfg["y_score_source"], "compute")

    def test_side_exec_params_apply_by_direction(self):
        from core.t0.config import apply_side_exec_params, load_t0_rules

        cfg = load_t0_rules(
            {
                "fill_mode_sell_then_buy": "trigger",
                "fill_mode_buy_then_sell": "mid",
            }
        )
        stb_cfg = apply_side_exec_params(cfg, "sell_then_buy")
        self.assertEqual(stb_cfg["fill_mode"], "trigger")
        bts_cfg = apply_side_exec_params(cfg, "buy_then_sell")
        self.assertEqual(bts_cfg["fill_mode"], "mid")
        self.assertEqual(stb_cfg["t0_pm_degrade"], "13:00")
        self.assertEqual(bts_cfg["t0_pm_degrade"], "13:00")

    def test_scores_from_item_y_tau_is_oc_not_mapped(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "predicted_score_tau": 0.19,
                "y_tau_oc": -0.78,
                "predicted_score_blend": -1.0,
            }
        )
        self.assertAlmostEqual(float(sc["y_tau"]), -0.78)
        self.assertAlmostEqual(float(sc["y_tau_oc"]), -0.78)
        self.assertAlmostEqual(float(sc["y_tau_mapped"]), 0.19)

    def test_open_decision_quote_uses_open_not_close(self):
        from core.t0.score_policy import open_decision_quote

        day = _bar("2026-01-10", 98, 110, 90, 105)
        prev = _bar("2026-01-09", 100, 101, 99, 100)
        q = open_decision_quote(day, prev, code="600000")
        self.assertEqual(q["open"], 98)
        self.assertEqual(q["price_raw"], 98)
        self.assertEqual(q["prev_close"], 100)
        self.assertAlmostEqual(q["change_raw"], -2.0, places=4)

    def test_enrich_quote_path_price_uses_day_close(self):
        from core.t0.score_policy import _enrich_quote_path_price, open_decision_quote

        day = _bar("2026-01-10", 98, 110, 90, 105)
        prev = _bar("2026-01-09", 100, 101, 99, 100)
        q = open_decision_quote(day, prev, code="600000")
        enriched = _enrich_quote_path_price(q, day, code="600000")
        self.assertEqual(enriched["open"], 98)
        self.assertEqual(enriched["prev_close"], 100)
        self.assertEqual(enriched["price_raw"], 105)
        self.assertAlmostEqual(enriched["change_raw"], -2.0, places=4)

    def test_scores_from_item_keeps_yon_path_separate(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "predicted_score_on": 0.2,
                "y_on": 0.2,
                "y_on_path": 3.5,
                "predicted_score_tau": 0.8,
            }
        )
        self.assertAlmostEqual(sc["y_co"], 0.2)
        self.assertAlmostEqual(sc["y_on_path"], 3.5)
        packed = __import__(
            "core.t0.score_policy", fromlist=["pack_day_scores"]
        ).pack_day_scores(sc)
        self.assertAlmostEqual(packed["y_co"], 0.2)
        self.assertAlmostEqual(packed["y_on_path"], 3.5)

    def test_eod_factor_bar_limit_matches_score_stock(self):
        """EOD 窗与实盘取数同长，满足 Alpha158（≥61）。周线按末根切桶。"""
        from core.signal.factors.alpha158 import ALPHA158_MIN_BARS, ALPHA158_PANEL_WINDOW
        from core.signal.score_stock import _alpha158_fetch_limit
        from core.t0.score_policy import _EOD_FACTOR_BAR_LIMIT

        self.assertGreaterEqual(int(ALPHA158_PANEL_WINDOW), int(ALPHA158_MIN_BARS))
        self.assertEqual(int(_EOD_FACTOR_BAR_LIMIT), int(ALPHA158_PANEL_WINDOW))
        self.assertEqual(int(_EOD_FACTOR_BAR_LIMIT), _alpha158_fetch_limit())

    def test_paper_t0_daily_bar_limit_covers_eod_hist(self):
        """纸面预演日线须含当日 + EOD 窗，否则比回测少 1 根。"""
        import inspect

        from core.t0.score_policy import (
            T0_PAPER_DAILY_BAR_LIMIT,
            _EOD_FACTOR_BAR_LIMIT,
        )
        from services import paper_trades as pt

        self.assertGreaterEqual(int(T0_PAPER_DAILY_BAR_LIMIT), int(_EOD_FACTOR_BAR_LIMIT) + 1)
        src = inspect.getsource(pt.PaperTradesMixin._simulate_t0_impl)
        self.assertIn("T0_PAPER_DAILY_BAR_LIMIT", src)
        self.assertNotIn("limit=40", src)

    def test_simulate_t0_on_holdings_passes_tau_pool_day(self):
        """预演须把观察池 ∪ 持仓缺口截面传进单日模拟，否则 rescore 退回单票池、ŷ 相对回测会漂。"""
        from unittest.mock import patch

        from core.t0.rules import simulate_t0_on_holdings

        paper = {
            "cash": 200000,
            "holdings": [
                {"stock_code": "600875", "stock_name": "东方电气", "shares": 300, "cost": 25.0}
            ],
        }
        hist = [
            {"date": f"2026-08-{i:02d}", "open": 24.0, "high": 24.2, "low": 23.8, "close": 24.1}
            for i in range(1, 21)
        ]
        day = {
            "date": "2026-09-11",
            "open": 25.3,
            "high": 25.5,
            "low": 25.0,
            "close": 25.1,
            "prev_close": 25.6,
        }
        mins = [
            {
                "date": "2026-09-11",
                "datetime": f"2026-09-11 09:{35 + i * 5:02d}:00",
                "open": 25.3,
                "high": 25.4,
                "low": 25.2,
                "close": 25.25,
            }
            for i in range(3)
        ]
        captured = {}

        def _fake_sim(**kwargs):
            captured["tau_pool_day"] = kwargs.get("tau_pool_day")
            return {
                "success": True,
                "skipped": True,
                "reason": "test skip",
                "trades": [],
                "pnl": 0.0,
                "date": "2026-09-11",
            }

        with patch("core.t0.rules.simulate_t0_day", side_effect=_fake_sim):
            with patch("core.paper.tplus1.session_date", return_value="2026-09-11"):
                out = simulate_t0_on_holdings(
                    paper,
                    bars_by_code={"600875": day},
                    hist_bars_by_code={"600875": hist},
                    minute_bars_by_code={"600875": mins},
                    dry_run=True,
                )
        self.assertTrue(out.get("success"), out)
        pool = captured.get("tau_pool_day")
        self.assertIsInstance(pool, dict)
        self.assertIn("pool_gaps", pool)
        self.assertEqual(out.get("score_model_role"), "live")

    def test_simulate_t0_on_holdings_seeds_peer_minutes(self):
        """预演须把持仓 5m 写入同伴仓，否则 sector_ret 读早盘残缺仓、ŷ 相对回测漂。"""
        import inspect

        from core.t0.rules import simulate_t0_on_holdings
        from services.paper_trades import PaperTradesMixin

        src = inspect.getsource(simulate_t0_on_holdings)
        self.assertIn("hydrate_cs_peer_minutes", src)
        self.assertIn("build_tau_pool_watching_holdings", src)
        self.assertNotIn("build_tau_pool_from_holding_bars", src)
        impl = inspect.getsource(PaperTradesMixin._simulate_t0_impl)
        self.assertIn("skip_codes=None if dry_run", impl)
        self.assertIn("_sync_preview_scans_to_ledgers", impl)
        self.assertNotIn("_attach_fresh_scan_to_booked_preview", impl)
        tick = inspect.getsource(PaperTradesMixin.run_t0_intraday_tick)
        self.assertIn("build_tau_pool_watching_holdings", tick)
        self.assertIn("hydrate_cs_peer_minutes", tick)

    def test_tip_fields_include_features_co(self):
        from core.t0.score_policy import tip_fields_from_item

        tip = tip_fields_from_item(
            {
                "features_co": {
                    "ret_oc": -0.5,
                    "ret_cc": -1.2,
                    "gap_pct": -0.8,
                    "noise": 1,
                },
                "formula_terms_co": {
                    "intercept": 0.0,
                    "total": -0.7,
                    "terms": [{"key": "ret_oc", "contrib": -0.3}],
                },
            }
        )
        self.assertEqual(tip.get("features_co", {}).get("ret_oc"), -0.5)
        self.assertNotIn("noise", tip.get("features_co") or {})
        self.assertIsNotNone(tip.get("formula_terms_co"))

    def test_tip_fields_include_formula_terms_r(self):
        from core.t0.score_policy import tip_fields_from_item

        tip = tip_fields_from_item(
            {
                "features_tau": {"gap_pct": 1.2, "ret_open_to_tau": 0.3},
                "formula_terms_r": {
                    "intercept": 0.05,
                    "total": -0.4,
                    "head": "r",
                    "terms": [{"key": "gap_pct", "label": "缺口", "contrib": -0.45}],
                },
                "y_spec_τc": {"formula": "close[T]/price[τ]-1", "unit": "pct"},
            }
        )
        self.assertEqual(tip.get("formula_terms_r", {}).get("head"), "r")
        self.assertAlmostEqual(tip.get("formula_terms_r", {}).get("total"), -0.4, places=6)
        self.assertEqual(tip.get("y_spec_τc", {}).get("formula"), "close[T]/price[τ]-1")

    def test_tip_fields_include_formula_terms_t30(self):
        from core.t0.score_policy import tip_fields_from_item

        tip = tip_fields_from_item(
            {
                "features_tau": {"ret_last_30m": 0.4, "t30_lag1": -0.2},
                "formula_terms_t30": {
                    "intercept": 0.01,
                    "total": 0.3,
                    "head": "t30",
                    "terms": [{"key": "ret_last_30m", "label": "近30m", "contrib": 0.2}],
                },
                "y_spec_τ30": {"formula": "mean(price[τ+25m],price[τ+30m],price[τ+35m])/price[τ]-1", "unit": "pct"},
            }
        )
        self.assertEqual(tip.get("formula_terms_t30", {}).get("head"), "t30")
        self.assertAlmostEqual(tip.get("formula_terms_t30", {}).get("total"), 0.3, places=6)
        self.assertEqual(tip.get("y_spec_τ30", {}).get("formula"), "mean(price[τ+25m],price[τ+30m],price[τ+35m])/price[τ]-1")

    def test_compute_scores_from_bars_pit(self):
        from core.t0.score_policy import (
            clear_score_model_cache,
            compute_scores_from_bars,
            scores_have_any,
        )

        clear_score_model_cache()
        hist = []
        px = 100.0
        for i in range(30):
            d = f"2025-11-{(i % 28) + 1:02d}"
            o = px
            c = px * (1.0 + (0.01 if i % 3 else -0.005))
            hist.append(_bar(d, o, max(o, c) * 1.01, min(o, c) * 0.99, c))
            px = c
        day = _bar("2025-12-01", px * 1.02, px * 1.05, px * 0.98, px * 1.01)
        sc = compute_scores_from_bars("600000", hist, day_bar=day, fuse_intraday=True)
        # 无模型时可能空；有簇/全局模型时应有 ŷ
        if scores_have_any(sc):
            self.assertIsNotNone(sc.get("y_eod") or sc.get("y_trade") or sc.get("y_tau"))
            self.assertEqual(sc.get("_score_source"), "compute")
            feats_co = sc.get("features_co") or {}
            path_feats = sc.get("features_co_path") or {}
            # 开盘决策：ret_oc 取 T-1 已实现（昨开→昨收），非当日 close；决策与路径口径一致
            if feats_co.get("ret_oc") is not None:
                self.assertNotAlmostEqual(float(feats_co["ret_oc"]), 0.0, places=4)
            if path_feats.get("ret_oc") is not None:
                self.assertNotAlmostEqual(float(path_feats["ret_oc"]), 0.0, places=4)
            if sc.get("y_on_path") is not None and sc.get("y_on") is not None:
                # 旁路路径价不得覆盖决策 y_on（二者可同可异，但字段须并存）
                self.assertIn("y_on_path", sc)

    def test_compute_aligns_trade_with_holdings_blend(self):
        """即时算分后 align：y_trade 须与 predicted_score_blend 一致，双头不得塌成 y_eod。"""
        from core.t0.score_policy import scores_from_item

        item = {
            "predicted_score": 2.5,
            "predicted_score_eod": 2.5,
            "predicted_score_blend": 2.5,
            "predicted_score_tau": -0.04,
            "gap_pct": 0.26,
            "dual_score_window": "intraday",
            "dual_score_weights": {
                "w_eod": 0.5,
                "w_tau": 0.0,
                "tau_in_trade": False,
                "window": "eod_next",
            },
        }
        from core.signal.dual_score import align_trade_score_fields

        align_trade_score_fields(
            item,
            write_score=False,
            refresh_window=False,
            config={
                "dual_score": {
                    "w_eod": 0.5,
                    "w_tau": 0.5,
                    "w_mode": "fixed",
                }
            },
        )
        sc = scores_from_item(item)
        self.assertAlmostEqual(sc["y_eod"], 2.5, places=4)
        self.assertNotAlmostEqual(sc["y_trade"], 2.5, places=3)
        self.assertAlmostEqual(float((sc.get("dual_score_weights") or {}).get("w_tau") or 0), 0.5, places=3)

    def test_scores_from_item_prefers_blend_for_trade(self):
        """双头：predicted_score=ŷ_oo，ŷ_trade 须取 blend，不能塌成与 y_eod 相同。"""
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "predicted_score": 3.29,
                "predicted_score_eod": 3.29,
                "predicted_score_blend": 1.31,
                "predicted_score_tau": 0.16,
                "predicted_score_on": 0.75,
                "predicted_score_nowcast": 2.75,
            }
        )
        self.assertAlmostEqual(sc["y_eod"], 3.29, places=4)
        self.assertAlmostEqual(sc["y_trade"], 1.31, places=4)
        self.assertAlmostEqual(sc["y_tau"], 0.16, places=4)
        self.assertNotAlmostEqual(sc["y_eod"], sc["y_trade"], places=4)

    def test_attach_day_scores_fills_skip_and_features(self):
        """跳过日也要带 scores + direction_features，预演明细才能画 y_*。"""
        from core.t0.score_policy import attach_day_scores

        snap = {
            "y_eod": 1.2,
            "y_tau": -0.3,
            "y_trade": 0.8,
            "y_on": 0.5,
            "y_nowcast": 1.0,
        }
        out = attach_day_scores({"skipped": True, "reason": "横盘"}, snap)
        self.assertTrue(out["skipped"])
        self.assertAlmostEqual(out["scores"]["y_tau"], -0.3, places=4)
        self.assertAlmostEqual(out["direction_features"]["y_eod"], 1.2, places=4)
        # 已有 features 优先，缺项用 snap 补
        out2 = attach_day_scores(
            {"direction_features": {"y_tau": 0.1}},
            {"y_eod": 2.0, "y_tau": 9.0, "y_trade": 0.5},
        )
        self.assertAlmostEqual(out2["direction_features"]["y_tau"], 0.1, places=4)
        self.assertAlmostEqual(out2["direction_features"]["y_eod"], 2.0, places=4)

    def test_attach_eod_tau_realized_labels(self):
        from core.t0.score_policy import attach_day_scores, attach_eod_tau_realized

        day = attach_eod_tau_realized(
            {"scores": {"y_eod": 1.0, "y_tau": 0.5}},
            open_px=10.0,
            close_px=10.2,
            prev_close=10.0,
        )
        self.assertAlmostEqual(day["tau_realized"], 2.0, places=4)
        self.assertAlmostEqual(day["eod_realized"], 2.0, places=4)
        self.assertAlmostEqual(day["scores"]["tau_realized"], 2.0, places=4)
        # attach_day_scores 在有开收时自动写真实值
        out = attach_day_scores(
            {"open": 10.0, "close": 9.9, "prev_close": 10.0},
            {"y_eod": 0.1, "y_tau": -0.2, "y_trade": 0.0},
        )
        self.assertAlmostEqual(out["tau_realized"], -1.0, places=4)
        self.assertAlmostEqual(out["eod_realized"], -1.0, places=4)

    def test_scores_from_item_keeps_formula_terms_for_tip(self):
        """成交明细 tip 需要因子组成；scores 须保留 slim 分项。"""
        from core.t0.score_policy import pack_day_scores, scores_from_item

        sc = scores_from_item(
            {
                "predicted_score": 1.0,
                "predicted_score_eod": 1.0,
                "predicted_score_blend": 0.8,
                "predicted_score_tau": 0.2,
                "score_formula_terms": {
                    "intercept": 0.01,
                    "total": 1.0,
                    "terms": [
                        {
                            "key": "mom3",
                            "label": "动量3",
                            "beta": 0.1,
                            "z": 2.0,
                            "contrib": 0.2,
                        }
                    ],
                },
                "factor_coefficients": {"mom3": 0.1},
                "formula_terms_tau": {
                    "intercept": 0.0,
                    "total": 0.2,
                    "terms": [
                        {
                            "key": "gap_pct",
                            "label": "缺口",
                            "beta": 0.1,
                            "z": 2.0,
                            "contrib": 0.2,
                        }
                    ],
                },
                "features_tau": {"gap_pct": 1.5},
                "gap_pct": 1.5,
            }
        )
        self.assertTrue(sc.get("score_formula_terms", {}).get("terms"))
        self.assertTrue(sc.get("formula_terms_tau", {}).get("terms"))
        packed = pack_day_scores(sc)
        self.assertIsNotNone(packed)
        self.assertTrue(packed.get("score_formula_terms", {}).get("terms"))
        self.assertAlmostEqual(float(packed.get("gap_pct")), 1.5, places=4)

    def test_slim_formula_terms_pins_amihud(self):
        """非流动性 β 往往偏小，slim 时在 limit 内保留（不追加超限）。"""
        from core.t0.score_policy import _TIP_PIN_FACTORS, _slim_formula_terms

        terms = [
            {
                "key": f"f{i}",
                "label": f"F{i}",
                "beta": 0.1,
                "z": 1.0,
                "contrib": 1.0 - i * 0.01,
            }
            for i in range(20)
        ]
        terms.append(
            {
                "key": "amihud",
                "label": "非流动性",
                "beta": 0.02,
                "z": 0.1,
                "contrib": 0.002,
            }
        )
        slim = _slim_formula_terms(
            {"intercept": 0.0, "total": 1.0, "terms": terms},
            limit=10,
            pin_keys=_TIP_PIN_FACTORS,
        )
        keys = [t["key"] for t in (slim or {}).get("terms") or []]
        self.assertIn("amihud", keys)
        self.assertLessEqual(len(keys), 10)

    def test_slim_formula_terms_drops_imputed_zeros(self):
        from core.t0.score_policy import _slim_formula_terms

        slim = _slim_formula_terms(
            {
                "intercept": 0.2,
                "total": 1.0,
                "terms": [
                    {
                        "key": "up_extent",
                        "label": "上探",
                        "beta": 1.0,
                        "z": 1.5,
                        "contrib": 1.5,
                    },
                    {
                        "key": "sector_ret_to_tau",
                        "label": "板块中位",
                        "beta": -0.2,
                        "z": 0.0,
                        "contrib": 0.0,
                        "note": "缺特征·z≈0",
                    },
                ],
            },
            limit=12,
        )
        keys = [t["key"] for t in (slim or {}).get("terms") or []]
        self.assertEqual(keys, ["up_extent"])
        self.assertEqual(int(slim.get("missing_n") or 0), 1)

    def test_compute_scores_passes_index_bars_for_rs(self):
        """缺指数时相对强弱会退回开盘涨跌；须传入 index_bars 与数据中心同源。"""
        from unittest.mock import patch

        from core.t0.score_policy import clear_score_model_cache, compute_scores_from_bars

        clear_score_model_cache()
        hist = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 10 + i * 0.1, 11, 9, 10 + i * 0.1)
            for i in range(24)
        ]
        day = _bar("2025-11-01", 12.0, 12.5, 11.5, 12.2)
        idx = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 3000, 3010, 2990, 3000 + i)
            for i in range(24)
        ]
        captured = {}

        def _fake_window(code, window, **kwargs):
            captured["index_bars"] = kwargs.get("index_bars")
            captured["fundamentals"] = kwargs.get("fundamentals")
            return {
                "stock_code": code,
                "sub_scores": {"relative_strength": 85.0, "momentum": 50.0},
                "predicted_score": 0.5,
                "success": True,
            }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item", side_effect=_fake_window
        ), patch(
            "core.t0.score_policy._scoring_models",
            return_value=(None, {}, None),
        ), patch(
            "core.signal.dual_score.attach_dual_score_pit", side_effect=lambda it, **kw: it
        ), patch(
            "core.signal.return_score.apply_predicted_scores_by_model",
            side_effect=lambda items, *a, **k: list(items),
        ):
            compute_scores_from_bars(
                "601600",
                hist,
                day_bar=day,
                index_bars=idx,
                fundamentals={"market_cap": 1e11},
            )
        self.assertTrue(captured.get("index_bars"))
        self.assertEqual(len(captured["index_bars"]), 24)
        self.assertEqual(captured.get("fundamentals", {}).get("market_cap"), 1e11)

    def test_compute_scores_reuses_eod_window_across_minute_prefix(self):
        """同一交易日多根 5m 前缀只建一次 EOD 因子窗。"""
        from unittest.mock import patch

        from core.t0.score_policy import clear_score_model_cache, compute_scores_from_bars

        clear_score_model_cache()
        hist = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 10 + i * 0.1, 11, 9, 10 + i * 0.1)
            for i in range(24)
        ]
        day = _bar("2025-11-01", 12.0, 12.5, 11.5, 12.2)
        n_window = {"n": 0}

        def _fake_window(code, window, **kwargs):
            n_window["n"] += 1
            return {
                "stock_code": code,
                "sub_scores": {"relative_strength": 85.0, "momentum": 50.0},
                "predicted_score": 0.5,
                "success": True,
            }

        prefix_a = [{"datetime": "2025-11-01 09:35:00", "open": 12, "high": 12.1, "low": 11.9, "close": 12.0}]
        prefix_b = prefix_a + [
            {"datetime": "2025-11-01 09:40:00", "open": 12.0, "high": 12.2, "low": 11.9, "close": 12.1}
        ]
        with patch(
            "core.signal.cross_section_batch.score_window_as_item", side_effect=_fake_window
        ), patch(
            "core.t0.score_policy._scoring_models",
            return_value=(None, {}, None),
        ), patch(
            "core.signal.dual_score.attach_dual_score_pit", side_effect=lambda it, **kw: it
        ), patch(
            "core.signal.return_score.apply_predicted_scores_by_model",
            side_effect=lambda items, *a, **k: list(items),
        ):
            compute_scores_from_bars(
                "601600",
                hist,
                day_bar=day,
                fundamentals={"market_cap": 1e11},
                minute_bars=prefix_a,
                use_minute_tau=True,
                minute_tau_hm="09:35",
            )
            compute_scores_from_bars(
                "601600",
                hist,
                day_bar=day,
                fundamentals={"market_cap": 1e11},
                minute_bars=prefix_b,
                use_minute_tau=True,
                minute_tau_hm="09:40",
            )
        self.assertEqual(n_window["n"], 1)

    def test_compute_scores_passes_required_factor_keys(self):
        """G69 等组 β 含 amihud；T0 须传 required_factor_keys，否则 regime 会滤掉非流动性。"""
        from unittest.mock import MagicMock, patch

        from core.t0.score_policy import clear_score_model_cache, compute_scores_from_bars

        clear_score_model_cache()
        hist = [
            _bar(f"2025-10-{(i % 28) + 1:02d}", 10 + i * 0.1, 11, 9, 10 + i * 0.1)
            for i in range(24)
        ]
        day = _bar("2025-11-01", 12.0, 12.5, 11.5, 12.2)
        captured = {}

        def _fake_window(code, window, **kwargs):
            captured["required_factor_keys"] = kwargs.get("required_factor_keys")
            return {
                "stock_code": code,
                "sub_scores": {"amihud": 32.0, "relative_strength": 85.0},
                "predicted_score": 0.5,
                "success": True,
            }

        mock_model = MagicMock()
        mock_model.coefficients = {"amihud": -0.4, "relative_strength": -0.7, "momentum": 0.1}
        mock_model.explain_prediction.return_value = {
            "intercept": 0.0,
            "total": 0.5,
            "terms": [],
        }

        with patch(
            "core.signal.cross_section_batch.score_window_as_item", side_effect=_fake_window
        ), patch(
            "core.t0.score_policy._scoring_models",
            return_value=(None, {"601600": mock_model}, mock_model),
        ), patch(
            "core.signal.dual_score.attach_dual_score_pit", side_effect=lambda it, **kw: it
        ), patch(
            "core.signal.return_score.apply_predicted_scores_by_model",
            side_effect=lambda items, *a, **k: list(items),
        ), patch(
            "core.t0.score_policy._t0_index_bars_for_score", return_value=[]
        ), patch(
            "core.t0.score_policy._t0_local_fundamentals", return_value=None
        ):
            compute_scores_from_bars("601600", hist, day_bar=day)
        keys = set(captured.get("required_factor_keys") or [])
        self.assertIn("amihud", keys)
        self.assertIn("relative_strength", keys)

    def test_build_tau_pool_by_date_has_ref_and_breadth(self):
        """τ 截面：按日 open 缺口聚合；广度只计正缺口（与训练/刷簿同构）。"""
        from core.t0.score_policy import build_tau_pool_by_date

        bars_a = [
            _bar("2025-11-01", 10, 11, 9, 10.0),
            _bar("2025-11-02", 10.5, 11, 10, 10.4),  # gap +5% → 计入广度
        ]
        bars_b = [
            _bar("2025-11-01", 20, 21, 19, 20.0),
            _bar("2025-11-02", 19.0, 20, 18.5, 19.2),  # gap -5% → 不计（非 |gap|）
        ]
        bars_c = [
            _bar("2025-11-01", 30, 31, 29, 30.0),
            _bar("2025-11-02", 30.2, 31, 30, 30.1),  # gap +0.67% → 不计
        ]
        pool = build_tau_pool_by_date(
            {"600000": bars_a, "600001": bars_b, "600002": bars_c}
        )
        day = pool.get("2025-11-02") or {}
        self.assertEqual(len(day.get("pool_gaps") or []), 3)
        # 仅 +5% 一票命中 → 1/3
        self.assertAlmostEqual(float(day.get("sector_gap_breadth") or -1), 1.0 / 3.0, places=4)
        self.assertIn("600000", day.get("gaps_by_code") or {})
        ref = (day.get("ref_by_code") or {}).get("600000")
        self.assertIsNotNone(ref)

    def test_holding_tau_pool_seeds_gap_breadth_for_live_fallback(self):
        """盯盘缺截面时：持仓缺口写入日缓存仍可补同业广度（窄池对照）。"""
        from core.t0.score_policy import (
            _TAU_XS_DAY_CACHE,
            _tau_cross_section_kwargs,
            build_tau_pool_from_holding_bars,
            seed_tau_cross_section_day,
        )

        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)
        hist = {
            "600183": [_bar("2026-09-15", 148, 150, 147, 149.0)],
            "600875": [_bar("2026-09-15", 24, 25, 23, 24.5)],
        }
        days = {
            "600183": _bar("2026-09-16", 151, 155, 147, 154.0),
            "600875": _bar("2026-09-16", 26, 27, 25, 26.2),
        }
        days["600183"]["prev_close"] = 149.0
        days["600875"]["prev_close"] = 24.5
        pool = build_tau_pool_from_holding_bars(hist, days)
        day = pool.get("2026-09-16") or {}
        self.assertGreaterEqual(len(day.get("pool_gaps") or []), 2)
        self.assertIsNotNone(day.get("sector_gap_breadth"))
        seed_tau_cross_section_day("2026-09-16", day)
        xs = _tau_cross_section_kwargs("600183", "2026-09-16")
        self.assertIsNotNone(xs.get("sector_gap_breadth"))
        self.assertEqual(len(xs.get("pool_gaps") or []), len(day.get("pool_gaps") or []))
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)

    def test_tau_xs_fallback_does_not_cache_empty_pool(self):
        from unittest.mock import patch

        from core.t0.score_policy import (
            _TAU_XS_DAY_CACHE,
            _tau_cross_section_kwargs,
            seed_tau_cross_section_day,
        )

        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)
        with patch(
            "core.t0.score_policy.load_bars_by_code_for_tau_pool",
            return_value={"600183": [_bar("2026-09-15", 148, 150, 147, 149)]},
        ):
            xs = _tau_cross_section_kwargs("600183", "2026-09-16")
        self.assertEqual(xs, {})
        self.assertNotIn("2026-09-16", _TAU_XS_DAY_CACHE)
        seed_tau_cross_section_day(
            "2026-09-16",
            {"pool_gaps": [1.0, -0.5], "sector_gap_breadth": 0.5, "ref_by_code": {}},
        )
        filled = _tau_cross_section_kwargs("600183", "2026-09-16")
        self.assertAlmostEqual(float(filled.get("sector_gap_breadth") or -1), 0.5)
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)

    def test_t0_cs_universe_matches_live_and_backtest(self):
        """盯盘与做 T 回测共用纸面持仓 ∪ 观察池；单票回测并入同一宇宙。"""
        from core.signal.minute_tau_feats import peer_codes_for_sector_ret
        from core.t0.score_policy import (
            clear_score_model_cache,
            current_t0_cs_universe_codes,
            set_t0_cs_universe_codes,
            t0_cs_universe_codes,
        )

        paper = {
            "holdings": [
                {"stock_code": "600183"},
                {"stock_code": "600875"},
            ]
        }
        live = t0_cs_universe_codes(holdings=paper["holdings"])
        bt = t0_cs_universe_codes("600183", paper=paper)
        self.assertEqual(live[:2], ["600183", "600875"])
        self.assertEqual(bt[:2], ["600183", "600875"])
        self.assertEqual(live, bt)
        set_t0_cs_universe_codes(live)
        self.assertEqual(current_t0_cs_universe_codes(), live)
        self.assertEqual(peer_codes_for_sector_ret()[:2], live[:2])
        from core.watching.store import WATCHING_MAX_SIZE
        from core.t0.score_policy import t0_cs_universe_cap

        self.assertEqual(t0_cs_universe_cap(), WATCHING_MAX_SIZE)
        many = [f"{i:06d}" for i in range(30)]
        set_t0_cs_universe_codes(many)
        self.assertEqual(peer_codes_for_sector_ret(), many)
        clear_score_model_cache()

    def test_tau_xs_fallback_uses_cs_universe_memo(self):
        from unittest.mock import patch

        from core.t0.score_policy import (
            _TAU_XS_DAY_CACHE,
            _tau_cross_section_kwargs,
            clear_score_model_cache,
            set_t0_cs_universe_codes,
        )

        clear_score_model_cache()
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)
        set_t0_cs_universe_codes(["600183", "600875"])
        bars = {
            "600183": [
                _bar("2026-09-15", 148, 150, 147, 149.0),
                _bar("2026-09-16", 151, 155, 147, 154.0),
            ],
            "600875": [
                _bar("2026-09-15", 24, 25, 23, 24.5),
                _bar("2026-09-16", 26, 27, 25, 26.2),
            ],
        }
        bars["600183"][-1]["prev_close"] = 149.0
        bars["600875"][-1]["prev_close"] = 24.5
        with patch(
            "core.t0.score_policy.load_bars_by_code_for_tau_pool",
            return_value=bars,
        ) as load:
            xs = _tau_cross_section_kwargs("600183", "2026-09-16")
        self.assertEqual(set(load.call_args[0][0]), {"600183", "600875"})
        self.assertIsNotNone(xs.get("sector_gap_breadth"))
        self.assertGreaterEqual(len(xs.get("pool_gaps") or []), 2)
        self.assertIsNotNone(xs.get("sector_gap_median"))
        clear_score_model_cache()
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)

    def test_tau_xs_fallback_empty_memo_uses_watching_universe(self):
        from unittest.mock import patch

        from core.t0.score_policy import (
            _TAU_XS_DAY_CACHE,
            _tau_cross_section_kwargs,
            clear_score_model_cache,
        )

        clear_score_model_cache()
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)
        bars = {
            "600183": [
                _bar("2026-09-15", 148, 150, 147, 149.0),
                _bar("2026-09-16", 151, 155, 147, 154.0),
            ],
            "600000": [
                _bar("2026-09-15", 10, 11, 9, 10.0),
                _bar("2026-09-16", 10.5, 11, 10, 10.4),
            ],
        }
        bars["600183"][-1]["prev_close"] = 149.0
        bars["600000"][-1]["prev_close"] = 10.0
        with patch(
            "core.t0.score_policy.t0_cs_universe_codes",
            return_value=["600183", "600000"],
        ) as uni, patch(
            "core.t0.score_policy.load_bars_by_code_for_tau_pool",
            return_value=bars,
        ) as load:
            xs = _tau_cross_section_kwargs("600183", "2026-09-16")
        uni.assert_called()
        self.assertEqual(set(load.call_args[0][0]), {"600183", "600000"})
        self.assertGreaterEqual(len(xs.get("pool_gaps") or []), 2)
        clear_score_model_cache()
        _TAU_XS_DAY_CACHE.pop("2026-09-16", None)

    def test_build_tau_pool_watching_holdings_merges_watch_and_holdings(self):
        from unittest.mock import patch

        from core.t0.score_policy import build_tau_pool_watching_holdings

        hist = {"600183": [_bar("2026-09-15", 148, 150, 147, 149.0)]}
        days = {"600183": _bar("2026-09-16", 151, 155, 147, 154.0)}
        days["600183"]["prev_close"] = 149.0
        watch_bars = {
            "600000": [
                _bar("2026-09-15", 10, 11, 9, 10.0),
                _bar("2026-09-16", 10.5, 11, 10, 10.4),
            ]
        }
        watch_bars["600000"][-1]["prev_close"] = 10.0
        with patch(
            "core.t0.score_policy.t0_cs_universe_codes",
            return_value=["600183", "600000"],
        ), patch(
            "core.t0.score_policy.load_bars_by_code_for_tau_pool",
            return_value=watch_bars,
        ):
            pool = build_tau_pool_watching_holdings(
                hist, days, holdings=[{"stock_code": "600183"}]
            )
        day = pool.get("2026-09-16") or {}
        self.assertEqual(len(day.get("pool_gaps") or []), 2)
        self.assertIn("600183", day.get("gaps_by_code") or {})
        self.assertIn("600000", day.get("gaps_by_code") or {})

    def test_attach_dual_score_pit_prefers_sector_gap_median(self):
        """gap_vs_sector = gap − sector_gap_median（同行参照，非池中位数）。"""
        from core.signal.dual_score.tau import attach_dual_score_pit

        item = {
            "stock_code": "600000",
            "predicted_score": 1.0,
            "predicted_score_eod": 1.0,
            "_pool_gaps": [1.0, 2.0, 3.0],  # 池中位=2 → 若误用则 gap_vs=0
        }
        quote = {
            "date": "2025-11-02",
            "open": 102.0,
            "prev_close": 100.0,
            "price_raw": 102.0,
        }
        bars = [_bar("2025-11-01", 100, 101, 99, 100.0)]
        out = attach_dual_score_pit(
            item,
            quote=quote,
            bars=bars,
            rem_model_doc=None,
            sector_gap_breadth=0.1,
            sector_gap_median=0.5,
            fuse_intraday=True,
        )
        ft = out.get("features_tau") or {}
        self.assertAlmostEqual(float(ft.get("gap_pct")), 2.0, places=4)
        # 2.0 - 0.5 = 1.5；若用池中位 2.0 则得 0
        self.assertAlmostEqual(float(ft.get("gap_vs_sector")), 1.5, places=4)

    def test_compute_scores_map_shares_pool_gaps(self):
        from core.t0.score_policy import (
            clear_score_model_cache,
            compute_scores_map_from_bars,
            scores_have_any,
        )

        clear_score_model_cache()

        def _hist(n=25, start=40.0):
            bars = []
            px = start
            for i in range(n):
                o = px
                c = px * (1.0 + (0.008 if i % 2 == 0 else -0.004))
                bars.append(_bar(f"2025-10-{(i % 28) + 1:02d}", o, max(o, c) * 1.01, min(o, c) * 0.99, c))
                px = c
            return bars, px

        h1, px1 = _hist(25, 40)
        h2, px2 = _hist(25, 55)
        specs = [
            {"code": "600000", "hist_bars": h1, "day_bar": _bar("2025-11-01", px1 * 1.03, px1 * 1.04, px1, px1 * 1.02)},
            {"code": "600001", "hist_bars": h2, "day_bar": _bar("2025-11-01", px2 * 0.97, px2, px2 * 0.95, px2 * 0.98)},
        ]
        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            side_effect=lambda code, hist, **kw: {
                "y_eod": 0.5,
                "y_tau": 0.4 if (kw.get("pool_gaps") and len(kw.get("pool_gaps") or []) >= 2) else None,
                "y_trade": 0.5,
                "y_on": 0.1,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "compute",
                "_pool_n": len(kw.get("pool_gaps") or []),
            },
        ) as mocked:
            out = compute_scores_map_from_bars(specs)
            self.assertEqual(mocked.call_count, 2)
            # 两次调用都应带上截面 gaps；开盘 hydrate 禁分钟
            for call in mocked.call_args_list:
                self.assertGreaterEqual(len(call.kwargs.get("pool_gaps") or []), 2)
                self.assertEqual(call.kwargs.get("use_minute_tau"), False)
            self.assertTrue(scores_have_any(out.get("600000")))

    def test_compute_miss_does_not_read_book_or_ledger(self):
        from core.t0.score_policy import resolve_scores_for_code, scores_have_any

        empty = {
            "y_eod": None,
            "y_tau": None,
            "y_trade": None,
            "y_on": None,
            "y_nowcast": None,
            "y_check": None,
            "eod_trust": None,
        }
        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            return_value=dict(empty),
        ) as mocked:
            sc = resolve_scores_for_code(
                "600000",
                hist_bars=[_bar("2026-01-01", 1, 1, 1, 1)] * 20,
                day_bar=_bar("2026-01-02", 1, 1, 1, 1),
                source="ledger",
                allow_fallback=True,
            )
        mocked.assert_called_once()
        self.assertFalse(scores_have_any(sc))
        self.assertNotEqual(sc.get("_score_source"), "live_book")
        self.assertNotEqual(sc.get("_score_source"), "ledger")

    def test_backtest_dual_y_compute_not_ledger(self):
        from core.t0.score_policy import clear_score_model_cache

        clear_score_model_cache()
        bars = []
        px = 50.0
        for i in range(40):
            d = f"2026-01-{(i % 28) + 1:02d}"
            o = px
            h = o * 1.03
            l = o * 0.97
            c = o * (1.005 if i % 2 == 0 else 0.995)
            bars.append(_bar(d, o, h, l, c))
            px = c
        minute_by_date = {str(b["date"]): _mins_hl(bar=b) for b in bars}
        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            return_value={
                "y_eod": 0.5,
                "y_tau": 0.8,
                "y_trade": 0.4,
                "y_on": 0.1,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "compute",
            },
        ) as mocked:
            out = backtest_t0_on_bars(
                bars,
                initial_shares=1000,
                initial_cost=50,
                rules=_rules(
                    direction="dual_y",
                    min_range_pct=0.5,
                    y_tau_enter=0.25,
                    path_mode="first_touch",
                    use_atr=False,
                ),
                stock_code="600000",
                initial_cash=50000,
                minute_by_date=minute_by_date,
            )
            self.assertTrue(out.get("success"))
            self.assertGreater(mocked.call_count, 0)

    def test_dual_y_sell_then_buy_and_cover(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=1.0,
                fill_mode="optimistic",
                must_cover_same_day_sell_then_buy=False,
                # 破带测路径：ŷ=0；关掉 τ/path 入场与强同号
                y_tau_enter=0.0,
                y_path_enter=0.0,
                y_use_path=False,
            ),
            scores=_scores_r(-0.5),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "sell_then_buy")
        self.assertFalse((out.get("cover_policy") or {}).get("must_cover"))
        self.assertGreater(out.get("sold_qty") or 0, 0)

    def test_dual_y_buy_then_sell(self):
        bar = _bar("2026-01-10", 100, 105, 96, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=1.0,
                fill_mode="optimistic",
                y_tau_enter=0.0,
                y_path_enter=0.0,
                y_use_path=False,
            ),
            scores=_scores_r(0.5),
            minute_bars=_mins_lh(bar=bar),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "buy_then_sell")

    def test_dual_y_scaled_trigger_vs_weak_trade(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        rules = _rules(
            direction="dual_y",
            path_mode="first_touch",
            min_range_pct=1.0,
            fill_mode="optimistic",
            t0_ratio=0.4,
            y_tau_enter=0.0,
            y_path_enter=0.0,
            y_trade_strong=2.0,
            y_eod_strong=5.0,
            y_ratio_cut=0.75,
        )
        strong = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={**_scores_r(-0.5), "y_trade": 1.20, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        weak = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={**_scores_r(-0.5), "y_trade": 0.20, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        self.assertTrue(strong["success"])
        self.assertTrue(weak["success"])
        self.assertFalse(strong.get("skipped"))
        self.assertFalse(weak.get("skipped"))
        # 动仓固定；弱信号压低目标价
        self.assertAlmostEqual(float(strong.get("t0_ratio") or 0), float(weak.get("t0_ratio") or 0))
        self.assertEqual(strong.get("sold_qty"), weak.get("sold_qty"))
        self.assertNotIn("trigger_scale", strong)
        self.assertNotIn("sell_trigger_pct", strong)


    def test_load_y_tau_map_alias(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"y_tau_map": "follow"})
        self.assertNotIn("y_tau_map", cfg)

    def test_dual_y_upgrade_acceptance_flags_flat_trades(self):
        from core.t0.viz import summarize_dual_y_upgrade_acceptance

        days = [
            {
                "skipped": False,
                "sold_qty": 100,
                "pnl": 10,
                "direction": "buy_then_sell",
                "scores": {"y_tau": 0.45, "tau_realized": 0.5},
            },
            {
                "skipped": False,
                "sold_qty": 100,
                "pnl": 20,
                "direction": "buy_then_sell",
                "scores": {"y_tau": 0.80, "tau_realized": 0.9},
            },
        ]
        acc = summarize_dual_y_upgrade_acceptance(
            days, rules={"y_tau_enter": 0.6}
        )
        self.assertTrue(acc["ok"])
        self.assertEqual(acc["flat_tau_trades"], 0)
        self.assertEqual(acc["y_tau_enter"], 0.0)

    def test_nowcast_gate_is_retired(self):
        """Kalman / y_nc 不再写入新分。"""
        from core.t0.score_policy import scores_from_item

        scores = scores_from_item(
            {
                "y_eod": 0.08,
                "y_tau": 0.12,
                "predicted_score_nowcast": -0.40,
                "gap_pct": -6.23,
            }
        )
        self.assertAlmostEqual(float(scores.get("y_nowcast")), -0.40, places=2)
        self.assertIsNone(scores.get("y_nc"))
        self.assertIsNone(scores.get("y_nc_oc"))


class TestT0Viz(unittest.TestCase):
    def test_cover_completed_treats_intentional_abandon(self):
        """正/反 T 主动 abandon 计完成；defer_eod_pending / 缺 exit_reason 仍 incomplete。"""
        from core.t0.viz import _cover_completed

        self.assertTrue(
            _cover_completed(
                {
                    "direction": "sell_then_buy",
                    "sold_qty": 400,
                    "covered_qty": 0,
                    "exit_reason": "abandon_cover",
                }
            )
        )
        self.assertTrue(
            _cover_completed(
                {
                    "direction": "buy_then_sell",
                    "bought_qty": 400,
                    "sold_back_qty": 0,
                    "exit_reason": "abandon_cover",
                }
            )
        )
        self.assertTrue(
            _cover_completed(
                {
                    "direction": "buy_then_sell",
                    "bought_qty": 400,
                    "sold_back_qty": 0,
                    "exit_reason": "abandon_cover_cap",
                }
            )
        )
        self.assertFalse(
            _cover_completed(
                {
                    "direction": "buy_then_sell",
                    "bought_qty": 400,
                    "sold_back_qty": 0,
                    "exit_reason": "defer_eod_pending",
                }
            )
        )
        self.assertFalse(
            _cover_completed(
                {
                    "direction": "buy_then_sell",
                    "bought_qty": 400,
                    "sold_back_qty": 0,
                }
            )
        )

    def test_y_tau_attribution_hit_miss(self):
        from core.t0.viz import build_y_tau_attribution

        days = [
            {
                "date": "2026-01-10",
                "direction": "buy_then_sell",
                "open": 100.0,
                "close": 102.0,
                "pnl": 10.0,
                "exposure_pnl": 0,
                "sold_qty": 0,
                "bought_qty": 100,
                "r_realized": 2.0,
                "direction_features": {"y_τc": 0.8, "gap_pct": 0.5},
            },
            {
                "date": "2026-01-11",
                "direction": "sell_then_buy",
                "open": 100.0,
                "close": 98.0,
                "pnl": -5.0,
                "exposure_pnl": 0,
                "sold_qty": 100,
                "bought_qty": 0,
                "r_realized": -2.0,
                "direction_features": {"y_τc": 0.6, "gap_pct": 0.2},
            },
        ]
        att = build_y_tau_attribution(days)
        self.assertEqual(att["traded_with_tau"], 2)
        self.assertEqual(att["hit"], 1)
        self.assertEqual(att["miss"], 1)
        self.assertAlmostEqual(att["oc_hit_rate_pct"], 50.0)

    def test_traded_score_portrait_labels_and_hits(self):
        from core.t0.viz import build_backtest_score_portrait, build_traded_score_portrait

        days = [
            {
                "date": "2026-01-10",
                "direction": "buy_then_sell",
                "open": 10.0,
                "close": 10.5,
                "pnl": 10.0,
                "bought_qty": 100,
                "sold_qty": 100,
                "scores": {
                    "y_τc": 0.5,
                    "tau_realized": 5.0,
                },
            },
            {
                "date": "2026-01-11",
                "direction": "buy_then_sell",
                "open": 10.0,
                "close": 9.5,
                "pnl": -8.0,
                "bought_qty": 100,
                "sold_qty": 100,
                "scores": {
                    "y_τc": 0.4,
                    "tau_realized": -5.0,
                },
            },
            {
                "date": "2026-01-12",
                "skipped": True,
                "signal_skip": True,
                "reason": "dual_y：y_τ↔y_path异号跳过",
                "scores": {
                    "y_τc": 0.5,
                    "tau_realized": 1.0,
                },
            },
        ]
        port = build_traded_score_portrait(days)
        self.assertEqual(port["n_traded"], 2)
        self.assertEqual(port["label_tau"]["pos"], 1)
        self.assertEqual(port["label_tau"]["neg"], 1)
        self.assertEqual(port["tau_hit"]["hit"], 1)
        self.assertEqual(port["tau_hit"]["miss"], 1)
        self.assertNotIn("path_hit", port)
        self.assertNotIn("y_hl_hit", port)

        all_port = build_backtest_score_portrait(days)
        self.assertEqual(all_port["scope"], "all")
        self.assertEqual(all_port["n_days"], 3)
        self.assertEqual(all_port["n_skipped"], 1)
        self.assertEqual(all_port["tau_hit"]["hit"], 2)
        self.assertEqual(all_port["tau_hit"]["miss"], 1)
        self.assertEqual(all_port["traded"]["n_traded"], 2)
        self.assertEqual(all_port["traded"]["tau_hit"]["miss"], 1)
        self.assertIn("by_slot", all_port)
        self.assertGreaterEqual(all_port["by_slot"]["n_slots"], 1)
        self.assertGreaterEqual(all_port["by_slot"]["slots"][0]["n_days"], 1)

    def test_score_portrait_by_slot_splits_clocks(self):
        from core.t0.viz import build_backtest_score_portrait

        days = [
            {
                "date": "2026-03-01",
                "t0_slots_enabled": True,
                "open": 10.0,
                "close": 10.5,
                "prev_close": 10.0,
                "tau_realized": 5.0,
                "path_realized": 3.0,
                "eod_realized": 5.0,
                "scores": {"y_eod": 1.0, "y_trade": 0.8},
                "t0_slot_results": [
                    {
                        "id": "s1",
                        "hm": "10:00",
                        "skipped": False,
                        "bought_qty": 100,
                        "sold_qty": 100,
                        "pnl": 5,
                        "scores": {
                            "y_τc": 1.0,
                            "y_path": 2.0,
                            "y_trade": 0.9,
                        },
                    },
                    {
                        "id": "s2",
                        "hm": "10:30",
                        "skipped": True,
                        "reason": "dual_y：异号",
                        "scores": {"y_τc": 0.8, "y_path": -1.0},
                    },
                    {
                        "id": "s3",
                        "hm": "11:00",
                        "skipped": True,
                        "scores": {"y_τc": 0.5, "y_path": 0.4},
                    },
                    {
                        "id": "s4",
                        "hm": "11:30",
                        "skipped": True,
                        "scores": {"y_τc": 0.2, "y_path": -0.3},
                    },
                ],
            },
            {
                "date": "2026-03-02",
                "t0_slots_enabled": True,
                "open": 10.0,
                "close": 9.5,
                "prev_close": 10.0,
                "tau_realized": -5.0,
                "path_realized": -2.0,
                "eod_realized": -5.0,
                "scores": {"y_eod": -1.0, "y_trade": -0.7},
                "t0_slot_results": [
                    {
                        "id": "s1",
                        "hm": "10:00",
                        "skipped": True,
                        "scores": {"y_τc": -0.5, "y_path": -1.0},
                    },
                    {
                        "id": "s2",
                        "hm": "10:30",
                        "skipped": False,
                        "bought_qty": 100,
                        "sold_qty": 100,
                        "pnl": -3,
                        "scores": {"y_τc": -0.8, "y_path": -1.5},
                    },
                ],
            },
            {
                # 无槽位明细：仍进各钟样本；eod/trade 用日级分
                "date": "2026-03-03",
                "skipped": True,
                "open": 10.0,
                "close": 10.2,
                "prev_close": 10.0,
                "tau_realized": 2.0,
                "path_realized": 1.0,
                "eod_realized": 2.0,
                "scores": {
                    "y_τc": 0.6,
                    "y_path": 0.5,
                    "y_eod": 0.4,
                    "y_trade": 0.3,
                },
            },
        ]
        port = build_backtest_score_portrait(days)
        slots = {s["hm"]: s for s in port["by_slot"]["slots"]}
        self.assertTrue({"10:00", "10:30", "11:00", "11:30"}.issubset(set(slots)))
        self.assertIn("09:35", slots)
        self.assertEqual(port["n_days"], 3)
        for hm in ("09:35", "10:00", "10:30", "11:00", "11:30"):
            self.assertEqual(slots[hm]["n_days"], 3, hm)
            self.assertIn("tau_hit", slots[hm])
            self.assertIn("y_tc_hit", slots[hm])
        self.assertEqual(slots["10:00"]["n_traded"], 1)
        self.assertEqual(slots["10:00"]["tau_hit"]["hit"], 2)
        self.assertEqual(slots["10:00"]["tau_hit"]["flat"], 1)  # 03-03 无该钟ŷ
        self.assertEqual(slots["10:30"]["n_traded"], 1)
        self.assertEqual(slots["10:30"]["traded"]["tau_hit"]["hit"], 1)
        self.assertEqual(slots["11:00"]["n_with_yhat"], 1)
        self.assertEqual(slots["11:30"]["n_with_yhat"], 1)
        self.assertEqual(slots["09:35"]["n_with_yhat"], 0)

    def test_score_portrait_by_slot_uses_close_band_scan(self):
        """分槽应读扫描每根 ŷ，而不是只统计破带开轮钟。"""
        from core.t0.viz import build_backtest_score_portrait

        days = [
            {
                "date": "2026-04-01",
                "t0_slots_enabled": True,
                "tau_realized": 2.0,
                "path_realized": 1.0,
                "open": 10.0,
                "close": 10.2,
                "skipped": True,
                "scores": {"y_τc": 0.5, "y_path": 0.4},
                "close_band_scan": [
                    {"hm": "09:35", "y_τc": 0.4, "y_path": 0.3},
                    {"hm": "09:40", "y_τc": 1.5, "y_path": 1.2},
                    {"hm": "11:00", "y_τc": 1.8, "y_path": 1.5},
                ],
                "t0_slot_results": [],
            },
            {
                "date": "2026-04-02",
                "t0_slots_enabled": True,
                "tau_realized": -1.0,
                "path_realized": -0.5,
                "open": 10.0,
                "close": 9.9,
                "sold_qty": 100,
                "bought_qty": 100,
                "pnl": 1.0,
                "scores": {"y_τc": -0.2, "y_path": -0.1},
                "close_band_scan": [
                    {"hm": "09:35", "y_τc": -0.8, "y_path": -0.6},
                    {"hm": "09:40", "y_τc": -0.9, "y_path": -0.7},
                    {"hm": "11:00", "y_τc": -1.2, "y_path": -0.9},
                ],
                "t0_slot_results": [
                    {
                        "id": "r1",
                        "hm": "09:35",
                        "skipped": False,
                        "bought_qty": 100,
                        "sold_qty": 100,
                        "pnl": 1.0,
                        "scores": {"y_τc": -0.8, "y_path": -0.6},
                    }
                ],
            },
        ]
        port = build_backtest_score_portrait(days)
        slots = {s["hm"]: s for s in port["by_slot"]["slots"]}
        for hm in ("09:35", "09:40", "11:00"):
            self.assertEqual(slots[hm]["n_days"], 2, hm)
            self.assertEqual(slots[hm]["n_with_yhat"], 2, hm)
        self.assertEqual(slots["09:35"]["n_traded"], 1)
        self.assertEqual(slots["09:40"]["n_traded"], 0)
        # 09:35 扫描 ŷ 对全日标签：+0.4 vs +2 hit；-0.8 vs -1 hit
        self.assertEqual(slots["09:35"]["tau_hit"]["hit"], 2)
        self.assertEqual(slots["09:40"]["tau_hit"]["hit"], 2)
        # 日级预估改用 09:35，不应被跳过日晚钟垫高
        self.assertEqual(port["tau_hit"]["hit"], 2)
        self.assertEqual(port["n_days"], 2)

    def test_merge_score_portraits_pads_missing_slot_clocks(self):
        from core.t0.viz import _merge_score_portraits

        a = {
            "n_days": 100,
            "n_traded": 30,
            "n_skipped": 70,
            "n_signal_skip": 0,
            "label_tau": {"pos": 1, "neg": 0, "zero": 0, "missing": 0},
            "label_path": {"pos": 1, "neg": 0, "zero": 0, "missing": 0},
            "label_eod": {"pos": 0, "neg": 0, "zero": 0, "missing": 0},
            "label_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 0},
            "pred_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 0},
            "tau_hit": {"hit": 1, "miss": 0, "flat": 0},
            "path_hit": {"hit": 1, "miss": 0, "flat": 0},
            "eod_hit": {"hit": 0, "miss": 0, "flat": 0},
            "trade_hit": {"hit": 0, "miss": 0, "flat": 0},
            "by_slot": {
                "slots": [
                    {
                        "hm": "09:35",
                        "n_days": 100,
                        "n_traded": 30,
                        "n_with_yhat": 30,
                        "tau_hit": {"hit": 10, "miss": 10, "flat": 80},
                        "path_hit": {"hit": 10, "miss": 10, "flat": 80},
                        "pred_joint": {"same_sign": 20, "opposite_sign": 10, "flat": 70},
                    }
                ]
            },
        }
        b = {
            "n_days": 10,
            "n_traded": 1,
            "n_skipped": 9,
            "n_signal_skip": 0,
            "label_tau": {"pos": 1, "neg": 0, "zero": 0, "missing": 0},
            "label_path": {"pos": 1, "neg": 0, "zero": 0, "missing": 0},
            "label_eod": {"pos": 0, "neg": 0, "zero": 0, "missing": 0},
            "label_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 0},
            "pred_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 0},
            "tau_hit": {"hit": 1, "miss": 0, "flat": 0},
            "path_hit": {"hit": 1, "miss": 0, "flat": 0},
            "eod_hit": {"hit": 0, "miss": 0, "flat": 0},
            "trade_hit": {"hit": 0, "miss": 0, "flat": 0},
            "by_slot": {
                "slots": [
                    {
                        "hm": "09:35",
                        "n_days": 10,
                        "n_traded": 1,
                        "n_with_yhat": 1,
                        "tau_hit": {"hit": 1, "miss": 0, "flat": 9},
                        "path_hit": {"hit": 1, "miss": 0, "flat": 9},
                        "pred_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 9},
                    },
                    {
                        "hm": "09:40",
                        "n_days": 10,
                        "n_traded": 1,
                        "n_with_yhat": 1,
                        "tau_hit": {"hit": 1, "miss": 0, "flat": 9},
                        "path_hit": {"hit": 1, "miss": 0, "flat": 9},
                        "pred_joint": {"same_sign": 1, "opposite_sign": 0, "flat": 9},
                    },
                ]
            },
        }
        merged = _merge_score_portraits([a, b])
        slots = {s["hm"]: s for s in merged["by_slot"]["slots"]}
        self.assertEqual(slots["09:35"]["n_days"], 110)
        self.assertEqual(slots["09:40"]["n_days"], 110)
        self.assertEqual(slots["09:40"]["n_with_yhat"], 1)
        self.assertEqual(slots["09:40"]["tau_hit"]["hit"], 1)

    def test_score_portrait_ignores_old_oc_fields(self):
        """画像不读 y_tau / y_tau_oc。没有 y_τc 时 τ 命中不计。"""
        from core.t0.viz import build_score_portrait

        days = [
            {
                "date": "2026-02-01",
                "open": 10.0,
                "close": 10.2,
                "scores": {
                    "y_tau": -0.5,
                    "y_tau_oc": 1.0,
                    "tau_realized": 2.0,
                },
            },
            {
                "date": "2026-02-02",
                "skipped": True,
                "scores": {
                    "y_tau": -0.5,
                    "tau_realized": 2.0,
                    "features_tau": {"ret_open_to_tau": 2.01},
                },
            },
        ]
        port = build_score_portrait(days)
        self.assertEqual(port["tau_hit"]["hit"], 0)
        self.assertEqual(port["tau_hit"]["miss"], 0)
        self.assertEqual(port["tau_hit"]["flat"], 2)

    def test_score_portrait_r_oc_tc_hits(self):
        """画像命中：R_τ / y_τc 各自对标签。"""
        from core.t0.viz import build_score_portrait

        days = [
            {
                "date": "2026-03-01",
                "open": 10.0,
                "close": 10.2,
                "skipped": True,
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": 1.8,
                        "y_oc": 1.8,
                        "y_τc": 1.5,
                        "r_hat": 1.6,
                        "r_realized": 2.0,
                    }
                ],
                "scores": {"tau_realized": 2.0},
            },
            {
                "date": "2026-03-02",
                "open": 10.0,
                "close": 9.8,
                "skipped": True,
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": -1.0,
                        "y_oc": -1.0,
                        "y_τc": 1.0,
                        "r_hat": 1.0,
                        "r_realized": -2.0,
                    }
                ],
                "scores": {"tau_realized": -2.0},
            },
        ]
        port = build_score_portrait(days)
        self.assertNotIn("oc_hit", port)
        self.assertEqual(port["r_tau_hit"]["hit"], 1)
        self.assertEqual(port["r_tau_hit"]["miss"], 1)
        self.assertEqual(port["y_tc_hit"]["hit"], 1)
        self.assertEqual(port["y_tc_hit"]["miss"], 1)

    def test_score_portrait_t30_hits(self):
        """画像 ŷ_τ30 命中 + τ30旁路（破带方向同号）。"""
        from core.t0.viz import build_score_portrait, build_score_portrait_by_slot

        days = [
            {
                "date": "2026-03-01",
                "open": 10.0,
                "close": 10.2,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": 1.8,
                        "y_τ30": 0.7,
                        "y_t30_realized": 0.40,
                        "pick": "buy_then_sell",
                    }
                ],
            },
            {
                "date": "2026-03-02",
                "open": 10.0,
                "close": 9.8,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": -1.0,
                        "y_τ30": 0.3,
                        "y_t30_realized": 0.50,
                        "pick": "buy_then_sell",
                    }
                ],
            },
        ]
        port = build_score_portrait(days)
        self.assertEqual(port["y_t30_hit"]["hit"], 1)
        self.assertEqual(port["y_t30_hit"]["miss"], 1)
        self.assertEqual(port["y_t30_band"]["hit"], 1)
        self.assertEqual(port["y_t30_band"]["miss"], 1)
        slots = {
            s["hm"]: s for s in (build_score_portrait_by_slot(days).get("slots") or [])
        }
        s = slots["09:35"]
        self.assertEqual(s["y_t30_hit"]["hit"], 1)
        self.assertEqual(s["y_t30_hit"]["miss"], 1)
        self.assertEqual(s["y_t30_band"]["hit"], 1)
        self.assertEqual(s["y_t30_band"]["miss"], 1)

    def test_score_portrait_t45_t75_hits(self):
        """画像 ŷ_τ45 / ŷ_τ75 命中 + 旁路。"""
        from core.t0.viz import build_score_portrait, build_score_portrait_by_slot

        days = [
            {
                "date": "2026-03-01",
                "open": 10.0,
                "close": 10.2,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": 1.8,
                        "y_τ45": 0.7,
                        "y_t45_realized": 0.40,
                        "y_τ75": 0.65,
                        "y_t75_realized": 0.50,
                        "pick": "buy_then_sell",
                    }
                ],
            },
            {
                "date": "2026-03-02",
                "open": 10.0,
                "close": 9.8,
                "skipped": True,
                "direction": "buy_then_sell",
                "close_band_scan": [
                    {
                        "hm": "09:35",
                        "c": 10.0,
                        "y_tau": -1.0,
                        "y_τ45": 0.3,
                        "y_t45_realized": 0.50,
                        "y_τ75": 0.35,
                        "y_t75_realized": 0.60,
                        "pick": "buy_then_sell",
                    }
                ],
            },
        ]
        port = build_score_portrait(days)
        self.assertEqual(port["y_t45_hit"]["hit"], 1)
        self.assertEqual(port["y_t45_hit"]["miss"], 1)
        self.assertEqual(port["y_t45_band"]["hit"], 1)
        self.assertEqual(port["y_t45_band"]["miss"], 1)
        self.assertEqual(port["y_t75_hit"]["hit"], 1)
        self.assertEqual(port["y_t75_hit"]["miss"], 1)
        self.assertEqual(port["y_t75_band"]["hit"], 1)
        self.assertEqual(port["y_t75_band"]["miss"], 1)
        slots = {
            s["hm"]: s for s in (build_score_portrait_by_slot(days).get("slots") or [])
        }
        s = slots["09:35"]
        self.assertEqual(s["y_t45_hit"]["hit"], 1)
        self.assertEqual(s["y_t75_hit"]["hit"], 1)

    def test_backtest_includes_viz(self):
        bars = [_bar("d0", 100, 105, 98, 101) for _ in range(25)]
        for i, b in enumerate(bars):
            b["date"] = f"2026-01-{i+1:02d}"
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(
                direction="dual_y",
                path_mode="first_touch",
                min_range_pct=0.5,
            ),
            stock_code="688047",
        )
        self.assertTrue(report.get("success"))
        self.assertIn("viz", report)
        self.assertIn("skip_categories", report["viz"])
        self.assertIn("daily_activity", report["viz"])

    def test_classify_skip_reason(self):
        from core.t0.viz import classify_t0_skip_reason

        self.assertEqual(
            classify_t0_skip_reason("前序ŷ_τw不足（须3根）"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("前序ŷ_τw命中 1/3 < 67%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("y_complexity=0.820>0.70 太折跳过"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("y_cx=0.820>0.70 太折跳过"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("y_tpd=0.820>0.40 反转过密跳过"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade（即时算分失败）"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_path（path_ridge 模型未 promote）"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade 快照"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116<0.25 横盘跳过"),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116%<0.25% 横盘跳过"),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("|R̂_τ|=0.200%<0.5% 未过入场（超额不足）"),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("|ŷ_τc|=0.200%<0.5% 未过入场（横盘）"),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("ŷ_oc=+0.20 未过入场"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("ŷ_τc=+0.20 未过入场（须|ŷ_τc|>=0.5）"),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("缺 y_τc，未估 C_τ"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y：|y_τ|=0.450% 弱信号区（0.4%≤|y_τ|<0.6%）跳过"
            ),
            "y_tc_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_check=conflict 禁止做T"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y[trend]：冲突 y_eod=0.185% vs y_τ=-0.113%（先验≠盘中）跳过"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T分钟路径未触及卖出价"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T分钟路径未开成第一腿"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T分钟路径未触及低吸位"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T确认根未开第一腿"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T确认根未开第一腿"),
            "trigger_miss",
        )
        # 历史账本文案仍可归类
        self.assertEqual(
            classify_t0_skip_reason("正T本轮窗口结束未开第一腿"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T本轮窗口结束未开第一腿"),
            "trigger_miss",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "正T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓"
            ),
            "tplus1",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T待固定前缀 2/12"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T固定前缀后半上涨 0/2=0%<50%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "固定前缀未确认，放弃正T（正T固定前缀后半上涨 0/2=0%<50%）"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("振幅不足 0.30% < 1.00%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("前缀振幅12.000%>|ŷ_path|5.872%：空间用尽跳过"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "固定前缀未确认，放弃正T（前缀振幅12.000%>|ŷ_path|5.872%：空间用尽跳过）"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("前缀振幅>|ŷ_path|×裕度"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("前缀振幅>|ŷ_path|"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "正T买价未低于τ带 108.000≥102.000（开盘×(1+5×ŷ_τ=0.400%)）"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "反T卖价未高于τ带 96.000≤98.000（开盘×(1+5×ŷ_τ=-0.400%)）"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "正T卖价未高于τ出场带 100.400≤100.500（开盘×(1+1×ŷ_τ=0.500%)）"
            ),
            "tau_exit_price",
        )
        from core.t0.viz import summarize_skip_reason_label

        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.009%<0.02% 横盘跳过"),
            "ŷ_τc未过入场",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.016%<0.02% 横盘跳过"),
            "ŷ_τc未过入场",
        )
        self.assertEqual(
            summarize_skip_reason_label("正T分钟路径未开成第一腿"),
            "正T未开第一腿",
        )
        self.assertEqual(
            summarize_skip_reason_label("正T分钟路径未触及低吸位"),
            "正T未触达",
        )
        self.assertEqual(
            summarize_skip_reason_label(
                "正T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓"
            ),
            "T+1无可卖",
        )
        self.assertEqual(
            summarize_skip_reason_label("反T分钟路径未触及卖出价"),
            "反T未触卖出",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_path=10.000%≤15.0% 未过门槛"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "other",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "其它",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_trade|=0.002%<0.01% 未过入场"),
            "其它",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_trade|=0.085%<0.15% 未过入场"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_trade=-0.381%<floor-0.15% 资格不足"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y：y_τ=0.450% 与 y_nowcast=-0.300% 异号跳过"
            ),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T上移振幅 0.80%<2.00%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T：现金不够 1 手（现金 1000 …）"),
            "cash",
        )
        self.assertEqual(
            classify_t0_skip_reason("盘中已有成交腿，跳过整单回放（防重复落账）"),
            "intraday_legs_open",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T复合确认：未达开盘锚偏离 0.12%<0.30%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("环境闸：前缀振幅 0.30%<0.50%"),
            "other",
        )
        self.assertEqual(
            classify_t0_skip_reason("多轮均未成交"),
            "multi_slot_miss",
        )

    def test_walk_t0_skip_row_path_mode_uses_cfg_not_day_rules(self):
        """跳过日明细 path_mode 回退 cfg，不引用未定义的 day_rules。"""
        import inspect
        from core.t0 import backtest as bt

        src = inspect.getsource(bt._walk_t0)
        self.assertNotIn("day_rules", src)

    def test_build_and_merge_viz(self):
        from core.t0.viz import build_t0_viz_payload, merge_t0_viz_payloads

        days = [
            {
                "date": "2026-08-11",
                "skipped": True,
                "reason": "dual_y：缺 y_eod/y_τ/y_trade 快照",
                "signal_skip": True,
                "close": 10.5,
                "shares": 1000,
            },
            {
                "date": "2026-08-12",
                "direction": "sell_then_buy",
                "sold_qty": 100,
                "covered_qty": 100,
                "pnl": 150.0,
                "exposure_pnl": 0,
                "direction_score": 0.55,
                "close": 10.8,
                "shares": 1000,
            },
        ]
        viz = build_t0_viz_payload(days, stock_code="688047", stock_name="龙芯", initial_shares=1000)
        self.assertEqual(viz["trade_count"], 1)
        self.assertEqual(viz["skip_count"], 1)
        self.assertEqual(len(viz["cumulative_pnl"]), 1)
        self.assertEqual(viz["cumulative_pnl"][0]["cum_pnl"], 150.0)
        self.assertTrue(any(c["id"] == "missing_scores" for c in viz["skip_categories"]))
        sc = viz["stock_contrib"][0]
        self.assertEqual(sc["skip_top_id"], "missing_scores")
        self.assertIn("skip_breakdown", sc)
        self.assertEqual(sc["skip_breakdown"][0]["id"], "missing_scores")
        self.assertIn("color", sc["skip_breakdown"][0])
        self.assertEqual(sc["shares"], 1000)
        self.assertEqual(sc["window_start_date"], "2026-08-11")
        self.assertEqual(sc["window_end_date"], "2026-08-12")
        self.assertAlmostEqual(float(sc["start_price"]), 10.5, places=2)
        self.assertAlmostEqual(float(sc["end_price"]), 10.8, places=2)
        self.assertAlmostEqual(float(sc["return_pct"]), 150.0 / 10500.0 * 100.0, places=2)

        viz2 = build_t0_viz_payload(
            [
                {
                    "date": "2026-08-13",
                    "direction": "buy_then_sell",
                    "bought_qty": 100,
                    "sold_back_qty": 100,
                    "pnl": -20.0,
                    "exposure_pnl": 0,
                    "direction_score": -0.4,
                }
            ],
            stock_code="600519",
            stock_name="茅台",
        )
        merged = merge_t0_viz_payloads([viz, viz2])
        self.assertEqual(merged["trade_count"], 2)
        self.assertEqual(len(merged["stock_contrib"]), 2)
        self.assertEqual(merged["cumulative_pnl"][-1]["cum_pnl"], 130.0)
        self.assertIn("summary", merged)
        self.assertIn("y_tau_scatter", viz)
        self.assertEqual(viz["summary"]["y_tau_enter"], 0.0)

    def test_scatter_y_tau_not_direction_score_on_y_trade_skip(self):
        from core.t0.viz import build_t0_viz_payload, extract_scores

        day = {
            "date": "2026-08-17",
            "skipped": True,
            "signal_skip": True,
            "direction_score": 0.05,
            "direction_reason": "dual_y：|y_trade|=0.05%<0.15% 未过入场",
            "skip_category": "y_trade_weak",
            "direction_features": {
                "y_τc": 0.132,
                "y_trade": 0.05,
            },
        }
        sc = extract_scores(day)
        self.assertAlmostEqual(sc["y_τc"], 0.132, places=3)
        self.assertNotIn("y_trade", sc)

        viz = build_t0_viz_payload([day], stock_code="601898")
        scatter = viz.get("y_tau_scatter") or []
        self.assertEqual(len(scatter), 1)
        self.assertAlmostEqual(float(scatter[0]["y_τc"]), 0.132, places=3)
        self.assertNotIn("y_tau", scatter[0])
        self.assertEqual(scatter[0]["outcome"], "signal_skip")
        self.assertEqual(scatter[0].get("skip_category"), "y_trade_weak")

    def test_scatter_r_pct_includes_reverse_t_without_y_tau(self):
        from core.t0.viz import build_t0_viz_payload

        days = [
            {
                "date": "2026-09-11",
                "stock_code": "600584",
                "stock_name": "长电科技",
                "t0_slots_enabled": True,
                "bought_qty": 100,
                "sold_qty": 100,
                "pnl": -10,
                "close_band_scan": [
                    {"hm": "09:35", "r_pct": -1.38, "y_tau": 1.48},
                ],
                "t0_slot_results": [
                    {
                        "id": "r1",
                        "hm": "09:35",
                        "skipped": False,
                        "direction": "buy_then_sell",
                        "bought_qty": 100,
                        "sold_qty": 100,
                        "scores": {"y_tau": 1.48, "y_oc": 1.48},
                        "close_band": {"r_pct": -1.38},
                    }
                ],
            },
            {
                "date": "2026-09-01",
                "stock_code": "000938",
                "stock_name": "紫光股份",
                "t0_slots_enabled": True,
                "bought_qty": 100,
                "sold_qty": 100,
                "pnl": 5,
                "close_band_scan": [
                    {"hm": "09:35", "r_pct": 3.77},
                ],
                "t0_slot_results": [
                    {
                        "id": "r1",
                        "hm": "09:35",
                        "skipped": False,
                        "direction": "sell_then_buy",
                        "bought_qty": 100,
                        "sold_qty": 100,
                        "scores": {"y_to": -3.6},
                        "close_band": {"r_pct": 3.77},
                    }
                ],
            },
        ]
        viz = build_t0_viz_payload(days)
        scatter = viz.get("y_tau_scatter") or []
        traded = [p for p in scatter if p.get("outcome") == "traded"]
        self.assertEqual(len(traded), 2)
        rps = sorted(float(p["r_pct"]) for p in traded)
        self.assertAlmostEqual(rps[0], -1.38, places=2)
        self.assertAlmostEqual(rps[1], 3.77, places=2)
        dirs = {p.get("direction") for p in traded}
        self.assertEqual(dirs, {"buy_then_sell", "sell_then_buy"})
        rev = next(p for p in traded if p.get("direction") == "sell_then_buy")
        self.assertIsNone(rev.get("y_τc"))

    def test_attach_summary_to_viz(self):
        from core.t0.viz import attach_summary_to_viz, build_t0_viz_payload

        report = {
            "t0_pnl_total": 100.0,
            "participate_rate_pct": 12.5,
            "viz": build_t0_viz_payload([]),
        }
        attach_summary_to_viz(report)
        self.assertNotIn("compare", report["viz"])
        self.assertEqual(report["viz"]["summary"]["participate_rate_pct"], 12.5)



class TestT0Api(unittest.TestCase):
    def test_t0_backtest_api_mocked(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "success": True,
            "task": "t0_backtest",
            "stock_code": "600519",
            "t0_trade_days": 5,
            "t0_pnl_total": 120.5,
            "t0_win_rate_pct": 60.0,
        }
        with patch.object(deps.quant, "run_t0_backtest", return_value=mock) as run_bt:
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/t0-backtest",
                json={
                    "code": "茅台",
                    "from_paper": False,
                    "sync": True,
                    "score_model_role": "live",
                },
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(run_bt.call_args.kwargs.get("score_model_role"), "live")

    def test_t0_backtest_api_defaults_to_job(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
            import web.deps as deps
        except ImportError:
            self.skipTest("fastapi not installed")

        mock = {
            "ok": True,
            "success": True,
            "background": True,
            "job": {"id": "abc", "status": "running", "kind": "t0_backtest"},
        }
        with patch.object(deps.quant, "start_t0_backtest_job", return_value=mock) as started:
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/t0-backtest",
                json={"from_paper": True},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["background"])
        started.assert_called_once()
        self.assertEqual(started.call_args.kwargs.get("score_model_role"), "research")

    def test_index_has_t0_controls(self):
        follow = os.path.join(ROOT, "web", "static", "partials", "follow_panel.html")
        with open(follow, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("paper-t0-backtest", text)
        self.assertNotIn("paper-t0-metrics", text)
        self.assertIn("paper-t0-action-status", text)
        self.assertIn("paper-t0-run", text)
        self.assertIn("paper-t0-confirm", text)
        self.assertIn("paper-t0-worker-enabled", text)
        self.assertIn("paper-t0-worker-m-lastrun", text)
        self.assertIn("paper-t0-worker-trades", text)
        self.assertIn("paper-t0-worker-desk", text)
        self.assertIn("paper-rebalance-worker-enabled", text)
        self.assertIn("paper-rebalance-worker-ledger", text)
        self.assertIn("paper-rebalance-worker-desk", text)
        self.assertIn("自动调仓后台进程", text)
        self.assertIn("paper-t0-run", text)
        self.assertNotIn('id="paper-t0-form"', text)
        self.assertIn("手动预演", text)
        self.assertIn("自动做 T 后台进程", text)
        self.assertNotIn("paper-t0-auto-enabled", text)
        self.assertNotIn("paper-t0-auto-run", text)
        self.assertNotIn("启用自动做T", text)
        self.assertNotIn("落账控制", text)
        self.assertIn("持仓操作流水", text)
        self.assertIn("paper-holdings-table", text)
        self.assertNotIn("follow-section-fund", text)
        self.assertNotIn("资金调整", text)
        ov = text[text.find('id="follow-section-overview"') : text.find('id="follow-section-holdings"')]
        self.assertIn("paper-deposit", ov)
        self.assertIn("paper-withdraw", ov)
        self.assertIn("paper-reset", ov)
        self.assertLess(ov.find('id="follow-north-star"'), ov.find('id="follow-overview-fund"'))
        self.assertNotIn('id="paper-rebalance"', text)
        self.assertNotIn("跑一日", text)

    def test_replay_has_t0_backtest_controls(self):
        path = os.path.join(ROOT, "web", "static", "partials", "replay_panel.html")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("paper-t0-backtest", text)
        self.assertIn("paper-t0-metrics", text)
        self.assertIn("paper-t0-viz", text)
        self.assertIn("paper-t0-days", text)
        self.assertIn("paper-t0-lookback", text)
        self.assertIn("paper-t0-score-model-role", text)
        self.assertIn("replay-section-t0", text)
        self.assertIn("paper-t0-form", text)
        self.assertIn("replay-t0-kpi-row", text)
        self.assertIn("replay-t0-matrix", text)
        self.assertIn("dual_y", text)
        self.assertIn("paper-path-matrix-form", text)


class TestT0HoldingsVirtualSizing(unittest.TestCase):
    def test_align_daily_bars_to_minute_drops_uncovered(self):
        from quant.research.t0_backtest import _align_daily_bars_to_minute

        bars = [
            {"date": "2026-07-01", "close": 10},
            {"date": "2026-07-02", "close": 11},
            {"date": "2026-07-03", "close": 12},
            {"date": "2026-07-04", "close": 13},
        ]
        mins = {
            "2026-07-02": [{"t": 1}, {"t": 2}],
            "2026-07-03": [{"t": 1}, {"t": 2}],
            "2026-07-04": [{"t": 1}],  # <2 → 不可用
        }
        aligned, meta = _align_daily_bars_to_minute(bars, mins)
        self.assertTrue(meta.get("aligned"))
        self.assertEqual([b["date"] for b in aligned], ["2026-07-02", "2026-07-03"])
        self.assertEqual(meta.get("bars_before"), 4)
        self.assertEqual(meta.get("bars_after"), 2)

    def test_holdings_uses_virtual_shares_and_cash(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "stock_name": "茅台",
            "t0_pnl_total": 1000.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 5,
            "t0_cover_days": 5,
            "skip_days": 10,
            "signal_skip_days": 2,
            "sell_then_buy_days": 5,
            "buy_then_sell_days": 0,
            "sell_then_buy_pnl": 1000.0,
            "buy_then_sell_pnl": 0.0,
            "sell_then_buy_cover_days": 5,
            "buy_then_sell_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 15,
            "missing_minute_days": 0,
            "hold_mv_start": 1_000_000.0,
            "t0_win_days": 3,
            "t0_loss_days": 2,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)) as mocked:
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台", "shares": 200, "cost": 34}],
                lookback=20,
            )
        self.assertEqual(m.T0_BT_VIRTUAL_SHARES, 1_000.0)
        self.assertEqual(m.T0_BT_VIRTUAL_CASH, 200_000.0)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], m.T0_BT_VIRTUAL_SHARES)
        self.assertEqual(kwargs["initial_cash"], m.T0_BT_VIRTUAL_CASH)
        self.assertIsNone(kwargs["initial_cost"])
        self.assertTrue(out.get("virtual_sizing"))
        self.assertTrue(kwargs.get("skip_quote"))
        self.assertEqual(kwargs.get("compare_no_t0"), False)
        self.assertEqual(kwargs.get("stock_name"), "茅台")
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), 0.5)
        self.assertIn("虚拟每票", out.get("scope_label") or "")
        self.assertIn("本金", out.get("scope_label") or "")

    def test_holdings_passes_custom_virtual_sizing(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        fake = {
            "success": True,
            "stock_code": "600519",
            "t0_pnl_total": 200.0,
            "exposure_pnl_total": 0.0,
            "t0_trade_days": 1,
            "t0_cover_days": 1,
            "skip_days": 0,
            "signal_skip_days": 0,
            "sell_then_buy_days": 1,
            "buy_then_sell_days": 0,
            "sell_then_buy_pnl": 200.0,
            "buy_then_sell_pnl": 0.0,
            "sell_then_buy_cover_days": 1,
            "buy_then_sell_cover_days": 0,
            "uncover_days": 0,
            "minute_path_days": 1,
            "missing_minute_days": 0,
            "hold_mv_start": 50_000.0,
            "t0_win_days": 1,
            "t0_loss_days": 0,
            "trade_days_sample": [],
            "days": [],
            "viz": None,
        }
        with patch.object(m, "run_t0_backtest_for_code", return_value=dict(fake)) as mocked:
            out = m.run_t0_backtest_for_holdings(
                [{"stock_code": "600519", "stock_name": "茅台"}],
                lookback=10,
                virtual_shares=2000,
                virtual_cash=300_000,
            )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], 2000)
        self.assertEqual(kwargs["initial_cash"], 300_000)
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), round(200.0 / 300_000 * 100.0, 4))
        self.assertEqual(out["virtual_shares"], 2000)
        self.assertEqual(out["virtual_cash"], 300_000)

    def test_holdings_deadline_starts_before_tau_pool(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_holdings)
        dl_at = src.find("t_deadline = time.time()")
        tau_at = src.find("tau_pool = build_tau_pool_by_date")
        self.assertGreater(dl_at, 0)
        self.assertGreater(tau_at, 0)
        self.assertLess(dl_at, tau_at)
        self.assertGreaterEqual(m._HOLDINGS_DEADLINE_SEC, 600.0)
        self.assertLessEqual(m._HOLDINGS_DEADLINE_SEC, 1200.0)
        self.assertIn("progress_cb", src)
        self.assertIn("_one_progress", src)
        self.assertIn("scoring_model_role_context(requested)", src)
        self.assertIn("normalize_backtest_model_role", src)
        self.assertIn("准备回测", src)
        self.assertIn("_seed_pool_minutes", src)
        self.assertIn('label="加载截面 5m"', src)
        self.assertIn("_seed_pool_minutes(\n        pool_codes", src)
        self.assertIn("lookback_days=minute_span", src)
        self.assertIn("_fetch_minute_by_date", inspect.getsource(m._seed_pool_minutes))
        self.assertNotIn("_local_minute_by_date(code, period)", inspect.getsource(m._seed_pool_minutes))
        self.assertLess(src.find("准备回测"), src.find("_seed_pool_minutes"))
        self.assertLess(
            src.find("_seed_pool_minutes"), src.find("tau_pool = build_tau_pool_by_date")
        )
        seed_src = inspect.getsource(m._seed_pool_minutes)
        self.assertIn("加载截面 5m", seed_src)
        self.assertIn("seed_peer_minute_bars_map", seed_src)

    def test_seed_pool_minutes_uses_fetch_not_stale_only(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        msgs: list = []
        by_date = {"2026-09-01": [{"time": "09:35", "close": 10}]}
        with patch.object(m, "_fetch_minute_by_date", return_value=(by_date, {})) as fetched:
            with patch.object(m, "_local_minute_by_date") as loc:
                with patch("core.signal.minute_tau_feats.seed_peer_minute_bars_map") as seeded:
                    n = m._seed_pool_minutes(
                        ["600000", "000001"],
                        "5",
                        lookback_days=10,
                        progress_cb=lambda _c, _t, msg: msgs.append(msg),
                    )
        self.assertEqual(n, 2)
        self.assertEqual(fetched.call_count, 2)
        loc.assert_not_called()
        seeded.assert_called_once()
        self.assertTrue(any("加载截面 5m" in str(x) for x in msgs))
        self.assertEqual(fetched.call_args.kwargs.get("lookback_days"), 10)

    def test_t0_job_emits_prepare_before_run(self):
        import inspect

        from quant.services.quant_service_follow import QuantFollowMixin

        src = inspect.getsource(QuantFollowMixin.start_t0_backtest_job)
        self.assertIn('message="准备回测…"', src)
        self.assertLess(src.find("准备回测"), src.find("self.run_t0_backtest("))

    def test_for_code_always_seeds_pool_minutes(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_code)
        self.assertIn("_seed_pool_minutes(", src)
        extras_at = src.find("_seed_pool_minutes(")
        none_at = src.find("if tau_pool is None:")
        self.assertGreater(extras_at, 0)
        self.assertGreater(none_at, extras_at)
        self.assertIn("lookback_days=minute_span", src)

    def test_for_code_reuses_holdings_peer_minutes(self):
        import inspect

        from quant.research import t0_backtest as m

        src = inspect.getsource(m.run_t0_backtest_for_code)
        self.assertIn("reuse_cs", src)
        self.assertIn("peer_minute_seed_count", src)
        self.assertIn("if not reuse_cs:", src)
        self.assertLess(src.find("if not reuse_cs:"), src.find("_seed_pool_minutes("))

    def test_t0_walk_does_not_clear_score_cache_per_stock(self):
        import inspect

        from core.t0 import backtest as m

        src = inspect.getsource(m.backtest_t0_on_bars)
        self.assertIn("scoring_model_role_context(requested)", src)
        self.assertIn("normalize_backtest_model_role", src)
        self.assertNotIn("clear_score_model_cache", src)
        walk_src = inspect.getsource(m._walk_t0)
        self.assertIn("progress_cb", walk_src)
        self.assertIn("from core.research.holdout import current_scoring_model_role", walk_src)
        self.assertIn("current_scoring_model_role()", walk_src)
        sig = inspect.signature(m._walk_t0)
        self.assertIn("progress_cb", sig.parameters)

    def test_fetch_minute_prefers_complete_local_cache(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        # 齐窗日：铺满到 15:00 → 不打远端
        bars = []
        for i in range(80):
            mins = 9 * 60 + 35 + i * 5
            if 11 * 60 + 30 <= mins < 13 * 60:
                continue
            hh, mm = divmod(mins, 60)
            if hh > 15 or (hh == 15 and mm > 0):
                break
            bars.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        meta = {"data_source": "cache", "from_cache": True, "ok": True}
        with patch(
            "adapters.market.minute_history._load_stale_minute",
            return_value=(bars, meta),
        ), patch(
            "adapters.market.history.resolve_market_code", return_value=("CN", "600519")
        ), patch("core.ports.market.fetch_minute_bars") as remote:
            by_date, out_meta = m._fetch_minute_by_date("600519", period="5")
        remote.assert_not_called()
        self.assertIn("2026-08-20", by_date)
        self.assertTrue(out_meta.get("from_cache"))
        self.assertTrue(m._minute_day_complete(by_date["2026-08-20"]))

    def test_fetch_minute_refreshes_truncated_local_cache(self):
        """本地只有午前残缺日 → 补拉并用远端覆盖。"""
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        short = []
        for i in range(10):
            mins = 9 * 60 + 35 + i * 5
            hh, mm = divmod(mins, 60)
            short.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        full = []
        for i in range(80):
            mins = 9 * 60 + 35 + i * 5
            if 11 * 60 + 30 <= mins < 13 * 60:
                continue
            hh, mm = divmod(mins, 60)
            if hh > 15 or (hh == 15 and mm > 0):
                break
            full.append(
                {
                    "datetime": f"2026-08-20 {hh:02d}:{mm:02d}:00",
                    "date": "2026-08-20",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            )
        local_meta = {"data_source": "cache", "from_cache": True, "ok": True}
        with patch.object(
            m, "_local_minute_by_date", return_value=({"2026-08-20": short}, local_meta)
        ), patch(
            "core.ports.market.fetch_minute_bars",
            return_value=(full, {"ok": True, "from_cache": False}),
        ) as remote, patch(
            "core.ports.market.group_minute_bars_by_date",
            return_value={"2026-08-20": full},
        ):
            by_date, out_meta = m._fetch_minute_by_date("600519", period="5", timeout_sec=1)
        remote.assert_called_once()
        self.assertFalse(remote.call_args.kwargs.get("use_cache"))
        self.assertTrue(out_meta.get("refreshed_truncated"))
        self.assertTrue(m._minute_day_complete(by_date["2026-08-20"]))
        self.assertGreaterEqual(len(by_date["2026-08-20"]), 40)

    def test_align_prefers_complete_minute_days(self):
        from quant.research.t0_backtest import _align_daily_bars_to_minute

        bars = [
            {"date": "2026-07-01", "close": 10},
            {"date": "2026-07-02", "close": 11},
            {"date": "2026-07-03", "close": 12},
        ]
        short = [{"datetime": "2026-07-01 09:35:00"} for _ in range(10)]
        full = [{"datetime": f"2026-07-02 14:55:00"}] + [
            {"datetime": "2026-07-02 09:35:00"} for _ in range(45)
        ]
        # 故意把末根放到列表末尾供 complete 检测
        full = [{"datetime": "2026-07-02 09:35:00"} for _ in range(45)] + [
            {"datetime": "2026-07-02 14:55:00"}
        ]
        other = [{"datetime": "2026-07-03 14:55:00"} for _ in range(40)]
        mins = {
            "2026-07-01": short,
            "2026-07-02": full,
            "2026-07-03": other,
        }
        aligned, meta = _align_daily_bars_to_minute(bars, mins)
        self.assertEqual([b["date"] for b in aligned], ["2026-07-02", "2026-07-03"])
        self.assertEqual(meta.get("complete_minute_days"), 2)

    def test_fetch_minute_remote_skips_em_when_no_cache(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        with patch.object(m, "_local_minute_by_date", return_value=({}, {"ok": False})), patch(
            "core.ports.market.fetch_minute_bars", return_value=([], {"ok": False})
        ) as remote:
            by_date, meta = m._fetch_minute_by_date("600519", timeout_sec=1)
        remote.assert_called_once()
        self.assertTrue(remote.call_args.kwargs.get("skip_em"))
        self.assertFalse(by_date)
        self.assertIn("无分钟", str(meta.get("error") or ""))

    def test_call_with_timeout_does_not_wait_on_shutdown(self):
        import time

        from quant.research.t0_backtest import _call_with_timeout

        t0 = time.time()
        with self.assertRaises(TimeoutError):
            _call_with_timeout(lambda: time.sleep(30), 0.3)
        self.assertLess(time.time() - t0, 2.0)

    def test_tau_pool_bars_offline_only(self):
        from unittest.mock import patch

        from core.t0 import score_policy as sp

        seen = []

        def fake(code, limit=80, **kw):
            seen.append(dict(kw, code=code, limit=limit))
            return (
                [
                    {"date": "2026-08-01", "open": 10, "close": 10},
                    {"date": "2026-08-02", "open": 11, "close": 11},
                ],
                "cache",
            )

        with patch("core.data.facade.bars_and_source", side_effect=fake):
            out = sp.load_bars_by_code_for_tau_pool(["600519"], limit=40)
        self.assertIn("600519", out)
        self.assertTrue(seen[0].get("offline_ok"))
        self.assertTrue(seen[0].get("offline_only"))


class TestIntradaySkipLogic(unittest.TestCase):
    def test_should_process_skips_done_without_new_bar(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "done", "last_bar_ts": "2026-08-24 10:05:00"}
        self.assertFalse(
            should_process_intraday_stock(st, "2026-08-24 10:05:00", force_session_close=False)
        )
        self.assertTrue(
            should_process_intraday_stock(st, "2026-08-24 10:10:00", force_session_close=False)
        )

    def test_should_process_on_new_bar(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "idle", "last_bar_ts": "2026-08-24 10:05:00"}
        self.assertTrue(
            should_process_intraday_stock(st, "2026-08-24 10:10:00", force_session_close=False)
        )

    def test_force_session_close_retries_non_terminal(self):
        from core.t0.intraday import should_process_intraday_stock

        st = {"phase": "pending_cover", "last_bar_ts": "2026-08-24 14:55:00"}
        self.assertTrue(
            should_process_intraday_stock(st, "2026-08-24 14:55:00", force_session_close=True)
        )
        self.assertTrue(
            should_process_intraday_stock(
                {"phase": "done", "last_bar_ts": "2026-08-24 15:00:00"},
                "2026-08-24 15:00:00",
                force_session_close=True,
            )
        )
        self.assertFalse(
            should_process_intraday_stock(
                {"phase": "skipped", "last_bar_ts": "2026-08-24 15:00:00"},
                "2026-08-24 15:00:00",
                force_session_close=True,
            )
        )

    def test_done_holding_refreshes_scan_without_new_trades(self):
        from unittest.mock import patch

        from core.t0.intraday import PHASE_DONE, process_holding_intraday

        bar = _bar("2026-09-16", 150, 157, 147, 154)
        holding = {
            "stock_code": "600183",
            "stock_name": "生益科技",
            "shares": 500,
            "cost": 150,
        }
        old_trades = [
            {"side": "t0_buy", "price": 150.1, "shares": 100, "at": "2026-09-16 09:45:00"},
            {"side": "t0_sell", "price": 152.07, "shares": 100, "at": "2026-09-16 10:35:00"},
        ]
        st0 = {
            "phase": PHASE_DONE,
            "legs_written": 2,
            "last_bar_ts": "2026-09-16 10:40:00",
            "day_snapshot": {
                "stock_code": "600183",
                "trades": old_trades,
                "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                "close": 152.6,
            },
        }
        refreshed = {
            "stock_code": "600183",
            "trades": old_trades,
            "close_band_scan": [{"hm": "09:45", "y_oc": -0.62}],
            "close": 154.48,
        }
        paper = {"cash": 50000, "holdings": [holding], "trades": [], "rules": {}}
        with patch(
            "core.t0.slots.refresh_close_band_scan", return_value=dict(refreshed)
        ) as ref, patch("core.t0.slots.simulate_t0_day_slots") as sim:
            st, trades, snap = process_holding_intraday(
                code="600183",
                holding=holding,
                stock_state=st0,
                minute_bars=_mins_lh(bar=bar),
                bar=bar,
                cfg=_rules(t0_ratio=0.4, direction="dual_y"),
                sellable=500,
                cash=50000,
                atr_pct=None,
                hist_bars=None,
                scores=_scores_flat(),
                stance_code=None,
                coupling_mode="independent",
                as_of="2026-09-16",
                log_source="test",
                paper=paper,
                applied_legs=2,
                tau_pool_day={"pool_gaps": [1.0]},
            )
        sim.assert_not_called()
        ref.assert_called_once()
        self.assertEqual(ref.call_args.kwargs.get("tau_pool_day"), {"pool_gaps": [1.0]})
        self.assertEqual(trades, [])
        self.assertEqual(st.get("phase"), PHASE_DONE)
        self.assertEqual(st.get("legs_written"), 2)
        self.assertEqual(snap["close_band_scan"][0]["y_oc"], -0.62)
        self.assertEqual(snap["trades"], old_trades)
        self.assertEqual(paper.get("trades"), [])

    def test_build_intraday_desk_status_locked_rows(self):
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-08-25",
            "updated_at": 1.0,
            "stocks": {
                "600519": {
                    "phase": "skipped",
                    "legs_written": 0,
                    "day_snapshot": {
                        "stock_name": "贵州茅台",
                        "reason": "dual_y：横盘跳过",
                        "signal_skip": True,
                    },
                },
                "000001": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-25 10:05:00",
                    # idle 常缺 stock_name，由纸面持仓补全
                },
                "600050": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-25 09:40:00",
                    "stock_name": "中国联通",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/t0_intraday_state.json"
            import json

            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-08-25"
            ), patch(
                "core.paper.load_paper",
                return_value={
                    "holdings": [
                        {"stock_code": "000001", "stock_name": "平安银行"},
                        {"stock_code": "600050", "stock_name": "中国联通"},
                    ]
                },
            ):
                desk = mod.build_intraday_desk_status()
        self.assertTrue(desk["same_session"])
        self.assertTrue(desk.get("state_aligned"))
        self.assertEqual(desk["locked_count"], 1)
        self.assertEqual(desk["counts"]["skipped"], 1)
        self.assertEqual(desk["counts"]["idle"], 2)
        # 有 last_bar_ts 的按时间升序；无时间戳的（终锁）排最后
        self.assertEqual(
            [r["stock_code"] for r in desk["rows"]],
            ["600050", "000001", "600519"],
        )
        self.assertEqual(desk["rows"][0]["stock_name"], "中国联通")
        self.assertEqual(desk["rows"][1]["stock_name"], "平安银行")
        self.assertEqual(desk["rows"][2]["stock_name"], "贵州茅台")
        self.assertTrue(desk["rows"][2]["locked"])
        self.assertIn("横盘", desk["rows"][2]["reason"])

    def test_desk_idle_tplus1_reason_counts_as_locked(self):
        import json
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-09-10",
            "stocks": {
                "000938": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-09-10 11:05:00",
                    "stock_name": "紫光股份",
                    "day_snapshot": {
                        "reason": "正T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓",
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t0_intraday_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-09-10"
            ), patch(
                "core.paper.load_paper",
                return_value={
                    "holdings": [{"stock_code": "000938", "stock_name": "紫光股份"}]
                },
            ):
                desk = mod.build_intraday_desk_status()
        row = desk["rows"][0]
        self.assertEqual(row["phase"], "skipped")
        self.assertTrue(row["locked"])
        self.assertEqual(desk["locked_count"], 1)
        self.assertEqual(desk["counts"]["skipped"], 1)
        self.assertEqual(desk["counts"]["idle"], 0)

    def test_process_holding_tplus1_zero_sellable_locks(self):
        from unittest.mock import patch
        from core.t0.intraday import PHASE_SKIPPED, process_holding_intraday

        bar = _bar("2026-01-10", 99, 103, 97, 101)
        paper = {"cash": 50000, "holdings": [], "trades": [], "rules": {}}
        holding = {
            "stock_code": "000938",
            "stock_name": "紫光股份",
            "shares": 500,
            "cost": 100,
        }
        with patch(
            "core.t0.score_policy.rescore_scores_at_fixed_prefix",
            return_value=_scores_r(0.5),
        ):
            st, trades, snap = process_holding_intraday(
                code="000938",
                holding=holding,
                stock_state={},
                minute_bars=_mins_lh(bar=bar),
                bar=bar,
                cfg=_rules(t0_ratio=0.4, direction="dual_y"),
                sellable=0,
                cash=50000,
                atr_pct=None,
                hist_bars=None,
                scores=_scores_r(0.5),
                stance_code=None,
                coupling_mode="independent",
                as_of="2026-01-10",
                log_source="test",
                paper=paper,
                applied_legs=0,
            )
        self.assertEqual(st.get("phase"), PHASE_SKIPPED)
        self.assertTrue(st.get("score_locked"))
        self.assertEqual(trades, [])
        reason = str(st.get("reason") or snap.get("reason") or "")
        self.assertIn("T+1", reason)
        self.assertTrue("可卖旧仓" in reason or "无法先卖" in reason)

    def test_live_new_leg1_window(self):
        from core.t0.intraday import live_new_leg1_in_window

        latest = "2026-09-10 10:30:00"
        self.assertTrue(
            live_new_leg1_in_window(
                "2026-09-10 10:30:00", last_bar_ts="", latest_bar_ts=latest
            )
        )
        self.assertFalse(
            live_new_leg1_in_window(
                "2026-09-10 09:35:00", last_bar_ts="", latest_bar_ts=latest
            )
        )
        self.assertTrue(
            live_new_leg1_in_window(
                "2026-09-10 10:30:00",
                last_bar_ts="2026-09-10 10:25:00",
                latest_bar_ts=latest,
            )
        )
        self.assertFalse(
            live_new_leg1_in_window(
                "2026-09-10 09:40:00",
                last_bar_ts="2026-09-10 10:25:00",
                latest_bar_ts=latest,
            )
        )
        # 漏跳：中间 09:40 已过，只认当前 10:30
        self.assertFalse(
            live_new_leg1_in_window(
                "2026-09-10 09:40:00",
                last_bar_ts="2026-09-10 09:35:00",
                latest_bar_ts=latest,
            )
        )
        self.assertTrue(
            live_new_leg1_in_window(
                "2026-09-10 10:30:00",
                last_bar_ts="2026-09-10 09:35:00",
                latest_bar_ts=latest,
            )
        )

    def test_intraday_skips_stale_morning_leg1_on_first_tick(self):
        from unittest.mock import patch
        from core.t0.intraday import PHASE_IDLE, process_holding_intraday

        bar = _bar("2026-09-10", 25.92, 25.96, 25.64, 25.70)
        mins = _mins(
            "2026-09-10",
            [
                (935, 25.92, 25.96, 25.64, 25.70),
                (940, 25.70, 25.80, 25.63, 25.63),
                (1030, 25.63, 25.80, 25.60, 25.65),
            ],
        )
        fake_day = {
            "t0_slots_enabled": True,
            "direction_used": "sell_then_buy",
            "t0_slot_results": [
                {
                    "t0_slot": "r1",
                    "t0_slot_hm": "09:35",
                    "direction_used": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "shares": 100,
                            "price": 25.7,
                            "at": "2026-09-10 09:35:00",
                        }
                    ],
                }
            ],
            "trades": [
                {
                    "side": "t0_sell",
                    "shares": 100,
                    "price": 25.7,
                    "at": "2026-09-10 09:35:00",
                }
            ],
        }
        paper = {"cash": 80000, "holdings": [], "trades": [], "rules": {}, "operation_log": []}
        holding = {
            "stock_code": "600875",
            "stock_name": "东方电气",
            "shares": 300,
            "cost": 25,
        }
        with patch("core.t0.slots.simulate_t0_day_slots", return_value=fake_day):
            st, trades, snap = process_holding_intraday(
                code="600875",
                holding=holding,
                stock_state={},
                minute_bars=mins,
                bar=bar,
                cfg=_rules(direction="sell_then_buy", t0_ratio=0.4),
                sellable=300,
                cash=80000,
                atr_pct=None,
                hist_bars=None,
                scores=_scores_flat(),
                stance_code=None,
                coupling_mode="independent",
                as_of="2026-09-10",
                log_source="test",
                paper=paper,
                applied_legs=0,
            )
        self.assertEqual(trades, [])
        self.assertEqual(st.get("phase"), PHASE_IDLE)
        self.assertEqual(paper.get("trades") or [], [])
        self.assertEqual(st.get("last_bar_ts"), "2026-09-10 10:30:00")
        self.assertEqual((snap or {}).get("trades") or [], [])

    def test_intraday_applies_leg1_on_current_bar(self):
        from unittest.mock import patch
        from core.t0.intraday import PHASE_AFTER_LEG1, process_holding_intraday

        bar = _bar("2026-09-10", 25.92, 25.96, 25.64, 25.70)
        mins = _mins("2026-09-10", [(1030, 25.63, 25.80, 25.60, 25.65)])
        fake_day = {
            "t0_slots_enabled": True,
            "direction_used": "sell_then_buy",
            "t0_slot_results": [
                {
                    "t0_slot": "r1",
                    "t0_slot_hm": "10:30",
                    "direction_used": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "shares": 100,
                            "price": 25.65,
                            "at": "2026-09-10 10:30:00",
                        }
                    ],
                }
            ],
            "trades": [
                {
                    "side": "t0_sell",
                    "shares": 100,
                    "price": 25.65,
                    "at": "2026-09-10 10:30:00",
                }
            ],
        }
        paper = {"cash": 80000, "holdings": [], "trades": [], "rules": {}, "operation_log": []}
        holding = {
            "stock_code": "600875",
            "stock_name": "东方电气",
            "shares": 300,
            "cost": 25,
            "lots": [
                {
                    "shares": 300,
                    "bought_at": "2026-09-09T10:00:00",
                    "bought_date": "2026-09-09",
                }
            ],
        }
        with patch("core.t0.slots.simulate_t0_day_slots", return_value=fake_day):
            st, trades, snap = process_holding_intraday(
                code="600875",
                holding=holding,
                stock_state={},
                minute_bars=mins,
                bar=bar,
                cfg=_rules(direction="sell_then_buy", t0_ratio=0.4),
                sellable=300,
                cash=80000,
                atr_pct=None,
                hist_bars=None,
                scores=_scores_flat(),
                stance_code=None,
                coupling_mode="independent",
                as_of="2026-09-10",
                log_source="test",
                paper=paper,
                applied_legs=0,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(st.get("phase"), PHASE_AFTER_LEG1)
        self.assertTrue(trades[0].get("ts"))
        self.assertEqual((snap.get("trades") or [{}])[0].get("at"), "2026-09-10 10:30:00")
        self.assertTrue((snap.get("trades") or [{}])[0].get("ts"))

    def test_intraday_gap_does_not_replay_missed_bars(self):
        from unittest.mock import patch
        from core.t0.intraday import PHASE_AFTER_LEG1, process_holding_intraday

        bar = _bar("2026-09-10", 25.92, 25.96, 25.64, 25.70)
        mins = _mins(
            "2026-09-10",
            [
                (935, 25.92, 25.96, 25.64, 25.70),
                (940, 25.70, 25.80, 25.63, 25.63),
                (1030, 25.63, 25.80, 25.60, 25.65),
            ],
        )
        fake_day = {
            "t0_slots_enabled": True,
            "direction_used": "sell_then_buy",
            "t0_slot_results": [
                {
                    "t0_slot": "r1",
                    "t0_slot_hm": "09:40",
                    "direction_used": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "shares": 100,
                            "price": 25.63,
                            "at": "2026-09-10 09:40:00",
                        }
                    ],
                },
                {
                    "t0_slot": "r2",
                    "t0_slot_hm": "10:30",
                    "direction_used": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "shares": 100,
                            "price": 25.65,
                            "at": "2026-09-10 10:30:00",
                        }
                    ],
                },
            ],
            "trades": [
                {
                    "side": "t0_sell",
                    "shares": 100,
                    "price": 25.63,
                    "at": "2026-09-10 09:40:00",
                },
                {
                    "side": "t0_sell",
                    "shares": 100,
                    "price": 25.65,
                    "at": "2026-09-10 10:30:00",
                },
            ],
        }
        paper = {"cash": 80000, "holdings": [], "trades": [], "rules": {}, "operation_log": []}
        holding = {
            "stock_code": "600875",
            "stock_name": "东方电气",
            "shares": 300,
            "cost": 25,
            "lots": [
                {
                    "shares": 300,
                    "bought_at": "2026-09-09T10:00:00",
                    "bought_date": "2026-09-09",
                }
            ],
        }
        with patch("core.t0.slots.simulate_t0_day_slots", return_value=fake_day):
            st, trades, snap = process_holding_intraday(
                code="600875",
                holding=holding,
                stock_state={"last_bar_ts": "2026-09-10 09:35:00"},
                minute_bars=mins,
                bar=bar,
                cfg=_rules(direction="sell_then_buy", t0_ratio=0.4),
                sellable=300,
                cash=80000,
                atr_pct=None,
                hist_bars=None,
                scores=_scores_flat(),
                stance_code=None,
                coupling_mode="independent",
                as_of="2026-09-10",
                log_source="test",
                paper=paper,
                applied_legs=0,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(st.get("phase"), PHASE_AFTER_LEG1)
        self.assertAlmostEqual(float(trades[0].get("price") or 0), 25.65)
        self.assertEqual(str(trades[0].get("at") or "")[:16], "2026-09-10 10:30")
        self.assertEqual(len(snap.get("trades") or []), 1)

    def test_desk_status_falls_back_to_a_code_name(self):
        import json
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-08-26",
            "stocks": {
                "600029": {
                    "phase": "idle",
                    "legs_written": 0,
                    "last_bar_ts": "2026-08-26 09:40:00",
                    "wait_reason": "反T下移振幅 0.98%<1.00%",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t0_intraday_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-08-26"
            ), patch(
                "core.paper.load_paper",
                return_value={"holdings": [{"stock_code": "600029", "stock_name": ""}]},
            ), patch.object(
                mod,
                "_load_a_code_name_map",
                return_value={"600029": "南方航空"},
            ):
                desk = mod.build_intraday_desk_status()
        self.assertEqual(desk["rows"][0]["stock_code"], "600029")
        self.assertEqual(desk["rows"][0]["stock_name"], "南方航空")

    def test_resolve_stock_name_replaces_code_as_name(self):
        from core.t0 import intraday as mod

        with patch.object(
            mod, "_load_a_code_name_map", return_value={"000938": "紫光股份"}
        ):
            self.assertEqual(
                mod.resolve_stock_name("000938", fallback="000938"),
                "紫光股份",
            )
            row = {"stock_code": "000938", "stock_name": "000938"}
            mod.stamp_resolved_stock_names([row])
            self.assertEqual(row["stock_name"], "紫光股份")

    def test_load_paper_replaces_code_as_stock_name(self):
        import json
        import tempfile

        from core.paper.ledger import load_paper
        from core.t0 import intraday as mod

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cash": 1,
                        "holdings": [
                            {
                                "stock_code": "000938",
                                "stock_name": "000938",
                                "shares": 100,
                            }
                        ],
                        "trades": [
                            {
                                "stock_code": "000938",
                                "stock_name": "000938",
                                "side": "buy",
                            }
                        ],
                    },
                    f,
                )
            with patch.object(
                mod, "_load_a_code_name_map", return_value={"000938": "紫光股份"}
            ):
                paper = load_paper(path)
        self.assertEqual(paper["holdings"][0]["stock_name"], "紫光股份")
        self.assertEqual(paper["trades"][0]["stock_name"], "紫光股份")

    def test_desk_status_from_holdings_when_state_stale(self):
        import json
        import tempfile
        from unittest.mock import patch

        from core.t0 import intraday as mod

        fake = {
            "session_date": "2026-08-25",
            "stocks": {
                "600519": {
                    "phase": "done",
                    "legs_written": 2,
                    "last_bar_ts": "2026-08-25 15:00:00",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t0_intraday_state.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fake, f)
            with patch.object(mod, "_state_path", return_value=path), patch(
                "core.market.calendar.resolve_session_date", return_value="2026-08-28"
            ), patch(
                "core.paper.load_paper",
                return_value={
                    "holdings": [
                        {"stock_code": "600519", "stock_name": "贵州茅台"},
                        {"stock_code": "000001", "stock_name": "平安银行"},
                    ]
                },
            ):
                desk = mod.build_intraday_desk_status()
        self.assertFalse(desk.get("state_aligned"))
        self.assertTrue(desk["same_session"])
        self.assertEqual(desk["universe_count"], 2)
        codes = {r["stock_code"] for r in desk["rows"]}
        self.assertEqual(codes, {"600519", "000001"})
        idle = next(r for r in desk["rows"] if r["stock_code"] == "000001")
        self.assertEqual(idle["phase"], "idle")
        self.assertIn("Worker", idle.get("reason") or "")

    def test_dual_y_threshold_lock_and_session_align(self):
        from core.t0.intraday import (
            _dual_y_threshold_skip,
            _retryable_skip,
            accept_intraday_dual_y_scores,
            align_session_day_context,
            unlock_retryable_skipped,
        )

        # 盘口可重试；dual_y 门槛对齐后终锁（不进 retryable）
        self.assertTrue(_retryable_skip("振幅不足 0.30% < 0.80%"))
        self.assertTrue(_retryable_skip("反T分钟路径未触及卖出价"))
        self.assertTrue(_retryable_skip("正T待固定前缀 2/12"))
        self.assertTrue(_retryable_skip("反T待固定前缀 2/12"))
        self.assertFalse(_retryable_skip("dual_y：|y_τ|=0.027%<0.1% 横盘跳过"))
        # path_abandon 文案含「待回落/待反弹/振幅」不得误判可重试
        self.assertFalse(
            _retryable_skip("前缀无回落确认，放弃反T（反T待回落 0.20%<0.50%）")
        )
        self.assertFalse(
            _retryable_skip("前缀无低吸空间，放弃正T（正T下移振幅 0.30%<0.80%）")
        )
        self.assertFalse(
            _retryable_skip("固定前缀未确认，放弃正T（正T固定前缀后半上涨 0/2=0%<50%）")
        )
        self.assertFalse(
            _retryable_skip("固定前缀未确认，放弃反T（反T固定前缀后半下跌 0/2=0%<50%）")
        )
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_τ|=0.027%<0.1% 横盘跳过"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：y_path=10.000%≤15.0% 未过门槛"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_path|=1.1<30.0 横盘跳过"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：|y_trade|=0.041%<0.1% 未过入场"))
        self.assertTrue(_dual_y_threshold_skip("dual_y：y_τ=0.45% 与 y_nowcast=-0.3% 异号跳过"))
        self.assertFalse(_retryable_skip("stance=avoid 跳过做T"))

        # 旧版 dual_y 软锁（无 score_locked）可解锁一次重评
        unlocked = unlock_retryable_skipped(
            {
                "phase": "skipped",
                "legs_written": 0,
                "reason": "dual_y：|y_τ|=0.027%<0.1% 横盘跳过",
            }
        )
        self.assertIsNotNone(unlocked)
        self.assertEqual(unlocked["phase"], "idle")
        # 对齐后门槛终锁带 score_locked，不再解锁
        self.assertIsNone(
            unlock_retryable_skipped(
                {
                    "phase": "skipped",
                    "score_locked": True,
                    "reason": "dual_y：|y_τ|=0.027%<0.1% 横盘跳过",
                }
            )
        )
        self.assertIsNone(
            unlock_retryable_skipped(
                {"phase": "skipped", "reason": "stance=avoid 跳过做T"}
            )
        )

        self.assertIsNone(
            accept_intraday_dual_y_scores(
                {"y_tau": -0.027, "y_eod": 0.5, "_score_source": "live_book"},
                source="compute",
            )
        )
        self.assertIsNotNone(
            accept_intraday_dual_y_scores(
                {"y_tau": 0.13, "y_eod": 0.5, "_score_source": "compute"},
                source="compute",
            )
        )

        daily = [
            {"date": "2026-08-24", "open": 10, "high": 11, "low": 9.5, "close": 10.5},
        ]
        mins = [
            {"datetime": "2026-08-25 09:35:00", "open": 10.6, "high": 10.7, "low": 10.5, "close": 10.65},
            {"datetime": "2026-08-25 09:40:00", "open": 10.65, "high": 10.8, "low": 10.6, "close": 10.7},
        ]
        aligned = align_session_day_context(
            session_date="2026-08-25",
            daily_bars=daily,
            minute_by_day={"2026-08-25": mins},
        )
        self.assertTrue(aligned["ok"])
        self.assertEqual(aligned["bar"]["date"], "2026-08-25")
        self.assertEqual(aligned["bar"]["open"], 10.6)
        self.assertEqual(len(aligned["hist"]), 1)
        self.assertEqual(aligned["aligned"], "minute_open")
        # 勿回退昨分钟
        stale = align_session_day_context(
            session_date="2026-08-25",
            daily_bars=daily,
            minute_by_day={"2026-08-24": mins},
        )
        self.assertTrue(stale.get("pending"))
        self.assertFalse(stale.get("ok"))

    def test_past_morning_close_forces_dual_y_gate(self):
        from datetime import datetime
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        from core.t0.intraday import needs_midday_dual_y_gate, past_morning_close

        sh = ZoneInfo("Asia/Shanghai")
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 11, 29, tzinfo=sh),
        ):
            self.assertFalse(past_morning_close())
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 11, 30, tzinfo=sh),
        ):
            self.assertTrue(past_morning_close())
        with patch(
            "core.signal.session_pit.shanghai_now",
            return_value=datetime(2026, 8, 25, 13, 5, tzinfo=sh),
        ):
            self.assertTrue(past_morning_close())

        self.assertTrue(
            needs_midday_dual_y_gate(
                {"phase": "idle", "legs_written": 0, "last_bar_ts": "2026-08-25 11:30:00"}
            )
        )
        self.assertTrue(
            needs_midday_dual_y_gate(
                {"phase": "idle", "legs_written": 0, "direction": "sell_then_buy"}
            )
        )
        self.assertFalse(
            needs_midday_dual_y_gate(
                {"phase": "after_leg1", "legs_written": 1, "direction": "sell_then_buy"}
            )
        )
        self.assertFalse(
            needs_midday_dual_y_gate(
                {"phase": "skipped", "score_locked": True, "legs_written": 0}
            )
        )
        from core.t0.intraday import lock_zero_legs_after_morning

        locked = lock_zero_legs_after_morning(
            code="600519",
            holding={"stock_name": "贵州茅台"},
            session_date="2026-08-25",
        )
        self.assertEqual(locked["phase"], "skipped")
        self.assertTrue(locked["score_locked"])
        self.assertIn("无成交腿", locked["reason"])

class TestT0AutoWorker(unittest.TestCase):
    def test_worker_tick_waits_outside_session(self):
        from core.t0.auto_worker import T0AutoWorker

        w = T0AutoWorker()
        with patch(
            "core.t0.intraday.session_in_market",
            return_value=(False, "2026-08-24"),
        ), patch(
            "core.t0.auto_worker._session_closed",
            return_value=(False, "2026-08-24"),
        ):
            w._enabled = True
            w._tick()
        self.assertIn("待机", w.status()["last_tick_message"])

    def test_worker_tick_interval_is_five_minutes(self):
        from core.t0.auto_worker import TICK_INTERVAL_SEC
        from core.t0.config import T0_INTRADAY_TICK_SEC

        self.assertEqual(TICK_INTERVAL_SEC, T0_INTRADAY_TICK_SEC)
        self.assertEqual(TICK_INTERVAL_SEC, 300.0)

    def test_set_worker_starts_and_stops(self):
        import tempfile
        from core.t0.auto_worker import t0_auto_worker
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            with patch("core.t0.auto_worker._worker_config_path") as path_mock, patch(
                "services.paper_service.PaperService.run_t0_intraday_tick",
                return_value={"ok": True, "trade_count": 0, "skipped": True, "reason": "mock"},
            ), patch(
                "core.t0.intraday.session_in_market",
                return_value=(False, "2026-08-24"),
            ), patch(
                "core.t0.auto_worker._session_closed",
                return_value=(False, "2026-08-24"),
            ):
                path = os.path.join(tmp, "t0_auto_worker.json")
                path_mock.return_value = path
                paper_path = os.path.join(tmp, "paper.json")
                svc = PaperService(paper_path)
                svc.init()
                out = svc.set_t0_worker(True)
                self.assertTrue(out["worker"]["running"])
                self.assertTrue(out["t0_auto"]["enabled"])
                svc.set_t0_worker(False)
                self.assertFalse(t0_auto_worker.is_running())
                paper = __import__("core.paper", fromlist=["load_paper"]).load_paper(
                    paper_path
                )
                self.assertFalse(paper["rules"]["t0_auto"]["enabled"])


class TestT0AutoLastRunDetail(unittest.TestCase):
    def test_last_run_stores_compact_results(self):
        import tempfile
        from services.paper_trades import _compact_t0_result_rows, _write_t0_auto_last_run
        from core.paper import load_paper, save_paper

        sample = [
            {
                "stock_code": "600519",
                "stock_name": "茅台",
                "date": "2026-08-24",
                "skipped": True,
                "reason": "振幅不足",
                "direction_used": None,
                "trades": [{"side": "sell", "price": 100, "shares": 100, "at": "10:05"}],
                "minute_bars": 48,
            },
            {
                "stock_code": "000001",
                "stock_name": "平安",
                "date": "2026-08-24",
                "skipped": False,
                "direction_used": "sell_then_buy",
                "pnl": 12.5,
                "exposure_pnl": 0,
                "sold_qty": 100,
                "covered_qty": 100,
                "trades": [
                    {"side": "t0_sell", "price": 10.2, "shares": 100, "at": "10:15"},
                    {"side": "t0_buy", "price": 10.0, "shares": 100, "at": "14:30"},
                ],
            },
        ]
        compact = _compact_t0_result_rows(sample)
        self.assertEqual(len(compact), 2)
        self.assertTrue(compact[0].get("skip_category"))
        self.assertEqual(compact[1]["direction"], "sell_then_buy")
        self.assertNotIn("minute_bars", compact[0])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "rules": {},
                "trades": [],
                "operation_log": [],
            }
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": compact[1]["trades"],
                    "pnl_total": 12.5,
                    "results": sample,
                    "execution": {"t0": {"direction": "dual_y", "y_tau_map": "scalp"}},
                },
                source="paper_t0_auto",
            )
            save_paper(paper, path)
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(lr["source"], "paper_t0_auto")
            self.assertEqual(lr["session_date"], "2026-08-24")
            self.assertEqual(lr["rules"]["direction"], "dual_y")
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "000001")

    def test_compact_keeps_close_band_table_fields(self):
        from services.paper_trades import _compact_t0_result_rows

        compact = _compact_t0_result_rows(
            [
                {
                    "stock_code": "600875",
                    "date": "2026-09-10",
                    "skipped": False,
                    "direction_used": "sell_then_buy",
                    "pnl": 0,
                    "minute_bars": 48,
                    "scores": {
                        "y_tau": 0.08,
                        "y_path": 0.29,
                        "score_formula_terms": {"terms": [1, 2, 3]},
                        "factor_coefficients": {"momentum": 0.1},
                    },
                    "close_band_scan": [
                        {
                            "hm": "09:35",
                            "o": 25.5,
                            "l": 25.4,
                            "h": 25.8,
                            "c": 25.7,
                            "c_tau": 25.45,
                            "r_pct": 0.95,
                        }
                    ],
                    "forward_trace": [
                        {
                            "time": "09:35",
                            "open": 25.5,
                            "high": 25.8,
                            "low": 25.4,
                            "close": 25.7,
                            "leg1_fill": True,
                            "wait_reason": "drop-me",
                        }
                    ],
                    "t0_slot_results": [
                        {
                            "id": "r1",
                            "hm": "09:35",
                            "direction": "sell_then_buy",
                            "sold_qty": 100,
                            "trades": 1,
                            "close_band": {"c_tau": 25.45, "band_r_pct": 0.95},
                            "_t0_score_snap": {"huge": True},
                        }
                    ],
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                            "ts": "2026-09-10T10:29:15.941",
                            "t0_slot": "r1",
                            "t0_slot_hm": "09:35",
                        }
                    ],
                }
            ]
        )
        row = compact[0]
        self.assertNotIn("minute_bars", row)
        self.assertNotIn("score_formula_terms", row.get("scores") or {})
        self.assertAlmostEqual(float(row["scores"]["y_tau"]), 0.08)
        self.assertEqual(row["close_band_scan"][0]["c_tau"], 25.45)
        self.assertTrue(row["forward_trace"][0]["leg1_fill"])
        self.assertNotIn("wait_reason", row["forward_trace"][0])
        self.assertEqual(row["t0_slot_results"][0]["close_band"]["c_tau"], 25.45)
        self.assertNotIn("_t0_score_snap", row["t0_slot_results"][0])
        self.assertEqual(row["trades"][0]["t0_slot"], "r1")
        self.assertEqual(row["trades"][0]["ts"], "2026-09-10T10:29:15.941")

    def test_patch_last_run_scans_keeps_trades(self):
        from services.paper_trades import _patch_t0_last_run_scans

        paper = {
            "rules": {
                "t0_auto": {
                    "enabled": True,
                    "last_run": {
                        "session_date": "2026-09-16",
                        "trade_count": 2,
                        "pnl_total": -12.5,
                        "results": [
                            {
                                "stock_code": "600183",
                                "pnl": -12.5,
                                "close": 152.6,
                                "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                                "trades": [
                                    {
                                        "side": "t0_buy",
                                        "price": 150.1,
                                        "shares": 100,
                                        "at": "2026-09-16 09:45:00",
                                    }
                                ],
                            }
                        ],
                    },
                }
            }
        }
        self.assertTrue(
            _patch_t0_last_run_scans(
                paper,
                [
                    {
                        "stock_code": "600183",
                        "close": 154.48,
                        "close_band_scan": [{"hm": "09:45", "y_oc": -0.62}],
                    }
                ],
                session_date="2026-09-16",
            )
        )
        row = paper["rules"]["t0_auto"]["last_run"]["results"][0]
        self.assertEqual(row["close_band_scan"][0]["y_oc"], -0.62)
        self.assertEqual(row["close"], 154.48)
        self.assertEqual(row["pnl"], -12.5)
        self.assertEqual(row["trades"][0]["price"], 150.1)

    def test_sync_preview_scans_to_ledgers_patches_last_run_and_snapshot(self):
        """午夜后日历滚日时，仍按成交日把预演 ŷ 写回 last_run / 盘中快照。"""
        import tempfile
        from unittest.mock import patch

        from core.paper import load_paper, save_paper
        from services.paper_trades import _sync_preview_scans_to_ledgers

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000,
                "holdings": [],
                "trades": [],
                "operation_log": [],
                "rules": {
                    "t0_auto": {
                        "enabled": True,
                        "last_run": {
                            "session_date": "2026-09-16",
                            "trade_count": 2,
                            "pnl_total": -12.5,
                            "results": [
                                {
                                    "stock_code": "600183",
                                    "date": "2026-09-16",
                                    "close": 152.6,
                                    "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                                    "scores": {"y_oc": -0.73},
                                    "trades": [
                                        {
                                            "side": "t0_sell",
                                            "price": 150.06,
                                            "shares": 200,
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
            save_paper(paper, path)
            state = {
                "session_date": "2026-09-16",
                "stocks": {
                    "600183": {
                        "day_snapshot": {
                            "date": "2026-09-16",
                            "close": 152.6,
                            "close_band_scan": [{"hm": "09:45", "y_oc": -0.73}],
                            "scores": {"y_oc": -0.73},
                        }
                    }
                },
            }
            saved_state = {}

            def _save_state(st):
                saved_state.clear()
                saved_state.update(st)

            result = {
                "results": [
                    {
                        "stock_code": "600183",
                        "date": "2026-09-16",
                        "close": 154.48,
                        "close_band_scan": [
                            {"hm": "09:45", "y_oc": -0.62, "y_tw": 1}
                        ],
                        "scores": {"y_oc": -0.62},
                    }
                ]
            }
            with patch(
                "core.t0.intraday.load_intraday_state", return_value=state
            ), patch(
                "core.t0.intraday.save_intraday_state", side_effect=_save_state
            ):
                _sync_preview_scans_to_ledgers(
                    result, paper_path=path, session_date="2026-09-17"
                )
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]["results"][0]
            self.assertEqual(lr["close_band_scan"][0]["y_oc"], -0.62)
            self.assertEqual(lr["close"], 154.48)
            self.assertEqual(lr["trades"][0]["price"], 150.06)
            snap = saved_state["stocks"]["600183"]["day_snapshot"]
            self.assertEqual(snap["close_band_scan"][0]["y_oc"], -0.62)
            self.assertEqual(snap["close"], 154.48)

    def test_hydrate_last_run_from_intraday_snapshot(self):
        from unittest.mock import patch
        from services.paper_trades import _hydrate_t0_last_run_display

        lr = {
            "session_date": "2026-09-10",
            "results": [
                {
                    "stock_code": "600875",
                    "date": "2026-09-10",
                    "direction": "sell_then_buy",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                        }
                    ],
                    "pnl": 0,
                }
            ],
        }
        state = {
            "session_date": "2026-09-10",
            "stocks": {
                "600875": {
                    "day_snapshot": {
                        "stock_code": "600875",
                        "open": 25.4,
                        "close": 25.2,
                        "t0_slots_enabled": True,
                        "t0_slot_results": [
                            {
                                "id": "r1",
                                "hm": "09:35",
                                "sold_qty": 100,
                                "trades": 1,
                                "close_band": {"c_tau": 25.45, "band_r_pct": 0.95},
                            }
                        ],
                        "close_band_scan": [
                            {
                                "hm": "09:35",
                                "o": 25.5,
                                "l": 25.4,
                                "h": 25.8,
                                "c": 25.7,
                                "c_tau": 25.45,
                                "r_pct": 0.95,
                            }
                        ],
                        "forward_trace": [
                            {
                                "time": "09:35",
                                "open": 25.5,
                                "high": 25.8,
                                "low": 25.4,
                                "close": 25.7,
                                "leg1_fill": True,
                            }
                        ],
                        "trades": [
                            {
                                "side": "t0_sell",
                                "price": 25.7,
                                "shares": 100,
                                "at": "2026-09-10 09:35:00",
                                "t0_slot": "r1",
                                "t0_slot_hm": "09:35",
                            }
                        ],
                    }
                }
            },
        }
        with patch("core.t0.intraday.load_intraday_state", return_value=state):
            self.assertTrue(_hydrate_t0_last_run_display(lr))
        row = lr["results"][0]
        self.assertEqual(row["t0_slot_results"][0]["hm"], "09:35")
        self.assertEqual(row["close_band_scan"][0]["c"], 25.7)
        self.assertEqual(row["forward_trace"][0]["open"], 25.5)
        self.assertEqual(row["trades"][0]["t0_slot"], "r1")
        self.assertAlmostEqual(float(row["trades"][0]["price"]), 25.7)

    def test_stamp_book_ts_from_paper_trades(self):
        from services.paper_trades import _stamp_book_ts_from_paper

        lr = {
            "session_date": "2026-09-10",
            "results": [
                {
                    "stock_code": "600875",
                    "trades": [
                        {
                            "side": "t0_sell",
                            "price": 25.7,
                            "shares": 100,
                            "at": "2026-09-10 09:35:00",
                            "t0_slot_hm": "09:35",
                        }
                    ],
                }
            ],
        }
        paper = {
            "trades": [
                {
                    "stock_code": "600875",
                    "side": "t0_sell",
                    "price": 25.7,
                    "shares": 100,
                    "at": "2026-09-10 09:35:00",
                    "ts": "2026-09-10T10:29:15.941",
                }
            ]
        }
        self.assertTrue(_stamp_book_ts_from_paper(lr, paper))
        self.assertEqual(lr["results"][0]["trades"][0]["ts"], "2026-09-10T10:29:15.941")
        self.assertFalse(_stamp_book_ts_from_paper(lr, paper))

    def test_last_run_skipped_without_write(self):
        import tempfile
        from services.paper_trades import _write_t0_auto_last_run
        from core.paper import load_paper, save_paper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {"cash": 100000, "holdings": [], "rules": {}, "trades": [], "operation_log": []}
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": [],
                    "results": [{"stock_code": "600519", "skipped": True, "reason": "振幅不足"}],
                },
                source="paper_t0_auto",
            )
            self.assertNotIn("last_run", paper.get("rules", {}).get("t0_auto", {}))

    def test_last_run_from_flat_trades_when_results_empty(self):
        import tempfile
        from services.paper_trades import _write_t0_auto_last_run
        from core.paper import load_paper, save_paper

        legs = [
            {
                "side": "t0_sell",
                "stock_code": "600519",
                "shares": 100,
                "price": 10.2,
                "at": "2026-08-28 10:05:00",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {"cash": 100000, "holdings": [], "rules": {}, "trades": [], "operation_log": []}
            save_paper(paper, path)
            paper = load_paper(path)
            _write_t0_auto_last_run(
                paper,
                result={
                    "success": True,
                    "trades": legs,
                    "session_date": "2026-08-28",
                    "results": [],
                },
                source="paper_t0_auto",
            )
            save_paper(paper, path)
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertIsNotNone(lr.get("ts"))
            self.assertEqual(len(lr.get("results") or []), 1)
            row = lr["results"][0]
            self.assertEqual(row["stock_code"], "600519")
            self.assertEqual(len(row.get("trades") or []), 1)


class TestDeleteT0Records(unittest.TestCase):
    def test_delete_voids_roundtrip_and_restores_sellable(self):
        import tempfile
        from core.paper import load_paper, save_paper
        from core.paper.tplus1 import sellable_shares
        from services.paper_service import PaperService

        sess = "2026-08-26"
        prev = "2026-08-25"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 99989.91,
                "cost_model": "zero",
                "holdings": [
                    {
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "shares": 100,
                        "cost": 10.0,
                        "lots": [
                            {
                                "shares": 100,
                                "bought_at": f"{sess}T09:40:00",
                                "bought_date": sess,
                            }
                        ],
                        "t0": {"enabled": True, "last_date": sess},
                    }
                ],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.0,
                        "net_cash_delta": 1005.0,
                        "ts": f"{sess}T09:35:00",
                        "at": f"{sess} 09:35",
                    },
                    {
                        "side": "t0_buy",
                        "stock_code": "601600",
                        "stock_name": "中国铝业",
                        "price": 10.1,
                        "shares": 100,
                        "amount": 1010.0,
                        "fees": 5.09,
                        "net_cash_delta": -1015.09,
                        "ts": f"{sess}T09:40:00",
                        "at": f"{sess} 09:40",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": -10.09,
                            "source": "paper_t0_auto",
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "stock_name": "中国铝业",
                                    "date": sess,
                                    "direction": "sell_then_buy",
                                    "pnl": -10.09,
                                    "trades": [
                                        {
                                            "side": "t0_sell",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:35",
                                        },
                                        {
                                            "side": "t0_buy",
                                            "price": 10.1,
                                            "shares": 100,
                                            "at": "09:40",
                                        },
                                    ],
                                }
                            ],
                        },
                    }
                },
                "operation_log": [
                    {
                        "ts": f"{sess}T09:35:00",
                        "type": "sell",
                        "detail": "做T卖出 中国铝业 100股 @ 10.1",
                        "meta": {
                            "stock_code": "601600",
                            "origin": "t0",
                            "source": "paper_t0_auto",
                        },
                    },
                    {
                        "ts": f"{sess}T09:35:00",
                        "type": "t0_batch",
                        "detail": "做T自动·盘中 · 成交 1 笔",
                        "meta": {"origin": "t0", "source": "paper_t0_auto", "trade_count": 1},
                    },
                ],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 100000.0, "total_value": 101000.0},
            ), patch(
                "services.paper_trades.append_snapshot",
                return_value=None,
            ), patch(
                "core.market.calendar.prev_trading_day",
                return_value=prev,
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"), out)
            self.assertEqual(out.get("voided_legs"), 2)
            self.assertAlmostEqual(out.get("cash_delta"), 10.09, places=2)
            paper2 = load_paper(path)
            self.assertIsNone((paper2.get("rules") or {}).get("t0_auto", {}).get("last_run"))
            self.assertAlmostEqual(float(paper2.get("cash") or 0), 100000.0, places=2)
            h = next(x for x in paper2["holdings"] if x["stock_code"] == "601600")
            self.assertEqual(float(h.get("shares") or 0), 100)
            self.assertEqual(sellable_shares(h, as_of=sess), 100)
            voided = [t for t in paper2["trades"] if t.get("voided")]
            self.assertEqual(len(voided), 2)
            self.assertTrue(any(e.get("type") == "t0_void" for e in paper2.get("operation_log") or []))
            voided_logs = [
                e
                for e in (paper2.get("operation_log") or [])
                if e.get("voided") and e.get("type") in ("sell", "t0_batch")
            ]
            self.assertEqual(len(voided_logs), 2)

    def test_delete_only_last_run_keeps_other_code(self):
        import tempfile
        from core.paper import load_paper, save_paper
        from services.paper_service import PaperService

        sess = "2026-08-26"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            paper = {
                "cash": 100000.0,
                "holdings": [],
                "trades": [
                    {
                        "side": "t0_sell",
                        "stock_code": "601600",
                        "shares": 100,
                        "price": 10.0,
                        "net_cash_delta": 1000.0,
                        "ts": f"{sess}T09:35:00",
                    },
                    {
                        "side": "t0_sell",
                        "stock_code": "002352",
                        "shares": 100,
                        "price": 20.0,
                        "net_cash_delta": 2000.0,
                        "ts": f"{sess}T10:00:00",
                    },
                ],
                "rules": {
                    "t0_auto": {
                        "enabled": False,
                        "schedule": "after_close",
                        "last_run": {
                            "ts": 1.0,
                            "ok": True,
                            "trade_count": 2,
                            "pnl_total": 0,
                            "session_date": sess,
                            "results": [
                                {
                                    "stock_code": "601600",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 10}],
                                },
                                {
                                    "stock_code": "002352",
                                    "date": sess,
                                    "trades": [{"side": "t0_sell", "shares": 100, "price": 20}],
                                },
                            ],
                        },
                    }
                },
                "operation_log": [],
                "snapshots": [],
            }
            save_paper(paper, path)
            svc = PaperService(path)
            with patch(
                "services.paper_trades.mark_to_market",
                return_value={"cash": 99000.0, "total_value": 99000.0},
            ), patch("services.paper_trades.append_snapshot", return_value=None), patch(
                "core.market.calendar.prev_trading_day",
                return_value="2026-08-25",
            ):
                out = svc.delete_t0_records(stock_codes=["601600"], reverse_ledger=True)
            self.assertTrue(out.get("ok"))
            lr = load_paper(path)["rules"]["t0_auto"]["last_run"]
            self.assertEqual(len(lr["results"]), 1)
            self.assertEqual(lr["results"][0]["stock_code"], "002352")
            paper2 = load_paper(path)
            self.assertTrue(
                any(
                    t.get("stock_code") == "601600" and t.get("voided")
                    for t in paper2["trades"]
                )
            )
            self.assertFalse(
                any(
                    t.get("stock_code") == "002352" and t.get("voided")
                    for t in paper2["trades"]
                )
            )


class TestClearT0Intraday(unittest.TestCase):
    def test_clear_skips_legs_unless_force(self):
        import json
        import tempfile
        from core.t0 import intraday as intraday_mod
        from services.paper_service import PaperService

        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "t0_intraday_state.json")
            paper_path = os.path.join(tmp, "paper.json")
            state = {
                "session_date": "2026-08-26",
                "stocks": {
                    "000651": {"phase": "skipped", "legs_written": 0, "score_locked": True},
                    "002352": {"phase": "after_leg1", "legs_written": 1},
                },
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
            svc = PaperService(paper_path)
            with patch.object(intraday_mod, "_state_path", return_value=state_path), patch(
                "core.t0.intraday.build_intraday_desk_status",
                return_value={"rows": [], "universe_count": 0},
            ):
                out = svc.clear_t0_intraday(clear_all=True, force=False)
                self.assertEqual(out["cleared"], 1)
                self.assertEqual(out["stock_codes"], ["000651"])
                self.assertEqual(out["skipped_with_legs"], ["002352"])
                left = json.load(open(state_path, encoding="utf-8"))
                self.assertIn("002352", left["stocks"])
                self.assertNotIn("000651", left["stocks"])

                out2 = svc.clear_t0_intraday(stock_codes=["002352"], force=True)
                self.assertEqual(out2["cleared"], 1)
                left2 = json.load(open(state_path, encoding="utf-8"))
                self.assertEqual(left2.get("stocks") or {}, {})


class TestT0PreviewMinuteRefresh(unittest.TestCase):
    """手动预演：会话日 5m 停在午前时须 use_cache=False 补拉。"""

    def test_dry_run_fetches_when_session_day_stops_at_1000(self):
        import json
        import tempfile
        from datetime import datetime
        from services.paper_trades import PaperTradesMixin

        day = "2026-09-01"
        paper = {
            "strategy_id": "short_conservative",
            "cash": 2_000_000,
            "holdings": [{"stock_code": "002415", "shares": 500, "cost": 35.0}],
            "trades": [],
            "snapshots": [],
            "operation_log": [],
            "rules": {},
            "cost_model": "zero",
            "updated_at": f"{day}T12:00:00",
        }
        stale = [
            {
                "date": day,
                "datetime": f"{day} 09:35:00",
                "open": 35.5,
                "high": 35.6,
                "low": 35.4,
                "close": 35.5,
            },
            {
                "date": day,
                "datetime": f"{day} 10:00:00",
                "open": 35.5,
                "high": 35.9,
                "low": 35.5,
                "close": 35.8,
            },
        ]
        fresh = list(stale) + [
            {
                "date": day,
                "datetime": f"{day} 14:55:00",
                "open": 36.0,
                "high": 36.1,
                "low": 35.9,
                "close": 36.0,
            }
        ]
        daily = [
            {
                "date": "2026-08-29",
                "open": 35.0,
                "high": 35.5,
                "low": 34.8,
                "close": 35.2,
            },
            {
                "date": day,
                "open": 35.5,
                "high": 36.2,
                "low": 35.3,
                "close": 36.0,
                "prev_close": 35.2,
            },
        ]
        captured = {}

        def _sim_holdings(*args, **kwargs):
            captured["minute_bars_by_code"] = kwargs.get("minute_bars_by_code")
            return {
                "success": True,
                "trades": [],
                "pnl_total": 0.0,
                "results": [],
                "note": "ok",
            }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _T(PaperTradesMixin):
                pass

            svc = _T()
            svc.path = path
            with patch(
                "core.market.calendar.resolve_session_date", return_value=day
            ), patch(
                "core.market.calendar.is_trading_day", return_value=True
            ), patch(
                "core.signal.session_pit.shanghai_now",
                return_value=datetime(2026, 9, 1, 17, 30, 0),
            ), patch(
                "core.signal.session_pit.asof_session_final", return_value=True
            ), patch(
                "core.t0.intraday.session_in_market", return_value=(False, "closed")
            ), patch(
                "core.data.facade.bars_and_source", return_value=(daily, "test")
            ), patch(
                "core.execution.resolve_effective_execution",
                return_value={
                    "t0": {"enabled": True, "minute_period": "5"},
                    "coupling": {"t0_vs_stance": "independent"},
                    "channel": "paper",
                },
            ), patch(
                "core.execution.strip_execution_meta",
                side_effect=lambda t0: dict(t0 or {}),
            ), patch(
                "core.execution.execution_public_view",
                return_value={"ok": True, "t0": {}},
            ), patch(
                "adapters.market.minute_history.load_minute_cache",
                return_value=(stale, {"from_cache": True}),
            ), patch(
                "core.ports.market.fetch_minute_bars",
                return_value=(fresh, {"ok": True, "data_source": "test"}),
            ) as fetch, patch(
                "core.ports.market.resolve_market_code",
                return_value=("CN", "002415"),
            ), patch(
                "core.t0.rules.simulate_t0_on_holdings", side_effect=_sim_holdings
            ):
                out = svc.simulate_t0(dry_run=True)
            self.assertTrue(out.get("ok"), out)
            fetch.assert_called()
            self.assertFalse(fetch.call_args.kwargs.get("use_cache"))
            mins = (captured.get("minute_bars_by_code") or {}).get("002415") or []
            self.assertGreaterEqual(len(mins), 3)
            self.assertTrue(str(mins[-1].get("datetime") or "").startswith(f"{day} 14:55"))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
