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


def _mins_hl(date=None, o=100, high=105, low=98, close=101, bar=None):
    """反T：固定前缀 4 根（单测默认），后半 2 根下跌确认 → 第 4 根卖；其后回落买回。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    mid = (o + high) / 2.0
    return _mins(
        str(date),
        [
            (935, o, o + 0.2, o - 0.1, o + 0.1),  # 阳
            (940, o + 0.1, mid, o, mid - 0.05),  # 冲半高
            (945, mid - 0.05, mid, mid - 0.4, mid - 0.35),  # 阴 · 后半
            (950, mid - 0.35, high, mid - 0.4, mid - 0.5),  # 阴 · 后半 → 确认卖
            (1400, mid - 0.5, mid - 0.4, low, close),  # 买回
        ],
    )


def _mins_lh(date=None, o=100, high=105, low=96, close=101, bar=None):
    """正T：固定前缀 4 根（单测默认），后半 2 根上涨确认 → 第 4 根买；其后冲高卖旧。"""
    if isinstance(bar, dict):
        date = bar.get("date") or date
        o = float(bar.get("open") or o)
        high = float(bar.get("high") or high)
        low = float(bar.get("low") or low)
        close = float(bar.get("close") or close)
    mid = (o + low) / 2.0
    return _mins(
        str(date),
        [
            (935, o, o + 0.2, o - 0.2, o - 0.1),  # 阴
            (940, o - 0.1, o, low, mid),  # 可阴可阳
            (945, mid, mid + 0.3, mid - 0.1, mid + 0.2),  # 阳 · 后半
            (950, mid + 0.2, mid + 0.5, mid, mid + 0.4),  # 阳 · 后半 → 确认买
            (1400, mid + 0.4, high, mid + 0.3, close),  # 冲高卖旧
        ],
    )


def _rules(**kwargs):
    """单测默认 first_touch + 关 ATR；关中点追价以免干扰路径用例。"""
    base = {
        "path_mode": "first_touch",
        "use_atr": False,
        "t0_pm_degrade": "",
        "y_block_tau_nowcast_sign": False,
        # 路径用例默认关止损，避免夹具回踩/冲高误触；止损单测显式打开
        "t0_stop_pct_buy_then_sell": 0,
        "t0_stop_pct_sell_then_buy": 0,
        # 路径用例沿用较低门槛；与纸面策略默认（1% / 0.5%）解耦
        "min_range_pct_sell_then_buy": 0.1,
        "min_range_pct_buy_then_sell": 0.1,
        # 正/反T单测夹具短：侧向固定前缀 4 根
        "y_path_abandon_bars": 12,
        "y_path_abandon_bars_buy_then_sell": 4,
        "y_path_abandon_bars_sell_then_buy": 4,
        "y_prefix_upbar_ratio_buy_then_sell": 0.5,
        "y_prefix_downbar_ratio_sell_then_buy": 0.5,
    }
    base.update(kwargs)
    base["path_mode"] = "first_touch"
    # 单测常只写共用触发%；同步到仍生效的第二腿侧向键
    if "sell_trigger_pct" in kwargs:
        if "sell_trigger_pct_buy_then_sell" not in kwargs:
            base["sell_trigger_pct_buy_then_sell"] = kwargs["sell_trigger_pct"]
    if "buy_trigger_pct" in kwargs:
        if "buy_trigger_pct_sell_then_buy" not in kwargs:
            base["buy_trigger_pct_sell_then_buy"] = kwargs["buy_trigger_pct"]
    if "min_range_pct" in kwargs:
        if "min_range_pct_sell_then_buy" not in kwargs:
            base["min_range_pct_sell_then_buy"] = kwargs["min_range_pct"]
        if "min_range_pct_buy_then_sell" not in kwargs:
            base["min_range_pct_buy_then_sell"] = kwargs["min_range_pct"]
    return base


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

    def test_resolve_path_abandon_bars_side_keys(self):
        from core.t0.config import resolve_path_abandon_bars

        cfg = {
            "y_path_abandon_bars": 12,
            "y_path_abandon_bars_sell_then_buy": 8,
            "y_path_abandon_bars_buy_then_sell": 20,
        }
        self.assertEqual(resolve_path_abandon_bars(cfg, "sell_then_buy"), 8)
        self.assertEqual(resolve_path_abandon_bars(cfg, "buy_then_sell"), 20)
        self.assertEqual(resolve_path_abandon_bars(cfg, None), 12)
        self.assertEqual(resolve_path_abandon_bars({}, None), 6)
        self.assertEqual(resolve_path_abandon_bars({"y_path_abandon_bars": ""}, "buy_then_sell"), 6)

    def test_must_cover_legacy_false_overrides_reverse_default(self):
        """legacy must_cover_same_day=False 须落到正T侧，不被默认 True 吞掉。"""
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"must_cover_same_day": False})
        self.assertFalse(cfg["must_cover_same_day_buy_then_sell"])
        self.assertFalse(cfg["must_cover_same_day"])
        # 侧向键显式优先于 legacy
        cfg2 = load_t0_rules(
            {"must_cover_same_day": False, "must_cover_same_day_buy_then_sell": True}
        )
        self.assertTrue(cfg2["must_cover_same_day_buy_then_sell"])
        self.assertTrue(cfg2["must_cover_same_day"])

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
            sell_trig=5.0,
            buy_trig=1.5,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "",
                "y_prefix_segment_enabled": False,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=2.0,
            session_bars=mins,
            session_bar=bar,
            defer_eod=True,
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
            sell_trig=1.0,
            buy_trig=5.0,
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
                    "y_path_abandon_bars": 2,
                    "y_prefix_segment_enabled": False,
                    "sell_trigger_pct": 50.0,
                    "buy_trigger_pct": 1.0,
                    "t0_pm_degrade": "",
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
        self.assertEqual(d["sell_trigger_pct"], 3.0)
        self.assertEqual(d["buy_trigger_pct"], 1.0)
        self.assertTrue(d["must_cover_same_day"])
        self.assertFalse(d["must_cover_same_day_sell_then_buy"])
        self.assertTrue(d["must_cover_same_day_buy_then_sell"])
        self.assertEqual(d["t0_pm_degrade_sell_then_buy"], "13:00")
        self.assertEqual(d["t0_pm_degrade_buy_then_sell"], "14:00")
        self.assertEqual(d["t0_pm_degrade"], "14:00")
        self.assertEqual(d["t0_pm_chase_interval_min_sell_then_buy"], 10)
        self.assertEqual(d["t0_pm_chase_interval_min_buy_then_sell"], 10)
        self.assertAlmostEqual(d["t0_stop_pct_buy_then_sell"], 1.2)
        self.assertAlmostEqual(d["t0_stop_pct_sell_then_buy"], 1.2)
        self.assertEqual(d["t0_stop_arm_bars"], 2)
        self.assertTrue(d["t0_stop_on_close"])
        self.assertFalse(d["use_atr"])
        self.assertEqual(d["min_range_pct"], 0.2)
        self.assertEqual(d["min_range_pct_sell_then_buy"], 0.2)
        self.assertEqual(d["min_range_pct_buy_then_sell"], 0.2)
        self.assertEqual(d["buy_trigger_pct_sell_then_buy"], 1.0)
        self.assertEqual(d["sell_trigger_pct_buy_then_sell"], 3.0)
        self.assertNotIn("buy_trigger_pct_buy_then_sell", d)
        self.assertNotIn("sell_trigger_pct_sell_then_buy", d)
        self.assertNotIn("sell_trigger_pct_long", d)
        self.assertNotIn("buy_trigger_pct_reverse", d)
        self.assertEqual(d["y_path_enter"], 0.01)
        self.assertFalse(d["y_nowcast_oc_gate"])
        self.assertEqual(d["y_nc_enter"], 0.01)
        self.assertEqual(d["y_nc_strong"], 0.5)
        self.assertEqual(d["y_nowcast_enter"], 0.5)
        self.assertEqual(d["y_path_abandon_bars"], 6)
        self.assertTrue(d["y_prefix_segment_enabled"])
        self.assertTrue(d["y_prefix_segment_enabled_sell_then_buy"])
        self.assertTrue(d["y_prefix_segment_enabled_buy_then_sell"])
        self.assertEqual(d["y_prefix_upbar_ratio_buy_then_sell"], 0.2)
        self.assertEqual(d["y_prefix_downbar_ratio_sell_then_buy"], 0.2)
        self.assertEqual(d["y_tau_entry_price_mult"], 0.0)
        self.assertTrue(d["y_prefix_vs_path_skip"])
        self.assertEqual(d["y_trade_enter"], 0.01)
        self.assertEqual(d["y_trade_strong"], 0.5)
        self.assertEqual(d["y_eod_prior"], 0.01)
        self.assertEqual(d["y_eod_strong"], 0.5)
        self.assertEqual(d["y_on_allow"], 0.01)
        self.assertEqual(d["y_trade_floor"], 0.01)
        self.assertEqual(d["y_tau_map"], "trend")
        self.assertTrue(d["enabled"])

    def test_sell_and_cover_same_day(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                direction="sell_then_buy",
                ref="cost",
                min_range_pct=1.0,
            ),
            stock_code="600519",
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
            sell_trigger_pct=2.0,
            buy_trigger_pct=1.5,
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

    def test_low_range_skipped(self):
        bar = _bar("2026-01-10", 100, 100.5, 99.5, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="sell_then_buy",
                min_range_pct=1.5,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["sold_qty"], 0)
        self.assertIn("振幅", out.get("reason") or "")

    def test_forward_range_gate_waits_for_prefix(self):
        """前缀振幅未达标时不跑触达；齐窗后扩幅+阴线确认才卖。"""
        bar = _bar("2026-01-10", 100, 103, 99.5, 100)
        mins = _mins(
            "2026-01-10",
            [
                (935, 100, 100.05, 99.98, 100.02),  # 窄幅
                (940, 100.02, 100.06, 99.99, 100.01),
                (945, 100.01, 100.05, 99.50, 99.70),  # 阴 · 拉开振幅
                (950, 99.70, 103.0, 99.60, 99.65),  # 阴 · 齐窗触卖
                (1400, 99.65, 100.5, 99.5, 100.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="sell_then_buy",
                min_range_pct=2.5,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("range_mode"), "fixed_prefix")
        self.assertGreaterEqual(int(out.get("prefix_bars") or 0), 4)
        self.assertGreater(out.get("sold_qty") or 0, 0)

    def test_no_trigger_when_range_ok_but_levels_miss(self):
        """振幅够但后半阴线占比不足 → 固定前缀放弃，不卖。"""
        bar = _bar("2026-01-10", 100, 105, 98.5, 100)
        # 后半 2 根均为阳 → 下跌占比 0% < 50%
        mins = _mins(
            "2026-01-10",
            [
                (935, 100, 100.2, 99.8, 99.9),
                (940, 99.9, 102, 99.5, 101.5),
                (945, 101.5, 103, 101.0, 102.5),  # 阳
                (950, 102.5, 105, 102.0, 104.0),  # 阳
                (1400, 104, 104.5, 98.5, 100.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                sell_trigger_pct=3.0,
                buy_trigger_pct=1.5,
                direction="sell_then_buy",
                min_range_pct=1.0,
            ),
            minute_bars=mins,
        )
        self.assertTrue(out["success"])
        self.assertEqual(out.get("sold_qty") or 0, 0)
        self.assertFalse(out.get("trades") or [])
        self.assertTrue(out.get("skipped") or out.get("path_abandon"))
        reason = str(out.get("reason") or "")
        self.assertTrue(
            any(x in reason for x in ("固定前缀", "下跌", "放弃反T")),
            reason,
        )
    def test_tplus1_sellable_cap(self):
        bar = _bar("2026-01-10", 100, 110, 90, 100)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            sellable_shares=100,
            rules=_rules(
                t0_ratio=1.0,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
                direction="sell_then_buy",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                direction="sell_then_buy",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
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
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            stock_code="600519",
            minute_bars=_mins_lh(bar=bar))
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
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("bought_qty") or 0, 0)
        reason = out.get("reason") or ""
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
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["bought_qty"], 100)
        self.assertEqual(out["sold_back_qty"], 100)
        self.assertEqual(out["shares_end"], 1000)

    def test_auto_gap_down_picks_buy_then_sell(self):
        # 默认 ref=open 时，auto 必须看昨收跳空，否则永远反 T
        bar = _bar("2026-01-10", 98, 103, 96, 100)
        bar["prev_close"] = 100
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="auto",
                ref="open",
                auto_weak_pct=0.5,
                auto_strong_pct=0.5,
                t0_ratio=0.4,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
                fill_mode="trigger",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["direction_used"], "buy_then_sell")

    def test_auto_with_ref_open_no_prev_close_defaults_long(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="auto",
                ref="open",
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertEqual(out.get("direction_used") or "sell_then_buy", "sell_then_buy")


    def test_signal_strong_gap_picks_long(self):
        # 高开 + 昨收偏强 → 反T
        hist = [
            _bar("d1", 98, 100, 97, 99),
            _bar("d2", 99, 101, 98, 100),
            _bar("d3", 100, 104, 99, 103),  # 昨收近高
        ]
        bar = _bar("d4", 105, 108, 102, 104)  # 高开约 +1.9%
        bar["prev_close"] = 103
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "sell_then_buy")
        self.assertIsNotNone(out.get("direction_score"))

    def test_signal_weak_gap_picks_reverse(self):
        hist = [
            _bar("d1", 102, 103, 100, 101),
            _bar("d2", 101, 102, 99, 100),
            _bar("d3", 100, 101, 96, 97),  # 昨收偏弱
        ]
        bar = _bar("d4", 95, 99, 93, 96)  # 低开
        bar["prev_close"] = 97
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.25,
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "buy_then_sell")

    def test_signal_low_confidence_skips(self):
        hist = [
            _bar("d1", 100, 101, 99, 100),
            _bar("d2", 100, 101, 99, 100),
            _bar("d3", 100, 101, 99, 100),
        ]
        bar = _bar("d4", 100.1, 103, 98, 101)  # 几乎平开
        bar["prev_close"] = 100
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            hist_bars=hist,
            rules=_rules(
                direction="signal",
                path_mode="first_touch",
                dir_enter=0.35,
                min_range_pct=1.0,
            ),
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out["success"])
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("signal_skip"))
        self.assertIn("低置信", out.get("reason") or "")


    def test_exposure_when_uncovered(self):
        bar = _bar("2026-01-10", 100, 105, 103, 104.5)
        # 确认根 close≠收盘：卖@104 未回补 → 敞口按收盘计价
        mins = _mins(
            "2026-01-10",
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 103, 100.0, 102.5),
                (945, 102.5, 103.0, 101.8, 102.0),  # 阴
                (950, 105.0, 105.0, 103.5, 104.0),  # 阴 · 卖@104
                (1400, 104, 104.8, 103.2, 104.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=5.0,
                direction="sell_then_buy",
                fill_mode="trigger",
                must_cover_same_day=False,
                min_range_pct=0.5,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out["sold_qty"], 400)
        self.assertEqual(out["covered_qty"], 0)
        self.assertNotEqual(out["exposure_pnl"], 0)

    def test_backtest_runs_with_optimistic_compare(self):
        bars = [
            _bar(f"d{i}", 100 + i * 0.1, 110, 90, 100 + i * 0.05) for i in range(20)
        ]
        mins = {b["date"]: _mins_hl(bar=b) for b in bars}
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            rules=_rules(direction="sell_then_buy", min_range_pct=1.0),
            compare_optimistic=True,
            minute_by_date=mins,
            require_minute=True,
        )
        self.assertTrue(report["success"])
        self.assertGreaterEqual(report["t0_trade_days"], 1)
        self.assertIn("t0_pnl_total", report)
        self.assertIn("optimistic_compare", report)
        self.assertIn("exposure_pnl_total", report)
        self.assertIn("cover_rate_pct", report)
        self.assertIn("participate_rate_pct", report)
        self.assertIn("avg_pnl_per_trade_day", report)
        self.assertIn("t0_pnl_with_exposure", report)
        self.assertEqual(
            report["t0_pnl_with_exposure"],
            round(report["t0_pnl_total"] + report["exposure_pnl_total"], 2),
        )
        self.assertIn("delta_pnl_ratio_pct", report["optimistic_compare"])
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
                compare_optimistic=False,
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
                compare_optimistic=False,
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

    def test_minute_first_touch_avoids_false_cover(self):
        """先砸后拉：反T 固定前缀确认后尾盘卖；不应虚用早盘低点回补。"""
        bar = _bar("2024-01-02", 100, 105, 97, 104)
        minutes = _mins(
            "2024-01-02",
            [
                (935, 100, 100.2, 97.0, 97.5),  # 早盘砸
                (940, 97.5, 99.0, 97.2, 98.0),
                (945, 98.0, 99.0, 97.5, 97.8),  # 阴
                (950, 97.8, 105.0, 97.5, 97.6),  # 阴 · 触卖确认
                (1400, 104.0, 105.0, 103.5, 104.0),  # 高位无买回空间
            ],
        )
        synth = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                direction="sell_then_buy",
                path_mode="first_touch",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
            minute_bars=_mins_hl(bar=bar),
        )
        minute = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=minutes,
            rules=_rules(
                direction="sell_then_buy",
                path_mode="first_touch",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=False,
            ),
        )
        self.assertGreater(int(synth.get("covered_qty") or 0), 0)
        self.assertGreater(int(minute.get("sold_qty") or 0), 0)
        self.assertEqual(int(minute.get("covered_qty") or 0), 0)
        self.assertEqual(minute.get("path_mode"), "first_touch")

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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
            ),
        )
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertGreater(int(out.get("sold_qty") or 0), 0)
        self.assertEqual(int(out.get("covered_qty") or 0), int(out.get("sold_qty") or 0))
        self.assertGreater(float(out.get("pnl") or 0), 0)
        trades = out.get("trades") or []
        self.assertTrue(all(t.get("at") for t in trades), trades)
        self.assertEqual(trades[0].get("leg_kind"), "trigger")
        self.assertEqual(trades[1].get("leg_kind"), "trigger")
        self.assertIn("09:50", str(trades[0].get("at") or ""))
        self.assertIn("14:00", str(trades[1].get("at") or ""))

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
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
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
        mins = _mins(
            d,
            [
                (935, 34.15, 34.20, 34.00, 34.05),
                (940, 34.05, 34.08, 33.80, 33.90),
                (945, 33.90, 34.05, 33.85, 34.00),  # 阳
                (1040, 34.00, 34.15, 33.95, 34.11),  # 阳 → 买；末根午前
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
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="",
                t0_ratio=1.0,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
            defer_eod=True,
        )
        self.assertTrue(out.get("success"), out.get("reason"))
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        self.assertEqual(int(out.get("sold_back_qty") or 0), 0)
        self.assertEqual(out.get("exit_reason"), "defer_eod_pending")
        sells = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("sell")]
        self.assertEqual(len(sells), 0)

    def test_buy_then_sell_truncated_minutes_eod_uses_daily_close(self):
        """5m 缓存在午前截断时，回测仍按日线收盘强制卖旧仓。"""
        bar = _bar("2026-08-27", 34.15, 34.59, 33.85, 34.52)
        mins = _mins(
            "2026-08-27",
            [
                (935, 34.15, 34.20, 34.00, 34.05),
                (940, 34.05, 34.08, 33.80, 33.90),
                (945, 33.90, 34.05, 33.85, 34.00),
                (1040, 34.00, 34.15, 33.95, 34.11),
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
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="",
                t0_ratio=1.0,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
            defer_eod=False,
        )
        self.assertTrue(out.get("success"), out)
        self.assertGreater(int(out.get("bought_qty") or 0), 0)
        eod = [
            t
            for t in (out.get("trades") or [])
            if t.get("leg_kind") == "eod_cover"
        ]
        self.assertEqual(len(eod), 1, out)
        self.assertAlmostEqual(float(eod[0].get("price") or 0), 34.52, places=2)

    def test_intraday_setup_eod_uses_daily_close_not_minute(self):
        """盘中 force_session_close：eod 标记价用日线收盘，不用末根 5m close。"""
        from core.t0.intraday import _intraday_setup

        d = "2026-08-27"
        bar = _bar(d, 34.15, 34.59, 33.85, 34.52)
        mins = _mins(
            d,
            [
                (935, 34.15, 34.20, 34.00, 34.05),
                (940, 34.05, 34.08, 33.80, 33.90),
                (945, 33.90, 34.05, 33.85, 34.00),  # 阳
                (950, 34.00, 34.15, 33.95, 34.10),  # 阳 → 买
                (1455, 34.00, 34.11, 33.95, 34.11),  # 末根 close≠日线
            ],
        )
        holding = {"shares": 10000, "cost": 34.15, "stock_name": "中国船舶"}
        cfg = load_t0_rules(
            {
                "direction": "buy_then_sell",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 5.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day": True,
                "t0_pm_degrade": "",
                "y_path_abandon_bars_buy_then_sell": 4,
                "y_prefix_upbar_ratio_buy_then_sell": 0.5,
                    }
        )
        out = _intraday_setup(
            code="600150",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=10000,
            cash=500000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            force_session_close=True,
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertEqual(day.get("exit_reason"), "eod_cover", day)
        sells = [t for t in (day.get("trades") or []) if str(t.get("side", "")).endswith("sell")]
        eod = [t for t in sells if t.get("leg_kind") == "eod_cover"]
        self.assertEqual(len(eod), 1, day)
        self.assertAlmostEqual(float(eod[0]["price"]), 34.52, places=2)

    def test_sell_then_buy_abandons_cover_when_buyback_misses(self):
        """反T：卖出后买不回 → 放弃回补（减仓落袋），不收盘强买。"""
        bar = _bar("2024-01-03", 100, 105, 99, 100)
        minutes = _mins(
            "2024-01-03",
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 103, 100.0, 102.5),
                (945, 102.5, 103.0, 101.8, 102.0),  # 阴
                (950, 105.0, 105.0, 104.0, 104.0),  # 阴 · 卖@104
                (1500, 104, 104, 103.5, 100),  # 买回位≈102.44；低点够不着
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
                t0_pm_degrade="",
            ),
        )
        self.assertTrue(out["success"])
        self.assertGreater(out.get("sold_qty") or 0, 0)
        self.assertEqual(out.get("covered_qty") or 0, 0)
        self.assertEqual(out.get("exit_reason"), "abandon_cover")
        buys = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(buys, [])
        self.assertNotEqual(out.get("exposure_pnl"), 0)

    def test_deferred_eod_allows_afternoon_trigger_buy(self):
        """固定前缀确认后卖；第二腿应能等到下午 trigger，而非前缀末 eod_cover。"""
        d = "2024-01-03"
        bar = _bar(d, 100, 106, 99, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 103, 100.0, 102.5),
                (945, 102.5, 103.0, 101.5, 101.8),  # 阴
                (950, 101.8, 106, 101.5, 101.6),  # 阴 · 卖
                (1100, 105, 105.2, 104.8, 105),
                (1430, 101, 101.2, 98.0, 98.5),
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        trades = out.get("trades") or []
        buys = [t for t in trades if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1, trades)
        self.assertEqual(buys[0].get("leg_kind"), "trigger")
        self.assertIn("14:30", str(buys[0].get("at") or ""))
        self.assertEqual(out.get("range_mode"), "fixed_prefix")

    def test_directional_amplitude_waits_for_sell_then_buy_upside(self):
        """反T：固定前缀未齐前不卖；齐窗后半阴线确认后卖，下午买回。"""
        d = "2024-01-03"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins_hl(bar=bar)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            minute_bars=mins,
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        trades = out.get("trades") or []
        sells = [t for t in trades if str(t.get("side", "")).endswith("sell")]
        buys = [t for t in trades if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(sells), 1)
        self.assertEqual(len(buys), 1)
        self.assertIn("09:50", str(sells[0].get("at") or ""))
        self.assertEqual(buys[0].get("leg_kind"), "trigger")
        self.assertEqual(out.get("range_mode"), "fixed_prefix")

    def test_prefix_segment_long_waits_for_downbar(self):
        """反T：固定前缀齐窗且后半下跌占比达标才卖。"""
        d = "2024-01-04"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),  # 阳
                (940, 100.1, 103, 100.0, 102.5),  # 阳 · 冲高未齐窗
                (945, 102.5, 102.8, 101.5, 101.8),  # 阴
                (950, 101.8, 106, 101.5, 101.6),  # 阴 · 齐窗卖
                (1430, 101, 101.2, 94.5, 95.0),
                (1500, 95, 95.5, 94.5, 95.0),
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                must_cover_same_day=True,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        sells = [t for t in out.get("trades") or [] if str(t.get("side", "")).endswith("sell")]
        self.assertEqual(len(sells), 1)
        self.assertIn("09:50", str(sells[0].get("at") or ""))
        self.assertEqual(out.get("range_mode"), "fixed_prefix")

    def test_prefix_segment_reverse_waits_for_upbar(self):
        """正T：固定前缀齐窗且后半上涨占比达标才买。"""
        d = "2024-01-05"
        bar = _bar(d, 100, 103, 95, 101)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.5, 99.6),  # 阴
                (940, 99.6, 99.7, 97, 97.2),  # 阴
                (945, 97.2, 97.8, 97.0, 97.6),  # 阳
                (950, 97.6, 98.2, 97.4, 98.0),  # 阳 → 4/4 齐窗买
                (1400, 98.0, 103, 97.8, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
                y_path_abandon_bars=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        buys = [t for t in out.get("trades") or [] if str(t.get("side") or "") == "t0_buy"]
        self.assertGreater(out.get("bought_qty") or 0, 0, out)
        self.assertEqual(len(buys), 1, out.get("trades"))
        self.assertIn("09:50", str(buys[0].get("at") or ""))

    def test_fixed_prefix_rejects_downbar_heavy_reverse(self):
        """正T：固定前缀后半多为阴线 → 放弃。"""
        d = "2024-01-07"
        bar = _bar(d, 100, 103, 95, 101)
        mins = _mins(
            d,
            [
                (935, 100, 100, 99, 99.5),
                (940, 99.5, 99.5, 98, 98.2),
                (945, 98.2, 98.3, 97, 97.1),  # 阴
                (950, 97.1, 97.2, 96, 96.05),  # 阴 → 后半 0% 上涨
                (1400, 96.5, 103, 96, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
                y_path_abandon_bars=4,
                y_prefix_upbar_ratio_buy_then_sell=0.6,
            ),
        )
        self.assertTrue(out.get("skipped"), out)
        self.assertIn("固定前缀", str(out.get("reason") or ""))

    def test_fixed_prefix_confirm_off_allows_entry(self):
        """关闭固定前缀确认时，齐窗仅振幅即可开第一腿。"""
        d = "2024-01-08"
        bar = _bar(d, 100, 103, 95, 101)
        mins = _mins(
            d,
            [
                (935, 100, 100, 99, 99.5),
                (940, 99.5, 99.5, 98, 98.2),
                (945, 98.2, 98.3, 97, 97.1),
                (950, 97.1, 97.2, 96, 96.05),
                (1400, 96.5, 103, 96, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
                y_path_abandon_bars=4,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
        )
        self.assertTrue(out["success"], out.get("reason"))
        self.assertGreater(out.get("bought_qty") or 0, 0, out)

    def test_prefix_segment_abandon_reverse_without_upbar(self):
        """正T：固定前缀齐窗后半上涨不足 → 放弃。"""
        d = "2024-01-06"
        bar = _bar(d, 100, 101, 94, 95)
        mins = _mins(
            d,
            [
                (935, 100, 100.5, 99.5, 99.8),
                (940, 99.8, 99.9, 98.5, 98.6),
                (945, 98.6, 98.7, 97.5, 97.6),  # 阴
                (950, 97.6, 97.7, 96.5, 96.6),  # 阴
                (1400, 96.6, 97, 95, 95.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.2,
                sell_trigger_pct=1.5,
                buy_trigger_pct=0.5,
                y_path_abandon_bars=4,
                y_prefix_upbar_ratio_buy_then_sell=0.6,
            ),
        )
        self.assertTrue(out.get("skipped"), out)
        self.assertTrue(out.get("path_abandon"))
        self.assertIn("固定前缀", str(out.get("reason") or ""))

    def test_prefix_tau_entry_price_cap_reverse(self):
        """正T：确认根买价超过开盘×(1+5×|ŷ_τ|%) → 放弃。"""
        from core.t0.minute_path import tau_leg1_fill_price_ok

        gate = tau_leg1_fill_price_ok(
            fill_px=103.0,
            ref=100.0,
            y_tau=0.4,
            direction="buy_then_sell",
            mult=5.0,
        )
        self.assertFalse(gate["ok"], gate)
        self.assertAlmostEqual(gate["bound_px"], 102.0, places=4)

        d = "2024-02-11"
        bar = _bar(d, 100, 110, 95, 108)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.5, 99.6),  # 阴
                (940, 99.6, 99.7, 97, 97.2),  # 阴
                (945, 97.2, 105, 97.0, 104.0),  # 阳
                (950, 104.0, 109, 103.5, 108.0),  # 阳 · 买价过高
                (1400, 108.0, 110, 107, 108),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            scores={"y_tau": 0.4},
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.2,
                sell_trigger_pct=1.5,
                buy_trigger_pct=0.5,
                y_path_abandon_bars=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
                y_tau_entry_price_mult=5.0,
            ),
        )
        self.assertTrue(out.get("skipped"), out)
        self.assertTrue(out.get("path_abandon"), out)
        self.assertIn("τ", out.get("reason") or "")

    def test_prefix_range_vs_path_skip_when_exhausted(self):
        """前缀振幅 > |ŷ_path| → 确认根跳过。"""
        from core.t0.minute_path import prefix_range_vs_path_ok

        ok = prefix_range_vs_path_ok(range_pct=2.0, y_path=0.5)
        self.assertFalse(ok["ok"], ok)
        self.assertIn("用尽", ok.get("reason") or "")

        fine = prefix_range_vs_path_ok(range_pct=0.3, y_path=1.2)
        self.assertTrue(fine["ok"], fine)

        off = prefix_range_vs_path_ok(
            range_pct=9.0, y_path=0.1, cfg={"y_prefix_vs_path_skip": False}
        )
        self.assertTrue(off["ok"], off)
        self.assertTrue(off.get("skipped"))

        d = "2024-03-01"
        bar = _bar(d, 100, 110, 90, 105)
        # 4 根前缀，振幅约 (105-95)/100=10% >> |y_path|=0.5
        mins = _mins(
            d,
            [
                (935, 100, 102, 99, 101),
                (940, 101, 103, 100, 102),
                (945, 102, 104, 101, 103),
                (950, 103, 105, 95, 96),
                (1400, 96, 100, 95, 98),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            scores={"y_tau": 1.0, "y_path": 0.5, "y_trade": 0.5},
            rules=_rules(
                direction="buy_then_sell",
                y_use_path=False,
                y_path_abandon_bars=4,
                y_prefix_segment_enabled_buy_then_sell=True,
                y_prefix_upbar_ratio_buy_then_sell=0.0,
                y_prefix_vs_path_skip=True,
                y_tau_entry_price_mult=0,
                min_range_pct=0.1,
                min_range_pct_buy_then_sell=0.1,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out.get("skipped"), out)
        self.assertIn("ŷ_path", out.get("reason") or "")

    def test_prefix_tau_entry_price_floor_long(self):
        """反T：确认根卖价低于开盘×(1−5×|ŷ_τ|%) → 放弃（mult>0 时仍生效）。"""
        from core.t0.minute_path import tau_leg1_fill_price_ok

        gate = tau_leg1_fill_price_ok(
            fill_px=96.0,
            ref=100.0,
            y_tau=-0.4,
            direction="sell_then_buy",
            mult=5.0,
        )
        self.assertFalse(gate["ok"], gate)
        self.assertAlmostEqual(gate["bound_px"], 98.0, places=4)

        d = "2024-02-12"
        bar = _bar(d, 100, 105, 90, 92)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.5, 100.1),  # 阳
                (940, 100.1, 102, 100, 101.5),  # 阳
                (945, 101.5, 101.6, 96, 96.5),  # 阴
                (950, 96.5, 96.8, 95, 96.0),  # 阴 · 卖价过低
                (1400, 96.0, 96.5, 90, 92),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            scores={"y_tau": -0.4},
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=0.2,
                sell_trigger_pct=1.0,
                buy_trigger_pct=2.0,
                y_path_abandon_bars=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
                y_tau_entry_price_mult=5.0,
                y_prefix_vs_path_skip=False,
                must_cover_same_day=True,
            ),
        )
        self.assertTrue(out.get("skipped"), out)
        self.assertTrue(out.get("path_abandon"), out)
        self.assertIn("τ", out.get("reason") or "")

    def test_segment_confirm_bar_is_leg1_long(self):
        """反T：固定前缀齐窗后半下跌确认根卖出；之后再冲高不得当第一腿。"""
        d = "2024-02-01"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),  # 阳
                (940, 100.1, 103, 100.0, 102.8),  # 阳
                (945, 102.8, 103.0, 101.5, 101.8),  # 阴
                (950, 101.8, 102.5, 101.0, 101.2),  # 阴 → 齐窗卖@close
                (1000, 101.2, 106.0, 101.0, 105.0),  # 再冲高不得第一腿
                (1430, 101.0, 101.1, 94.0, 95.0),
                (1500, 95.0, 95.5, 94.0, 94.5),
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
                min_range_pct=0.5,
                sell_trigger_pct=1.0,
                buy_trigger_pct=5.0,
                must_cover_same_day=True,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        sells = [t for t in out.get("trades") or [] if str(t.get("side", "")).endswith("sell")]
        self.assertEqual(len(sells), 1, out)
        self.assertIn("09:50", str(sells[0].get("at") or ""), out)
        self.assertAlmostEqual(float(sells[0].get("price") or 0), 101.2, places=2)

    def test_abandon_stops_after_first_entry_ready(self):
        """首次固定前缀确认根即成交；齐窗后不再烧 abandon。"""
        d = "2024-02-05"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 103, 100.0, 102.8),
                (945, 102.8, 103.0, 101.5, 101.8),  # 阴
                (950, 101.8, 102.5, 101.0, 101.2),  # 阴 → 确认卖
                (1000, 101.2, 104.0, 101.0, 103.5),
                (1430, 101.0, 101.1, 94.0, 95.0),
                (1500, 95.0, 95.5, 94.0, 94.5),
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
                min_range_pct=0.5,
                sell_trigger_pct=1.0,
                buy_trigger_pct=5.0,
                must_cover_same_day=True,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        sells = [t for t in out.get("trades") or [] if str(t.get("side", "")).endswith("sell")]
        self.assertEqual(len(sells), 1, out)
        self.assertIn("09:50", str(sells[0].get("at") or ""), out)
        ft = out.get("forward_trace") or []
        fills = [r for r in ft if r.get("leg1_fill")]
        self.assertEqual(len(fills), 1)
        self.assertEqual(str(fills[0].get("time") or "")[:5], "09:50")
        self.assertFalse(out.get("path_abandon"), out)

    def test_segment_confirm_bar_is_leg1_reverse(self):
        """正T：固定前缀齐窗后半上涨确认根买入；之后下探不得当第一腿。"""
        d = "2024-02-06"
        bar = _bar(d, 100, 103, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 99.9),  # 阴
                (940, 99.9, 100.0, 97.0, 97.2),  # 阴
                (945, 97.2, 97.8, 97.0, 97.6),  # 阳
                (950, 97.6, 98.2, 97.4, 98.0),  # 阳 → 确认买
                (955, 98.0, 98.1, 96.5, 96.7),  # 再下探不得第一腿
                (1400, 97.0, 103, 97.0, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.5,
                sell_trigger_pct=1.5,
                buy_trigger_pct=3.0,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in out.get("trades") or [] if str(t.get("side") or "") == "t0_buy"]
        self.assertEqual(len(buys), 1, out)
        self.assertIn("09:50", str(buys[0].get("at") or ""), out)
        self.assertAlmostEqual(float(buys[0].get("price") or 0), 98.0, places=2)

    def test_pm_degrade_excludes_endpoint_bar(self):
        """P1-3：t0_pm_degrade=13:00 时 13:00 根仍可开正T（端点不计禁新开）。"""
        d = "2024-02-02"
        bar = _bar(d, 100, 103, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.1, 99.9, 99.8),
                (940, 99.8, 99.9, 97.0, 97.1),
                (945, 97.1, 97.5, 97.0, 97.4),  # 阳
                (1300, 97.4, 98.5, 97.2, 98.2),  # 阳 · 齐窗=确认根=端点
                (1400, 98.0, 103, 97.0, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.5,
                sell_trigger_pct=1.5,
                buy_trigger_pct=3.0,
                t0_pm_degrade="13:00",
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in out.get("trades") or [] if str(t.get("side") or "") == "t0_buy"]
        self.assertEqual(len(buys), 1, out)
        self.assertIn("13:00", str(buys[0].get("at") or ""), out)
        self.assertAlmostEqual(float(buys[0].get("price") or 0), 98.2, places=2)

    def test_leg2_trigger_uses_fill_not_ref(self):
        """第二腿相对 leg1 成交价：反T 买回 = sold×(1−buy%)。"""
        d = "2024-02-03"
        bar = _bar(d, 100, 106, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),  # 阳
                (940, 100.1, 103, 100.0, 102.5),  # 阳
                (945, 102.5, 103.0, 101.8, 102.0),  # 阴
                # 阴 · 齐窗卖；fill@close=104.5（trigger 用 close）
                (950, 105.0, 106.0, 104.0, 104.5),
                (1400, 104, 104, 95.5, 96.0),  # lo 到 sold×0.95≈99.275
                (1500, 95, 95.5, 94.5, 95.0),
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
                min_range_pct=0.5,
                sell_trigger_pct=1.0,
                buy_trigger_pct=5.0,
                must_cover_same_day=False,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in out.get("trades") or [] if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1, out)
        # sold@104.5 → buy_level=99.275（若用 ref 则 95）
        self.assertAlmostEqual(float(buys[0].get("price") or 0), 99.275, places=2)

    def test_forward_no_retroactive_leg1_before_amplitude_gate(self):
        """前向固定前缀：振幅未过闸的早盘不得成交；齐窗后半上涨确认后买入。"""
        d = "2024-01-07"
        bar = _bar(d, 100, 103, 94, 100)
        mins = _mins(
            d,
            [
                (935, 100, 100.05, 99.95, 100),  # 窄幅
                (940, 100, 100.05, 99.9, 99.92),
                (945, 99.92, 100.5, 97.0, 97.5),  # 阳 · 振幅拉开
                (1000, 97.5, 98.2, 97.2, 98.0),  # 阳 · 齐窗买
                (1400, 98.0, 103, 97.5, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        buys = [t for t in out.get("trades") or [] if str(t.get("side") or "") == "t0_buy"]
        self.assertEqual(len(buys), 1, out)
        self.assertIn("10:00", str(buys[0].get("at") or ""), out)
        self.assertNotIn("09:40", str(buys[0].get("at") or ""), out)
        self.assertEqual(out.get("range_mode"), "fixed_prefix")
        ft = out.get("forward_trace") or []
        self.assertGreaterEqual(len(ft), 4, out)
        ready = [r for r in ft if r.get("entry_ready")]
        self.assertEqual(len(ready), 1, out)
        self.assertTrue(ready[0].get("leg1_fill"), out)

    def test_flat_prefix_does_not_burn_abandon_budget(self):
        """早盘一字板不计入固定前缀决策；开板后凑齐固定窗仍可开。"""
        d = "2024-02-04"
        bar = _bar(d, 110, 110, 100, 105)
        points = []
        for hm in (935, 940, 945):
            points.append((hm, 110, 110.05, 109.95, 110.0))
        points.extend(
            [
                (1000, 110, 110.1, 104.0, 104.5),  # 阴 · 开板
                (1005, 104.5, 105.5, 104.2, 105.2),  # 阳
                (1010, 105.2, 106.0, 105.0, 105.8),  # 阳 · 若 fixed=6…
                (1400, 105.5, 112, 105, 110),
            ]
        )
        mins = _mins(d, points)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=110,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
                y_path_abandon_bars_buy_then_sell=6,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertTrue(out.get("success"), out)
        self.assertFalse(out.get("path_abandon"), out)
        self.assertGreater(out.get("bought_qty") or 0, 0, out)

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
                "sell_trigger_pct": 2.0,
                "buy_trigger_pct": 1.5,
            },
            minute_by_date=mins,
            compare_optimistic=False,
            compare_daily=False,
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
        report = backtest_t0_on_bars(
            bars,
            initial_shares=1000,
            initial_cost=100,
            initial_cash=50000,
            rules=_rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
            ),
            minute_by_date=mins,
            compare_optimistic=False,
        )
        traded = [
            x
            for x in (report.get("days") or [])
            if int(x.get("bought_qty") or 0) > 0 or int(x.get("sold_qty") or 0) > 0
        ]
        self.assertEqual(len(traded), 1, report)
        ft = traded[0].get("forward_trace") or []
        self.assertGreaterEqual(len(ft), 4, traded[0])
        self.assertTrue(any(r.get("leg1_fill") for r in ft), ft)

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
            sell_trigger_pct=1.5,
            buy_trigger_pct=2.0,
        )
        cfg = load_t0_rules(rules)
        day_out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=rules,
        )
        setup = _intraday_setup(
            code="688047",
            holding={"shares": 1000, "cost": 100, "stock_name": "测"},
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=1000,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
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

    def test_intraday_forward_no_retroactive_leg1(self):
        """Worker 递增长前缀：固定前缀未齐时的早触价不得落账。"""
        from core.t0.intraday import process_holding_intraday

        d = "2024-01-07"
        bar = _bar(d, 100, 103, 94, 100)
        # 第 2 根已触买价，但须等满 4 根且后半阳线占比过闸后才落账
        all_mins = _mins_lh(bar=bar)
        cfg = load_t0_rules(
            _rules(
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                sell_trigger_pct=1.5,
                buy_trigger_pct=2.0,
            )
        )
        holding = {"shares": 1000, "cost": 100, "stock_name": "测"}
        paper = {"cash": 50000.0, "holdings": [holding], "trades": []}
        st: dict = {"phase": "idle", "legs_written": 0}
        legs: list = []
        for n in range(2, len(all_mins) + 1):
            st, new_legs, _ = process_holding_intraday(
                code="688047",
                holding=holding,
                stock_state=st,
                minute_bars=all_mins[:n],
                bar=bar,
                cfg=cfg,
                sellable=1000,
                cash=float(paper.get("cash") or 0),
                atr_pct=None,
                hist_bars=None,
                scores=None,
                stance_code="hold",
                coupling_mode="independent",
                as_of=d,
                log_source="paper_t0_auto",
                paper=paper,
                applied_legs=len(legs),
            )
            legs.extend(new_legs)
        buys = [t for t in legs if str(t.get("side") or "") == "t0_buy"]
        self.assertEqual(len(buys), 1, legs)
        self.assertIn("09:50", str(buys[0].get("at") or ""), legs)
        self.assertNotIn("09:40", str(buys[0].get("at") or ""), legs)

    def test_forward_trace_pm_block_after_degrade(self):
        """午后禁新开：触价行标 pm_block。"""
        d = "2024-01-08"
        bar = _bar(d, 100, 106, 98, 101)
        mins = _mins_hl(d, o=100, high=106, low=98, close=101, bar=bar)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            minute_bars=mins,
            rules=_rules(
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=0.5,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
                t0_pm_degrade_sell_then_buy="09:30",
            ),
        )
        ft = out.get("forward_trace") or []
        self.assertTrue(ft, out)
        pm_blocks = [r for r in ft if r.get("pm_block")]
        self.assertTrue(pm_blocks, ft)
        self.assertEqual(int(out.get("sold_qty") or 0), 0, out)

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
            },
            minute_by_date=None,
            require_minute=True,
            compare_optimistic=False,
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
                    "sell_trigger_pct": 0.5,
                    "buy_trigger_pct": 0.5,
                    "direction": "buy_then_sell",
                    "fill_mode": "trigger",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 0.5,
                    "must_cover_same_day": True,
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
                "t0": {
                    "t0_ratio": 0.4,
                    "sell_trigger_pct": 2.0,
                    "buy_trigger_pct": 1.5,
                    "direction": "sell_then_buy",
                    "fill_mode": "optimistic",
                    "path_mode": "first_touch",
                    "use_atr": False,
                    "min_range_pct": 1.0,
                    "y_path_abandon_bars": 4,
                    "y_path_abandon_bars_sell_then_buy": 4,
                    "y_prefix_downbar_ratio_sell_then_buy": 0.5,
                }
            },
        }
        bars = {"600519": _bar("2026-01-09", 100, 105, 98, 101)}
        mins = {"600519": _mins_hl(bar=bars["600519"])}
        out = simulate_t0_on_holdings(
            paper, bars_by_code=bars, minute_bars_by_code=mins, dry_run=True
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["dry_run"])
        self.assertGreater(len(out["trades"]), 0)
        self.assertEqual(len(paper["trades"]), 0)
        self.assertEqual(float(paper["holdings"][0]["shares"]), 1000)

        out2 = simulate_t0_on_holdings(
            paper, bars_by_code=bars, minute_bars_by_code=mins, dry_run=False
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

    def test_routing_and_skill_task(self):
        self.assertEqual(infer_quant_task("茅台底仓做T回测一下"), "t0_backtest")
        self.assertIn("t0_backtest", AVAILABLE_TASKS)

    def test_engine_offline_backtest(self):
        bars = [_bar(f"d{i}", 100, 108, 95, 101) for i in range(30)]
        with patch(
            "core.data.facade.bars_and_source", return_value=(bars, "mock")
        ), patch(
            "skills.common.quote_api.StockAPI.query",
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
        self.assertIn("y_trade_floor", cfg)
        self.assertEqual(cfg["y_tau_enter"], 0.01)
        self.assertEqual(cfg["y_tau_enter_strong"], 0.01)
        self.assertEqual(cfg["y_score_source"], "compute")

    def test_y_tau_enter_strong_migrates_into_enter(self):
        cfg = load_t0_rules({"y_tau_enter": 0.4, "y_tau_enter_strong": 0.6})
        self.assertEqual(cfg["y_tau_enter"], 0.6)
        self.assertEqual(cfg["y_tau_enter_strong"], 0.6)
        low_strong = load_t0_rules({"y_tau_enter": 0.5, "y_tau_enter_strong": 0.3})
        self.assertEqual(low_strong["y_tau_enter"], 0.5)
        self.assertEqual(low_strong["y_tau_enter_strong"], 0.5)

    def test_y_tau_enter_floor_allows_001(self):
        cfg = load_t0_rules({"y_tau_enter": 0.01})
        self.assertEqual(cfg["y_tau_enter"], 0.01)
        clamped = load_t0_rules({"y_tau_enter": 0.0})
        self.assertEqual(clamped["y_tau_enter"], 0.01)

    def test_side_tau_enter_defaults_follow_base(self):
        cfg = load_t0_rules({"y_tau_enter": 0.4})
        self.assertEqual(cfg["y_tau_enter_sell_then_buy"], 0.4)
        self.assertEqual(cfg["y_tau_enter_buy_then_sell"], 0.4)
        split = load_t0_rules(
            {
                "y_tau_enter": 0.02,
                "y_tau_enter_sell_then_buy": 0.01,
                "y_tau_enter_buy_then_sell": 0.08,
            }
        )
        self.assertEqual(split["y_tau_enter_sell_then_buy"], 0.01)
        self.assertEqual(split["y_tau_enter_buy_then_sell"], 0.08)

    def test_side_exec_params_apply_by_direction(self):
        from core.t0.config import apply_side_exec_params, load_t0_rules

        cfg = load_t0_rules(
            {
                "buy_trigger_pct_sell_then_buy": 1.2,
                "sell_trigger_pct_buy_then_sell": 1.5,
                # 旧相对开盘第一腿键应被丢弃
                "buy_trigger_pct_buy_then_sell": 0.7,
                "sell_trigger_pct_sell_then_buy": 2.4,
                "min_range_pct_sell_then_buy": 0.3,
                "min_range_pct_buy_then_sell": 1.0,
                "fill_mode_sell_then_buy": "trigger",
                "fill_mode_buy_then_sell": "mid",
                # 旧 leg1 侧向键应被丢弃
                "sell_trigger_pct_long": 0.8,
                "buy_trigger_pct_reverse": 0.6,
            }
        )
        self.assertNotIn("sell_trigger_pct_long", cfg)
        self.assertNotIn("buy_trigger_pct_reverse", cfg)
        self.assertNotIn("buy_trigger_pct_buy_then_sell", cfg)
        self.assertNotIn("sell_trigger_pct_sell_then_buy", cfg)
        stb_cfg = apply_side_exec_params(cfg, "sell_then_buy")
        self.assertEqual(stb_cfg["buy_trigger_pct"], 1.2)
        self.assertEqual(stb_cfg["sell_trigger_pct"], 3.0)  # 无反T第一腿冲高%
        self.assertEqual(stb_cfg["min_range_pct"], 0.3)
        self.assertEqual(stb_cfg["fill_mode"], "trigger")
        bts_cfg = apply_side_exec_params(cfg, "buy_then_sell")
        self.assertEqual(bts_cfg["sell_trigger_pct"], 1.5)
        self.assertEqual(bts_cfg["buy_trigger_pct"], 1.0)  # 无正T第一腿低吸%
        self.assertEqual(bts_cfg["min_range_pct"], 1.0)
        self.assertEqual(bts_cfg["fill_mode"], "mid")
        self.assertEqual(stb_cfg["t0_pm_degrade"], "13:00")
        self.assertEqual(bts_cfg["t0_pm_degrade"], "14:00")

    def test_asymmetric_tau_enter_blocks_weak_buy_then_sell_allows_weak_sell_then_buy(self):
        from core.t0.score_policy import resolve_dual_y_direction

        cfg = load_t0_rules(
            {
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
                "y_tau_enter_sell_then_buy": 0.01,
                "y_tau_enter_buy_then_sell": 0.08,
                "y_trade_enter": 0.01,
            }
        )
        weak_bts = resolve_dual_y_direction(
            scores={"y_tau": 0.05, "y_trade": 0.5},
            cfg=cfg,
            cash=1e5,
            shares=1000,
        )
        self.assertTrue(weak_bts.get("skip"))
        self.assertIn("正T", weak_bts.get("direction_reason") or "")  # y_tau_enter_buy_then_sell=0.08 挡 0.05

        weak_stb = resolve_dual_y_direction(
            scores={"y_tau": -0.06, "y_trade": -0.5},
            cfg=cfg,
            cash=1e5,
            shares=1000,
        )
        self.assertFalse(weak_stb.get("skip"), weak_stb.get("direction_reason"))
        self.assertEqual(weak_stb.get("direction"), "sell_then_buy")

        strong_bts = resolve_dual_y_direction(
            scores={"y_tau": 0.10, "y_trade": 0.5},
            cfg=cfg,
            cash=1e5,
            shares=1000,
        )
        self.assertFalse(strong_bts.get("skip"), strong_bts.get("direction_reason"))
        self.assertEqual(strong_bts.get("direction"), "buy_then_sell")

    def test_direction_uses_y_tau_oc_not_remaining_mapped(self):
        """映射后 y_τ 与 OC 异号时，定向必须跟 OC（开→收），否则会出现负τ正T。"""
        from core.t0.score_policy import resolve_dual_y_direction

        cfg = load_t0_rules(
            {
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
                "y_tau_enter": 0.01,
                "y_trade_enter": 0.01,
            }
        )
        # 中国动力类：剩余映射翻正，OC 仍空 → 必须反T
        out = resolve_dual_y_direction(
            scores={
                "y_tau": 0.19,
                "y_tau_oc": -0.78,
                "y_trade": -1.0,
                "y_eod": -0.5,
            },
            cfg=cfg,
            cash=1e5,
            shares=1000,
        )
        self.assertFalse(out.get("skip"), out.get("direction_reason"))
        self.assertEqual(out.get("direction"), "sell_then_buy")
        self.assertAlmostEqual(float(out.get("direction_score")), -0.78)

        # path 开：OC 与 path 异号 → 跳过（不得用映射后同号蒙混过关）
        cfg_path = load_t0_rules(
            {
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
                "y_tau_enter": 0.01,
                "y_path_enter": 0.01,
                "y_trade_enter": 0.01,
            }
        )
        blocked = resolve_dual_y_direction(
            scores={
                "y_tau": 0.19,
                "y_tau_oc": -0.78,
                "y_path": 1.4,
                "y_trade": -1.0,
                "y_eod": -0.5,
            },
            cfg=cfg_path,
            cash=1e5,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("异号", blocked.get("direction_reason") or "")

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
        self.assertAlmostEqual(sc["y_on"], 0.2)
        self.assertAlmostEqual(sc["y_on_path"], 3.5)
        packed = __import__(
            "core.t0.score_policy", fromlist=["pack_day_scores"]
        ).pack_day_scores(sc)
        self.assertAlmostEqual(packed["y_on"], 0.2)
        self.assertAlmostEqual(packed["y_on_path"], 3.5)

    def test_eod_factor_bar_limit_matches_score_stock(self):
        """EOD 窗必须与 score_stock.fetch_daily_bars(limit=40) 一致，避免周线切桶漂移。"""
        import inspect

        from core.signal.score_stock import fetch_daily_bars
        from core.t0.score_policy import _EOD_FACTOR_BAR_LIMIT

        self.assertEqual(int(_EOD_FACTOR_BAR_LIMIT), 40)
        params = inspect.signature(fetch_daily_bars).parameters
        self.assertEqual(params["limit"].default, 40)

    def test_tip_fields_include_features_on(self):
        from core.t0.score_policy import tip_fields_from_item

        tip = tip_fields_from_item(
            {
                "features_on": {
                    "ret_oc": -0.5,
                    "ret_cc": -1.2,
                    "gap_pct": -0.8,
                    "noise": 1,
                },
                "formula_terms_on": {
                    "intercept": 0.0,
                    "total": -0.7,
                    "terms": [{"key": "ret_oc", "contrib": -0.3}],
                },
            }
        )
        self.assertEqual(tip.get("features_on", {}).get("ret_oc"), -0.5)
        self.assertNotIn("noise", tip.get("features_on") or {})
        self.assertIsNotNone(tip.get("formula_terms_on"))

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
            feats_on = sc.get("features_on") or {}
            path_feats = sc.get("features_on_path") or {}
            # 开盘决策：price=open → ret_oc≈0；路径复盘用 close，ret_oc 非零
            if feats_on.get("ret_oc") is not None:
                self.assertAlmostEqual(float(feats_on["ret_oc"]), 0.0, places=3)
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

        align_trade_score_fields(item, write_score=False, refresh_window=False)
        sc = scores_from_item(item)
        self.assertAlmostEqual(sc["y_eod"], 2.5, places=4)
        self.assertNotAlmostEqual(sc["y_trade"], 2.5, places=3)
        self.assertAlmostEqual(float((sc.get("dual_score_weights") or {}).get("w_tau") or 0), 0.5, places=3)

    def test_scores_from_item_prefers_blend_for_trade(self):
        """双头：predicted_score=ŷ_EOD，ŷ_trade 须取 blend，不能塌成与 y_eod 相同。"""
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
            # 两次调用都应带上截面 gaps
            for call in mocked.call_args_list:
                self.assertGreaterEqual(len(call.kwargs.get("pool_gaps") or []), 2)
            self.assertTrue(scores_have_any(out.get("600000")))

    def test_compute_fallback_skips_ledger(self):
        from core.t0.score_policy import resolve_scores_for_code

        with patch(
            "core.t0.score_policy.compute_scores_from_bars",
            return_value={
                "y_eod": None,
                "y_tau": None,
                "y_trade": None,
                "y_on": None,
                "y_nowcast": None,
                "y_check": None,
                "eod_trust": None,
            },
        ), patch(
            "core.t0.score_policy.load_scores_for_code_date",
            side_effect=AssertionError("ledger must not be used on compute fallback"),
        ), patch(
            "core.t0.score_policy._scores_from_live_book",
            return_value={
                "y_eod": 0.2,
                "y_tau": 0.3,
                "y_trade": 0.2,
                "y_on": None,
                "y_nowcast": None,
                "y_check": "ok",
                "eod_trust": 1.0,
                "_score_source": "live_book",
            },
        ):
            sc = resolve_scores_for_code(
                "600000",
                hist_bars=[_bar("2026-01-01", 1, 1, 1, 1)] * 20,
                day_bar=_bar("2026-01-02", 1, 1, 1, 1),
                source="compute",
                allow_fallback=True,
            )
            self.assertEqual(sc.get("_score_source"), "live_book")
            self.assertAlmostEqual(float(sc.get("y_tau") or 0), 0.3)

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

    def test_dual_y_missing_scores_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0),
            scores={},
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out.get("skipped"))
        self.assertTrue(out.get("signal_skip"))
        self.assertIn("缺", out.get("reason") or "")

    def test_dual_y_trade_floor_skips(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0, y_trade_floor=0.15),
            scores={"y_trade": 0.05, "y_tau": 0.8, "y_eod": 0.1},
            minute_bars=_mins_hl(bar=bar))
        self.assertTrue(out.get("skipped"))
        self.assertIn("|y_trade|", out.get("reason") or "")

    def test_dual_y_trade_floor_allows_large_negative(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(direction="dual_y", min_range_pct=1.0, y_trade_floor=0.15),
            scores={"y_trade": -0.5, "y_tau": 0.8, "y_eod": 0.1},
            minute_bars=_mins_hl(bar=bar))
        self.assertNotIn("预期幅度不足", out.get("reason") or "")

    def test_dual_y_eod_tau_disagree_weak_eod_ok(self):
        """|y_eod|≤gate 时 eod 与 τ 异号不拦（窗口不同）。"""
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=_rules(
                direction="dual_y",
                min_range_pct=1.0,
                y_eod_prior=0.35,
                y_tau_enter=0.25,
                fill_mode="optimistic",
            ),
            scores={"y_trade": 0.2, "y_tau": 0.8, "y_eod": -0.05},
            minute_bars=_mins_lh(bar=bar))
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertNotIn("异号", out.get("reason") or "")
        self.assertEqual(out.get("direction_used"), "buy_then_sell")

    def test_dual_y_strong_eod_tau_disagree_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": -1.25},
            cfg={
                "y_tau_enter": 0.25,
                "y_eod_strong": 1.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("y_eod", out.get("direction_reason") or "")
        self.assertIn("异号", out.get("direction_reason") or "")

        ok = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": 1.5},
            cfg={
                "y_tau_enter": 0.25,
                "y_eod_strong": 1.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))
        self.assertEqual(ok.get("direction"), "buy_then_sell")

    def test_dual_y_eod_below_enter_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": 0.005},
            cfg={
                "y_tau_enter": 0.25,
                "y_eod_enter": 0.01,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("y_eod", out.get("direction_reason") or "")
        self.assertIn("未过门槛", out.get("direction_reason") or "")

        ok = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": None},
            cfg={
                "y_tau_enter": 0.25,
                "y_eod_enter": 0.01,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))

    def test_dual_y_strong_trade_tau_disagree_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        # |y_trade|>strong 且与 τ 异号 → 跳过（不再误靠 y_eod 闸）
        out = resolve_dual_y_direction(
            scores={"y_trade": -0.43, "y_tau": 0.10, "y_eod": 0.05},
            cfg={
                "y_tau_enter": 0.05,
                "y_trade_strong": 0.1,
                "y_eod_strong": 5.0,
                "y_eod_enter": 0.01,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"), out)
        self.assertIn("y_trade", out.get("direction_reason") or "")

        ok = resolve_dual_y_direction(
            scores={"y_trade": 0.43, "y_tau": 0.10, "y_eod": 0.05},
            cfg={
                "y_tau_enter": 0.05,
                "y_trade_strong": 0.1,
                "y_eod_strong": 5.0,
                "y_eod_enter": 0.01,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"), ok.get("direction_reason"))

        # |y_trade|≤strong：异号仍放行
        weak = resolve_dual_y_direction(
            scores={"y_trade": -0.05, "y_tau": 0.10, "y_eod": 0.05},
            cfg={
                "y_tau_enter": 0.05,
                "y_trade_strong": 0.1,
                "y_eod_strong": 5.0,
                "y_eod_enter": 0.01,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(weak.get("skip"), weak.get("direction_reason"))
    def test_dual_y_strong_gate_uses_config_defaults(self):
        from core.t0.score_policy import resolve_dual_y_direction

        # 默认 y_eod_strong=0.5：抬高 trade 闸，专测 eod
        cfg = load_t0_rules(
            {
                "y_tau_enter": 0.02,
                "y_trade_strong": 5.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            }
        )
        blocked = resolve_dual_y_direction(
            scores={"y_trade": 0.15, "y_tau": -0.5, "y_eod": 0.60},
            cfg=cfg,
            cash=50000,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("y_eod", blocked.get("direction_reason") or "")
        allowed = resolve_dual_y_direction(
            scores={"y_trade": 0.08, "y_tau": -0.5, "y_eod": 0.40},
            cfg=cfg,
            cash=50000,
            shares=1000,
        )
        self.assertFalse(allowed.get("skip"), allowed.get("direction_reason"))

        # 默认 y_trade_strong=0.5：|trade|=0.60% 异号应拦
        cfg_trade = load_t0_rules(
            {
                "y_tau_enter": 0.05,
                "y_eod_strong": 5.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            }
        )
        pl = resolve_dual_y_direction(
            scores={"y_trade": -0.60, "y_tau": 0.10, "y_eod": 1.01},
            cfg=cfg_trade,
            cash=50000,
            shares=1000,
        )
        self.assertTrue(pl.get("skip"), pl)
        self.assertIn("y_trade", pl.get("direction_reason") or "")
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                must_cover_same_day=False,
                y_tau_enter=0.25,
            ),
            scores={
                "y_trade": -0.3,
                "y_tau": -0.8,
                "y_eod": -0.4,
                "y_on": 0.2,
            },
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
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                fill_mode="optimistic",
                y_tau_enter=0.25,
            ),
            scores={
                "y_trade": 0.2,
                "y_tau": 0.8,
                "y_eod": 0.5,
                "y_on": 0.1,
            },
            minute_bars=_mins_lh(bar=bar))
        self.assertTrue(out["success"])
        self.assertFalse(out.get("skipped"), out.get("reason"))
        self.assertEqual(out.get("direction_used"), "buy_then_sell")

    def test_scale_t0_ratio_no_longer_scales(self):
        """动仓比例固定；ŷ 改缩放目标价。"""
        from core.t0.score_policy import scale_t0_ratio

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_ratio_boost_cap": 1.25,
                "y_ratio_cut": 0.75,
                "y_tau_enter": 0.25,
            }
        )
        weak = scale_t0_ratio(0.4, {"y_trade": 0.10}, cfg)
        strong = scale_t0_ratio(0.4, {"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(weak, 0.4)
        self.assertAlmostEqual(strong, 0.4)

    def test_t0_confidence_scale_trade_and_triggers(self):
        from core.t0.score_policy import scale_t0_triggers, t0_confidence_scale

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_ratio_cut": 0.75,
                "y_ratio_boost_cap": 2.0,
                "y_tau_enter": 0.25,
                "y_ratio_tau_soft_band": 0.0,
            }
        )
        weak = t0_confidence_scale({"y_trade": 0.10}, cfg)
        strong = t0_confidence_scale({"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(weak, 0.75, places=3)
        self.assertAlmostEqual(strong, 2.0, places=3)
        trig = scale_t0_triggers(2.0, 1.5, {"y_trade": 0.10}, cfg)
        self.assertAlmostEqual(trig["trigger_scale"], 0.75, places=3)
        self.assertAlmostEqual(trig["sell_trigger_pct"], 1.5, places=3)
        self.assertAlmostEqual(trig["buy_trigger_pct"], 1.125, places=3)
        self.assertAlmostEqual(trig["sell_trigger_pct_base"], 2.0, places=3)
        trig_s = scale_t0_triggers(2.0, 1.5, {"y_trade": 1.30}, cfg)
        self.assertAlmostEqual(trig_s["trigger_scale"], 2.0, places=3)
        self.assertAlmostEqual(trig_s["sell_trigger_pct"], 4.0, places=3)

    def test_t0_confidence_scale_tau_and_eod_align(self):
        from core.t0.score_policy import t0_confidence_scale

        cfg = load_t0_rules(
            {
                "y_trade_floor": 0.15,
                "y_tau_enter": 0.25,
                "y_eod_prior": 0.35,
                "y_ratio_cut": 0.75,
                "y_ratio_tau_soft_band": 0.0,
                "y_ratio_eod_align_boost": 1.10,
            }
        )
        # 中等 τ：strength < 1；eod 同向略抬
        mid = t0_confidence_scale(
            {"y_trade": 0.30, "y_tau": -0.50, "y_eod": -0.40},
            cfg,
        )
        no_eod = t0_confidence_scale(
            {"y_trade": 0.30, "y_tau": -0.50, "y_eod": 0.05},
            cfg,
        )
        self.assertGreater(mid, no_eod)
        self.assertLessEqual(mid, 1.0)
        self.assertGreaterEqual(no_eod, 0.75)

    def test_dual_y_scaled_trigger_vs_weak_trade(self):
        bar = _bar("2026-01-10", 100, 105, 98, 101)
        rules = _rules(
            direction="dual_y",
            path_mode="first_touch",
            min_range_pct=1.0,
            sell_trigger_pct=2.0,
            buy_trigger_pct=1.5,
            fill_mode="optimistic",
            t0_ratio=0.4,
            y_tau_enter=0.25,
            y_trade_floor=0.15,
            y_trade_strong=2.0,
            y_ratio_cut=0.75,
            y_ratio_tau_soft_band=0.0,
        )
        strong = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={"y_trade": 1.20, "y_tau": -0.80, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        weak = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=50000,
            rules=rules,
            scores={"y_trade": 0.20, "y_tau": -0.80, "y_eod": -0.40},
            minute_bars=_mins_hl(bar=bar),
        )
        self.assertTrue(strong["success"])
        self.assertTrue(weak["success"])
        self.assertFalse(strong.get("skipped"))
        self.assertFalse(weak.get("skipped"))
        # 动仓固定；弱信号压低目标价
        self.assertAlmostEqual(float(strong.get("t0_ratio") or 0), float(weak.get("t0_ratio") or 0))
        self.assertEqual(strong.get("sold_qty"), weak.get("sold_qty"))
        self.assertGreater(float(strong.get("trigger_scale") or 0), float(weak.get("trigger_scale") or 0))
        self.assertGreater(
            float(strong.get("sell_trigger_pct") or 0),
            float(weak.get("sell_trigger_pct") or 0),
        )

    def test_intraday_setup_applies_scaled_triggers(self):
        from core.t0.intraday import _intraday_setup

        bar = _bar("2026-01-10", 100, 105, 98, 101)
        mins = _mins_hl(bar=bar)
        holding = {"shares": 1000, "cost": 100, "stock_name": "测试"}
        cfg = load_t0_rules(
            {
                "direction": "dual_y",
                "use_atr": False,
                "min_range_pct": 1.0,
                "fill_mode": "optimistic",
                "t0_ratio": 0.4,
                "sell_trigger_pct": 2.0,
                "buy_trigger_pct": 1.5,
                "y_tau_enter": 0.25,
                "y_trade_floor": 0.15,
                "y_trade_strong": 3.0,
                "y_eod_prior": 0.35,
                "y_ratio_cut": 0.75,
                "y_ratio_tau_soft_band": 0.0,
                "y_block_tau_nowcast_sign": False,
                "t0_pm_degrade": "",
                # 与 _rules / _mins_hl 短前缀夹具对齐
                "y_path_abandon_bars_sell_then_buy": 4,
                "y_path_abandon_bars_buy_then_sell": 4,
                "y_prefix_downbar_ratio_sell_then_buy": 0.5,
                "y_prefix_upbar_ratio_buy_then_sell": 0.5,
            }
        )
        out = _intraday_setup(
            code="000001",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=1000,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores={"y_trade": 2.0, "y_tau": -1.5, "y_eod": -0.80},
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"))
        day = out.get("day_result") or {}
        self.assertAlmostEqual(float(day.get("t0_ratio") or 0), 0.4, places=3)
        self.assertEqual(day.get("sold_qty"), 400)
        self.assertAlmostEqual(float(day.get("trigger_scale") or 0), 2.0, places=2)
        self.assertAlmostEqual(float(day.get("sell_trigger_pct") or 0), 4.0, places=2)
        # 落账明细 y_* 列依赖 day.scores / direction_features
        scores = day.get("scores") or {}
        self.assertAlmostEqual(float(scores.get("y_tau")), -1.5, places=2)
        self.assertAlmostEqual(float(scores.get("y_trade")), 2.0, places=2)
        self.assertAlmostEqual(float(scores.get("y_eod")), -0.80, places=2)
        feats = day.get("direction_features") or {}
        self.assertAlmostEqual(float(feats.get("y_tau")), -1.5, places=2)
        self.assertTrue(day.get("direction_reason"))

    def test_intraday_setup_defers_eod_cover_mid_morning(self):
        """盘中前缀有第一腿时不得把下一根 5m 当成收盘强平。"""
        from core.t0.intraday import _intraday_setup, process_holding_intraday

        d = "2026-08-26"
        bar = _bar(d, 9.38, 9.55, 9.30, 9.40)
        # 09:35 触卖；09:40 未触买回 — 旧 bug 会在 09:40 打 eod_cover
        mins = _mins(
            d,
            [
                (930, 9.38, 9.40, 9.35, 9.39),
                (935, 9.39, 9.50, 9.39, 9.48),
                (940, 9.48, 9.49, 9.46, 9.47),
            ],
        )
        holding = {"shares": 100, "cost": 9.38, "stock_name": "中国铝业"}
        cfg = load_t0_rules(
            {
                "direction": "sell_then_buy",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 1.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day": True,
                "t0_pm_degrade": "",
                "y_path_abandon_bars_sell_then_buy": 3,
                "y_prefix_downbar_ratio_sell_then_buy": 0.5,
            }
        )
        out = _intraday_setup(
            code="601600",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertEqual(day.get("sold_qty"), 100)
        self.assertEqual(day.get("covered_qty") or 0, 0)
        trades = day.get("trades") or []
        self.assertEqual(len(trades), 1)
        self.assertNotEqual(trades[0].get("leg_kind"), "eod_cover")
        self.assertFalse(out.get("path_complete"))

        paper = {"cash": 50000, "holdings": [holding], "trades": []}
        st, new_trades, _snap = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state={"phase": "idle", "legs_written": 0},
            minute_bars=mins,
            bar=bar,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=0,
        )
        self.assertEqual(len(new_trades), 1)
        self.assertEqual(st.get("phase"), "after_leg1")
        self.assertEqual(st.get("legs_written"), 1)
        self.assertEqual(st.get("shares_day_start"), 100)

        # 同前缀再 tick：持仓已因卖出变少，仍用日初仓重放；无新腿，保持 after_leg1
        st2, new2, _ = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state=st,
            minute_bars=mins,
            bar=bar,
            cfg=cfg,
            sellable=0,
            cash=float(paper.get("cash") or 0),
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=1,
        )
        self.assertEqual(new2, [])
        self.assertEqual(st2.get("phase"), "after_leg1")

        # 收到 14:55 收盘 K：反T放弃买回，不再强平第二腿
        mins_eod = mins + _mins(d, [(1455, 9.47, 9.48, 9.45, 9.46)])
        st3, new3, _ = process_holding_intraday(
            code="601600",
            holding=holding,
            stock_state=st2,
            minute_bars=mins_eod,
            bar=bar,
            cfg=cfg,
            sellable=0,
            cash=float(paper.get("cash") or 0),
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
            as_of=d,
            log_source="paper_t0_auto",
            paper=paper,
            applied_legs=1,
        )
        self.assertEqual(new3, [])
        self.assertEqual(st3.get("phase"), "done")
        self.assertEqual(st3.get("legs_written"), 1)
        day3 = (st3.get("day_result") or {}) if isinstance(st3.get("day_result"), dict) else {}
        # day_result 可能在 setup 快照里；以无新买腿 + done 为准
        self.assertTrue(
            all(not str(t.get("side", "")).endswith("buy") for t in (st3.get("pending_trades") or [])),
        )

    def test_intraday_setup_reverse_defers_eod_on_morning_prefix(self):
        """正T盘中前缀已低吸时，Worker 不得把 10:40 当成收盘强平。"""
        from core.t0.intraday import _intraday_setup

        d = "2026-08-27"
        bar = _bar(d, 34.15, 34.59, 33.85, 34.52)
        mins = _mins(
            d,
            [
                (935, 34.15, 34.20, 34.00, 34.10),
                (940, 34.10, 34.12, 33.80, 33.945),
                (1040, 34.00, 34.11, 33.95, 34.11),
            ],
        )
        holding = {"shares": 10000, "cost": 34.15, "stock_name": "中国船舶"}
        cfg = load_t0_rules(
            {
                "direction": "buy_then_sell",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 5.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day": True,
                "t0_pm_degrade": "",
                "y_path_abandon_bars_buy_then_sell": 3,
                "y_prefix_upbar_ratio_buy_then_sell": 0.5,
            }
        )
        out = _intraday_setup(
            code="600150",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=10000,
            cash=500000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertGreater(int(day.get("bought_qty") or 0), 0)
        self.assertEqual(int(day.get("sold_back_qty") or 0), 0)
        trades = day.get("trades") or []
        self.assertEqual(len(trades), 1)
        self.assertNotEqual(trades[0].get("leg_kind"), "eod_cover")
        self.assertFalse(out.get("path_complete"))

    def test_intraday_setup_eod_cover_at_session_close(self):
        """反T：收盘窗未买回 → abandon_cover，不强制买回。"""
        from core.t0.intraday import _intraday_setup

        d = "2026-08-26"
        bar = _bar(d, 9.38, 9.55, 9.30, 9.40)
        mins = _mins(
            d,
            [
                (930, 9.38, 9.40, 9.35, 9.39),
                (935, 9.39, 9.50, 9.39, 9.48),
                (1000, 9.48, 9.49, 9.46, 9.47),
                (1455, 9.47, 9.48, 9.45, 9.46),
            ],
        )
        holding = {"shares": 100, "cost": 9.38, "stock_name": "中国铝业"}
        cfg = load_t0_rules(
            {
                "direction": "sell_then_buy",
                "use_atr": False,
                "min_range_pct": 0.5,
                "fill_mode": "trigger",
                "t0_ratio": 1.0,
                "sell_trigger_pct": 1.0,
                "buy_trigger_pct": 1.0,
                "must_cover_same_day_sell_then_buy": False,
                "t0_pm_degrade_sell_then_buy": "",
                "y_path_abandon_bars_sell_then_buy": 3,
                "y_prefix_downbar_ratio_sell_then_buy": 0.5,
            }
        )
        out = _intraday_setup(
            code="601600",
            holding=holding,
            bar=bar,
            minute_bars=mins,
            cfg=cfg,
            sellable=100,
            cash=50000,
            atr_pct=None,
            hist_bars=None,
            scores=None,
            stance_code="hold",
            coupling_mode="independent",
        )
        self.assertTrue(out.get("ready"), out)
        day = out.get("day_result") or {}
        self.assertEqual(day.get("sold_qty"), 100)
        self.assertEqual(day.get("covered_qty") or 0, 0)
        self.assertEqual(day.get("exit_reason"), "abandon_cover")
        buys = [t for t in (day.get("trades") or []) if str(t.get("side")).endswith("buy")]
        self.assertEqual(buys, [])
        self.assertTrue(out.get("path_complete"))

    def test_dual_y_trend_map_inverts_positive_tau(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_eod": 0.4},
            cfg={"y_tau_enter": 0.25, "y_tau_map": "trend"},
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "buy_then_sell")
        self.assertIn("trend", out.get("direction_reason") or "")
        self.assertIn("正T", out.get("direction_reason") or "")
        self.assertNotIn("buy_then_sell", out.get("direction_reason") or "")

    def test_dual_y_blocks_tau_nowcast_sign_strong_disagree(self):
        """|nowcast| 够强且与 y_τ 异号 → 跳过；缺 nowcast 不拦。"""
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.50,
                "y_tau": 0.70,
                "y_nowcast": -1.20,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
                "y_nc_strong": 1.0,
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("异号", out.get("direction_reason") or "")
        self.assertIn("y_nc", out.get("direction_reason") or "")

        # 弱 nowcast 异号不拦
        weak = resolve_dual_y_direction(
            scores={
                "y_trade": 0.50,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": -0.30,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
                "y_nc_strong": 1.0,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(weak.get("skip"))

        # 缺 nowcast：即使开闸也不因「异号」跳过
        ok = resolve_dual_y_direction(
            scores={"y_trade": 0.50, "y_tau": 0.70, "y_eod": 0.27},
            cfg={
                "y_tau_enter": 0.40,
                "y_block_tau_nowcast_sign": True,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))
        self.assertNotIn("异号", ok.get("direction_reason") or "")

        # |y_trade| 弱于 strong：异号不拦（本测专验 nc，非 trade 强闸）
        trade_weak = resolve_dual_y_direction(
            scores={
                "y_trade": -0.05,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": 0.40,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_trade_strong": 0.1,
                "y_block_tau_nowcast_sign": True,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(trade_weak.get("skip"), trade_weak.get("direction_reason"))
        self.assertNotIn("异号", trade_weak.get("direction_reason") or "")

        # |y_trade|>strong 且与 τ 异号：强闸跳过（与 nc 闸独立）
        trade_strong = resolve_dual_y_direction(
            scores={
                "y_trade": -0.48,
                "y_tau": 0.70,
                "y_eod": 0.27,
                "y_nowcast": 0.40,
            },
            cfg={
                "y_tau_enter": 0.40,
                "y_trade_strong": 0.1,
                "y_block_tau_nowcast_sign": True,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(trade_strong.get("skip"))
        self.assertIn("y_trade", trade_strong.get("direction_reason") or "")

    def test_t0_confidence_scale_soft_tau_band_cuts(self):
        from core.t0.score_policy import t0_confidence_scale

        cfg = {
            "y_trade_floor": 0.15,
            "y_tau_enter": 0.40,
            "y_ratio_cut": 0.75,
            "y_ratio_tau_soft_band": 0.20,
            "y_ratio_eod_align_boost": 1.0,
        }
        soft = t0_confidence_scale({"y_trade": 0.5, "y_tau": 0.45}, cfg)
        strong = t0_confidence_scale({"y_trade": 0.5, "y_tau": 0.90}, cfg)
        self.assertAlmostEqual(soft, 0.75, places=4)
        self.assertGreater(strong, soft)

    def test_buy_then_sell_pm_chase_midpoint(self):
        """14:00 后已开未平：第二腿目标 = 旧目标与现价中点，可触达则成交（非强制市价平）。"""
        bar = _bar("2026-07-29", 100, 102, 97, 100.5)
        # 低吸 ~98.5 → 卖目标 buy×1.05≈103.4；14:00 中点≈101.3；14:10 再中点触高
        mins = _mins(
            "2026-07-29",
            [
                (930, 100, 100.05, 99.95, 100),
                (935, 100, 100, 98.4, 98.65),
                (1400, 99.5, 100.0, 99.0, 99.2),
                (1410, 99.2, 101.0, 99.1, 100.5),
                (1500, 100.5, 100.8, 100.0, 100.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
                y_path_abandon_bars_buy_then_sell=2,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "pm_chase")
        notes = " ".join(str(t.get("note") or "") for t in out.get("trades") or [])
        self.assertIn("中点追价", notes)
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "pm_chase")
        # 追价卖应低于原 5% 目标（≈103.4），且不低于低吸价（must_cover 地板）
        self.assertLess(float(sell["price"]), 102.0)
        self.assertGreaterEqual(float(sell["price"]), 98.5)

    def test_buy_then_sell_stop_loss_close_after_arm(self):
        """正T：延迟 arm 根后收盘跌破止损 → stop_loss；影线刺破不触发。"""
        d = "2026-08-01"
        bar = _bar(d, 100, 105, 90, 99)
        # 前缀 4 根确认买 close=100；止损 1.2%→98.8
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 100.3, 100.0, 100.2),
                (945, 100.2, 100.4, 100.1, 100.3),
                (950, 100.3, 100.5, 100.0, 100.0),  # 确认买
                (955, 100.0, 100.1, 98.0, 99.5),  # arm#1：影线破止损，收盘未破
                (1000, 99.5, 99.6, 98.0, 99.2),  # arm#2
                (1005, 99.2, 99.3, 97.5, 98.5),  # 启用：收盘 98.5≤98.8
                (1500, 98.5, 104.0, 98.0, 103.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                sell_trigger_pct=5.0,
                min_range_pct=0.1,
                must_cover_same_day=True,
                t0_stop_pct_buy_then_sell=1.2,
                t0_stop_arm_bars=2,
                t0_stop_on_close=True,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertEqual(out.get("exit_reason"), "stop_loss", out)
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "stop")
        self.assertAlmostEqual(float(sell["price"]), 98.5, places=2)

        # 仅影线破、收盘未破 → 不止损（卖触发也够不着 → eod）
        mins2 = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 100.3, 100.0, 100.2),
                (945, 100.2, 100.4, 100.1, 100.3),
                (950, 100.3, 100.5, 100.0, 100.0),
                (955, 100.0, 100.1, 99.5, 99.8),
                (1000, 99.8, 99.9, 99.4, 99.6),
                (1005, 99.6, 99.7, 97.5, 99.0),  # low 破、close 99.0>98.8
                (1500, 99.0, 99.2, 98.8, 99.0),
            ],
        )
        out2 = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            minute_bars=mins2,
            rules=_rules(
                direction="buy_then_sell",
                sell_trigger_pct=5.0,
                min_range_pct=0.1,
                must_cover_same_day=True,
                t0_stop_pct_buy_then_sell=1.2,
                t0_stop_arm_bars=2,
                t0_stop_on_close=True,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertEqual(out2.get("exit_reason"), "eod_cover", out2)

    def test_buy_then_sell_stop_beats_pm_chase(self):
        """正T：止损优先于中点追价（同窗已追价可调目标，仍走 stop_loss）。"""
        d = "2026-08-02"
        bar = _bar(d, 100, 105, 90, 99)
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 100.1),
                (940, 100.1, 100.3, 100.0, 100.2),
                (945, 100.2, 100.4, 100.1, 100.3),
                (950, 100.3, 100.5, 100.0, 100.0),  # 确认买@100；止损 98.8
                (955, 100.0, 100.1, 99.5, 99.8),  # arm#1
                (1000, 99.8, 99.9, 99.2, 99.5),  # arm#2；追价起算根
                (1005, 99.5, 99.6, 97.8, 98.4),  # 收盘破止损；追价也在调，仍应 stop
                (1010, 98.4, 99.0, 98.0, 98.8),
                (1500, 98.8, 99.0, 98.5, 98.9),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            minute_bars=mins,
            rules=_rules(
                direction="buy_then_sell",
                sell_trigger_pct=5.0,
                min_range_pct=0.1,
                must_cover_same_day=True,
                t0_pm_degrade="10:00",
                t0_pm_chase_interval_min=5,
                t0_stop_pct_buy_then_sell=1.2,
                t0_stop_arm_bars=2,
                t0_stop_on_close=True,
                y_path_abandon_bars_buy_then_sell=4,
                y_prefix_upbar_ratio_buy_then_sell=0.5,
            ),
        )
        self.assertEqual(out.get("exit_reason"), "stop_loss", out)
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "stop")
        self.assertAlmostEqual(float(sell["price"]), 98.4, places=2)

    def test_sell_then_buy_stop_loss_close_after_arm(self):
        """反T：延迟 arm 根后收盘涨破止损 → stop_loss 买回；影线刺破不触发。"""
        d = "2026-08-02"
        bar = _bar(d, 100, 110, 90, 101)
        # 前缀 4 根确认卖 close=100；止损 1.2%→101.2
        mins = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 99.9),  # 阴
                (940, 99.9, 100.0, 99.5, 99.6),  # 阴
                (945, 99.6, 99.7, 99.2, 99.3),  # 阴
                (950, 99.3, 100.2, 99.0, 100.0),  # 确认卖
                (955, 100.0, 101.5, 99.8, 100.5),  # arm#1：影线破，收盘未破
                (1000, 100.5, 101.0, 100.0, 100.8),  # arm#2
                (1005, 100.8, 101.8, 100.5, 101.3),  # 启用：收盘 101.3≥101.2
                (1500, 101.3, 102.0, 99.0, 99.5),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            minute_bars=mins,
            rules=_rules(
                direction="sell_then_buy",
                buy_trigger_pct=5.0,
                min_range_pct=0.1,
                must_cover_same_day=False,
                t0_pm_degrade="",
                t0_stop_pct_sell_then_buy=1.2,
                t0_stop_arm_bars=2,
                t0_stop_on_close=True,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertEqual(out.get("exit_reason"), "stop_loss", out)
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        self.assertEqual(buy.get("leg_kind"), "stop")
        self.assertAlmostEqual(float(buy["price"]), 101.3, places=2)

        mins2 = _mins(
            d,
            [
                (935, 100, 100.2, 99.8, 99.9),
                (940, 99.9, 100.0, 99.5, 99.6),
                (945, 99.6, 99.7, 99.2, 99.3),
                (950, 99.3, 100.2, 99.0, 100.0),
                (955, 100.0, 100.5, 99.8, 100.2),
                (1000, 100.2, 100.6, 100.0, 100.4),
                (1005, 100.4, 101.8, 100.2, 100.9),  # high 破、close 未破
                (1500, 100.9, 101.0, 100.5, 100.8),
            ],
        )
        out2 = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            minute_bars=mins2,
            rules=_rules(
                direction="sell_then_buy",
                buy_trigger_pct=5.0,
                min_range_pct=0.1,
                must_cover_same_day=False,
                t0_pm_degrade="",
                t0_stop_pct_sell_then_buy=1.2,
                t0_stop_arm_bars=2,
                t0_stop_on_close=True,
                y_path_abandon_bars_sell_then_buy=4,
                y_prefix_downbar_ratio_sell_then_buy=0.5,
            ),
        )
        self.assertEqual(out2.get("exit_reason"), "abandon_cover", out2)

    def test_leg2_does_not_chase_before_touchable_trigger(self):
        """已可触达原目标时不得先追价压低/抬高成交价。"""
        bar = _bar("2026-07-29", 100, 106, 97, 101)
        # 低吸后目标≈103.425；14:00 hi=104 已够得着，即使 close 很低也不该先中点
        mins = _mins(
            "2026-07-29",
            [
                (930, 100, 100.05, 99.95, 100),
                (935, 100, 100, 98.4, 98.65),
                (1400, 99.0, 104.0, 98.5, 99.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
                y_path_abandon_bars_buy_then_sell=2,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "trigger", out)
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "trigger")
        self.assertAlmostEqual(float(sell["price"]), float(sell["trigger"]), places=4)
        self.assertGreater(float(sell["price"]), 103.0)

    def test_must_cover_chase_not_below_buy(self):
        """must_cover 正T：追价目标不得低于低吸价（宁可等 eod）。"""
        bar = _bar("2026-07-29", 100, 102, 90, 92)
        mins = _mins(
            "2026-07-29",
            [
                (930, 100, 100.05, 99.95, 100),
                (935, 100, 100, 98.4, 98.65),
                (1400, 98.0, 98.2, 97.0, 97.5),
                (1410, 97.5, 98.0, 96.0, 96.5),
                (1420, 96.5, 97.0, 95.0, 95.5),
                (1500, 95.5, 96.0, 92.0, 92.0),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
                y_path_abandon_bars_buy_then_sell=2,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            minute_bars=mins,
        )
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        # 下跌日：追价被成本地板挡住 → eod；若 pm_chase 成交则不得低于买价
        if sell.get("leg_kind") == "pm_chase":
            self.assertGreaterEqual(float(sell["price"]), float(buy["price"]) - 1e-6, out)
        else:
            self.assertEqual(out.get("exit_reason"), "eod_cover", out)
            self.assertEqual(sell.get("leg_kind"), "eod_cover")

    def test_must_cover_chase_not_above_sold(self):
        """must_cover 反T：追价目标不得高于卖出价（宁可等 eod）。"""
        from core.t0.minute_path import _first_touch_sell_then_buy

        bar = {"date": "2026-07-29", "open": 100, "high": 106, "low": 99, "close": 101}
        mins = [
            {"datetime": "2026-07-29 09:30:00", "open": 100, "high": 100.05, "low": 99.95, "close": 100},
            {"datetime": "2026-07-29 09:35:00", "open": 100, "high": 102.5, "low": 100, "close": 102},
            # 午后推高：中点追价若无天花板会抬过卖出价；有天花板则钉在 sold
            {"datetime": "2026-07-29 14:00:00", "open": 102, "high": 104, "low": 101.8, "close": 103.5},
            {"datetime": "2026-07-29 14:10:00", "open": 103.5, "high": 105, "low": 102.5, "close": 104},
            {"datetime": "2026-07-29 14:20:00", "open": 104, "high": 105, "low": 103, "close": 103.5},
            # lo 触及卖出价：天花板下可按 sold 成交（zero 成本）
            {"datetime": "2026-07-29 14:30:00", "open": 103, "high": 103.2, "low": 101.9, "close": 102.0},
            {"datetime": "2026-07-29 15:00:00", "open": 102, "high": 102.2, "low": 101.5, "close": 101.5},
        ]
        out = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=bar,
            shares=1000,
            sellable_shares=1000,
            ref=100.0,
            sell_trig=2.0,
            buy_trig=1.5,
            lot=100,
            fill_mode="trigger",
            cfg={
                "t0_ratio": 0.4,
                "must_cover_same_day": True,
                "lot_size": 100,
                "t0_pm_degrade": "14:00",
                "t0_pm_chase_interval_min": 10,
                "y_prefix_segment_enabled": False,
            },
            cost_model="zero",
            cost_params={},
            stock_code="",
            atr_pct=None,
            range_pct=3.0,
            t0_ratio=0.4,
            session_bars=mins,
            session_bar=bar,
            defer_eod=False,
        )
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        sold_px = float(sell["price"])
        if buy.get("leg_kind") == "pm_chase":
            self.assertLessEqual(float(buy["price"]), sold_px + 1e-6, out)
        else:
            self.assertEqual(out.get("exit_reason"), "eod_cover", out)
            self.assertEqual(buy.get("leg_kind"), "eod_cover")
            self.assertLessEqual(float(buy["price"]), sold_px + 1e-6, out)

    def test_buy_then_sell_pm_chase_interval_holds(self):
        """未满间隔不二次中点：14:00 调一次后 14:05 不调，原中点仍触不到则不成交。"""
        bar = _bar("2026-07-29", 100, 102, 97, 99)
        # 卖目标 = buy×1.05≈103.4；14:00 中点≈101.3；14:05 high=101 触不到
        mins = _mins(
            "2026-07-29",
            [
                (935, 100, 100, 98.4, 98.5),
                (1400, 99.5, 100.0, 99.0, 99.2),  # mid≈101.3
                (1405, 99.2, 101.0, 99.1, 100.5),  # high 101 < 101.3，且未到 10min
                (1500, 99.5, 99.8, 99.0, 99.0),  # 再中点仍够不着
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=5.0,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day=False,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("sold_back_qty") or 0, 0)
        self.assertNotEqual(out.get("exit_reason"), "pm_chase")

    def test_buy_then_sell_falling_day_mid_stays_above_then_eod(self):
        """单边下跌：纯中点仍高于 high，追价触不到 → 收盘强制平（非 14:00 市价）。"""
        bar = _bar("2026-07-29", 40.86, 41.0, 37.0, 37.33)
        pts = [(935, 40.9, 40.9, 40.4, 40.5)]
        px = 40.5
        t = 940
        while t <= 1500:
            hh, mm = divmod(t, 100)
            if mm >= 60:
                t = (hh + 1) * 100
                continue
            if 1130 <= t < 1300:
                t = 1300
                continue
            if t >= 1400:
                px = max(37.33, px - 0.08)
            else:
                px -= 0.05
            pts.append((t, px + 0.05, px + 0.1, px - 0.05, px))
            mm += 5
            t = (hh + 1) * 100 if mm >= 60 else hh * 100 + mm
        out = simulate_t0_day(
            bar=bar,
            shares=10000,
            cost=40.86,
            cash=2_000_000,
            rules=_rules(
                t0_ratio=1.0,
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=0.5,
                must_cover_same_day=True,
                t0_pm_degrade="14:00",
                t0_pm_chase_interval_min=10,
                use_atr=False,
                y_path_abandon_bars_buy_then_sell=2,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            minute_bars=_mins("2026-07-29", pts),
        )
        self.assertEqual(out.get("exit_reason"), "eod_cover")
        sell = next(t for t in out["trades"] if t.get("side") == "t0_sell")
        self.assertEqual(sell.get("leg_kind"), "eod_cover")
        self.assertTrue(str(sell.get("at") or "").startswith("2026-07-29 15:00"))
        self.assertLess(float(sell["price"]), 38.5)

    def test_sell_then_buy_no_pm_chase_abandons(self):
        """反T：关中点追价；固定买回触不到 → abandon_cover。"""
        bar = _bar("2026-07-29", 100, 103, 99, 101)
        # 卖@102 → 买回 100.47；午后 lo 最低 100.5，若追价本可成交，现应放弃
        mins = _mins(
            "2026-07-29",
            [
                (935, 100, 102.5, 100, 102),
                (1400, 101.8, 102.0, 100.9, 101.5),
                (1500, 101.5, 101.8, 100.5, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day_sell_then_buy=False,
                t0_pm_degrade_sell_then_buy="",
                t0_pm_chase_interval_min=10,
                use_atr=False,
                y_path_abandon_bars_sell_then_buy=2,
                y_prefix_segment_enabled_sell_then_buy=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "abandon_cover")
        self.assertFalse(any(t.get("side") == "t0_buy" for t in (out.get("trades") or [])))

    def test_sell_then_buy_pm_chase_buyback(self):
        """反T：15:00 起中点追价抬高买回目标。"""
        bar = _bar("2026-07-29", 100, 103, 99, 101)
        # 卖@102 → 买回基准 102×0.985≈100.47；15:00 中点抬高后 lo 可触
        mins = _mins(
            "2026-07-29",
            [
                (930, 100, 100.05, 99.95, 100),
                (935, 100, 102.5, 100, 102),
                (1500, 101.5, 101.8, 100.5, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="sell_then_buy",
                fill_mode="trigger",
                min_range_pct=1.0,
                must_cover_same_day_sell_then_buy=False,
                t0_pm_degrade_sell_then_buy="15:00",
                t0_pm_chase_interval_min=10,
                y_path_abandon_bars_sell_then_buy=2,
                y_prefix_segment_enabled_sell_then_buy=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "pm_chase")
        buy = next(t for t in out["trades"] if t.get("side") == "t0_buy")
        self.assertEqual(buy.get("leg_kind"), "pm_chase")

    def test_sell_then_buy_must_cover_eod_buy(self):
        """反T：勾选强制回补 → 收盘买回。"""
        bar = _bar("2026-08-26", 100, 102, 99, 101)
        mins = _mins(
            "2026-08-26",
            [
                (935, 100, 102, 100, 101.5),
                (1500, 101, 101.5, 100.8, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            rules=_rules(
                direction="sell_then_buy",
                sell_trigger_pct=1.0,
                buy_trigger_pct=2.0,
                min_range_pct=0.5,
                must_cover_same_day_sell_then_buy=True,
                t0_pm_degrade_sell_then_buy="",
                y_path_abandon_bars_sell_then_buy=2,
                y_prefix_segment_enabled_sell_then_buy=False,
            ),
            minute_bars=mins,
        )
        self.assertEqual(out.get("exit_reason"), "eod_cover")
        buys = [t for t in (out.get("trades") or []) if str(t.get("side", "")).endswith("buy")]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0].get("leg_kind"), "eod_cover")

    def test_pm_chase_blocks_late_open(self):
        """中点追价起算后不再新开第一腿。"""
        bar = _bar("2026-07-29", 100, 105, 96, 101)
        mins = _mins(
            "2026-07-29",
            [
                (1000, 100, 101, 99, 100),
                (1410, 100, 100, 96, 97),  # 低吸出现在午后 → 应跳过
                (1500, 97, 105, 97, 101),
            ],
        )
        out = simulate_t0_day(
            bar=bar,
            shares=1000,
            cost=100,
            cash=100000,
            rules=_rules(
                t0_ratio=0.4,
                sell_trigger_pct=2.0,
                buy_trigger_pct=1.5,
                direction="buy_then_sell",
                fill_mode="trigger",
                min_range_pct=1.0,
                t0_pm_degrade="14:00",
                use_atr=False,
                y_path_abandon_bars_buy_then_sell=2,
                y_prefix_segment_enabled_buy_then_sell=False,
            ),
            minute_bars=mins,
        )
        self.assertTrue(out.get("skipped"))
        self.assertIn("未开成第一腿", out.get("reason") or "")

    def test_dual_y_fixed_sell_then_buy_ignores_negative_tau(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": -0.8, "y_eod": -0.5},
            cfg={
                "y_tau_enter": 0.25,
                "y_tau_map": "fixed_sell_then_buy",
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "sell_then_buy")

    def test_load_y_tau_map_alias(self):
        from core.t0.config import load_t0_rules

        cfg = load_t0_rules({"y_tau_map": "follow"})
        self.assertEqual(cfg["y_tau_map"], "trend")

    def test_dual_y_overnight_allow_when_yon_aligns(self):
        from core.t0.config import load_t0_rules
        from core.t0.score_policy import resolve_cover_policy

        cfg = load_t0_rules(
            {"must_cover_same_day": False, "y_on_allow": 1.2, "y_trade_floor": 0.15}
        )
        cover = resolve_cover_policy(
            scores={"y_on": 1.5, "y_trade": 0.3},
            direction="buy_then_sell",
            cfg=cfg,
        )
        self.assertFalse(cfg["must_cover_same_day_buy_then_sell"])
        self.assertFalse(cover["must_cover"])
        self.assertTrue(cover["allow_overnight"])

    def test_dual_y_path_agrees_buy_then_sell(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": 20.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "buy_then_sell")
        self.assertIn("y_path", out.get("direction_reason") or "")

    def test_dual_y_path_disagree_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": -25.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("异号", out.get("direction_reason") or "")

    def test_dual_y_path_below_enter_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": 10.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("y_path", out.get("direction_reason") or "")
        self.assertIn("未过门槛", out.get("direction_reason") or "")

    def test_dual_y_tau_below_enter_skips(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.5, "y_tau": 0.45},
            cfg={
                "y_tau_enter": 0.60,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        reason = out.get("direction_reason") or ""
        self.assertTrue("横盘" in reason or "入场" in reason or "未过" in reason)

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
        self.assertFalse(acc["ok"])
        self.assertEqual(acc["flat_tau_trades"], 1)
        self.assertEqual(acc["weak_tau_trades"], 0)

        ok = summarize_dual_y_upgrade_acceptance(
            [days[1]], rules={"y_tau_enter": 0.6}
        )
        self.assertTrue(ok["ok"])

    def test_dual_y_path_opposite_sign_skips(self):
        """y_τ 与 y_path 异号 → 跳过。"""
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": -1.1},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 30.0,
                "y_use_path": True,
                "y_path_required": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("异号", out.get("direction_reason") or "")

    def test_dual_y_path_weak_passes_when_path_off(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={"y_trade": 0.3, "y_tau": 0.8, "y_path": 10.0},
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 30.0,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(out.get("skip"))
        self.assertEqual(out.get("direction"), "buy_then_sell")

    def test_dual_y_path_required_no_model(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.8,
                "y_path_status": "no_model",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_path_enter": 15.0,
                "y_use_path": True,
                "y_path_required": True,
                "y_block_tau_nowcast_sign": False,
            },
            cash=100000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("未 promote", out.get("direction_reason") or "")

    def test_dual_y_gap_tier_skips_high_gap_reverse(self):
        from core.t0.score_policy import resolve_dual_y_direction

        out = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.8,
                "gap_pct": 2.5,
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_gap_tier_mode": "skip_opposite",
                "y_gap_tier_pct": 1.5,
                "y_block_tau_nowcast_sign": False,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(out.get("skip"))
        self.assertIn("高开", out.get("direction_reason") or "")

    def test_nowcast_oc_gate_uses_oc_not_cc(self):
        from core.t0.score_policy import _nowcast_oc_pct, resolve_dual_y_direction

        oc = _nowcast_oc_pct(1.0, 2.0)
        self.assertIsNotNone(oc)
        self.assertLess(float(oc), 0.0)
        # CC 与 y_τ 同号，但 OC 异号 → 应跳过
        blocked = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.70,
                "y_nowcast": 1.0,
                "gap_pct": 2.0,
                "nowcast_vs": "prev_close",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": True,
                "y_gap_tier_mode": "off",
                "y_nc_strong": 0.5,
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("y_nc_oc", blocked.get("direction_reason") or "")
        # OC 同号 → 不拦
        ok = resolve_dual_y_direction(
            scores={
                "y_trade": 0.3,
                "y_tau": 0.70,
                "y_nowcast": 2.5,
                "gap_pct": 2.0,
                "nowcast_vs": "prev_close",
            },
            cfg={
                "y_tau_enter": 0.25,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": True,
                "y_gap_tier_mode": "off",
                "y_tau_nowcast_sign_eps": 0.05,
            },
            cash=50000,
            shares=1000,
        )
        self.assertFalse(ok.get("skip"))

    def test_sign_gate_uses_table_y_nc_when_oc_off(self):
        """表列 y_nc 与 y_τ 异号 → OC 关时仍应拦。"""
        from core.t0.score_policy import resolve_dual_y_direction, scores_from_item

        scores = scores_from_item(
            {
                "y_trade": 3.0,
                "y_tau": 0.12,
                "y_eod": 0.08,
                "predicted_score_nowcast": -6.61,
                "nowcast_vs": "prev_close",
                "gap_pct": -10.0,
                "features_tau": {"gap_pct": -10.0},
            }
        )
        self.assertLess(float(scores.get("y_nc") or 0), 0)
        blocked = resolve_dual_y_direction(
            scores=scores,
            cfg={
                "y_tau_enter": 0.02,
                "y_use_path": False,
                "y_block_tau_nowcast_sign": True,
                "y_nowcast_oc_gate": False,
                "y_nc_strong": 1.0,
                "y_gap_tier_mode": "off",
            },
            cash=50000,
            shares=1000,
        )
        self.assertTrue(blocked.get("skip"))
        self.assertIn("y_nc", blocked.get("direction_reason") or "")

    def test_nc_recomputes_from_kalman_not_stale_raw(self):
        """raw predicted_score_nowcast=-0.4 误标 prev_close 时，y_nc 应 Kalman 重算。"""
        from core.t0.score_policy import scores_from_item

        scores = scores_from_item(
            {
                "y_eod": 0.08,
                "y_tau": 0.12,
                "predicted_score_nowcast": -0.40,
                "nowcast_vs": "prev_close",
                "gap_pct": -6.23,
                "features_tau": {"gap_pct": -6.23},
            }
        )
        nc = float(scores.get("y_nc") or 0)
        self.assertLess(nc, -1.0)
        self.assertNotAlmostEqual(nc, -0.40, places=1)

    def test_scores_from_item_persists_y_nc_oc(self):
        from core.t0.score_policy import scores_from_item

        scores = scores_from_item(
            {
                "y_eod": 0.08,
                "y_tau": 0.12,
                "predicted_score_nowcast": -0.40,
                "nowcast_vs": "prev_close",
                "gap_pct": -6.23,
                "features_tau": {"gap_pct": -6.23},
            }
        )
        self.assertIsNotNone(scores.get("y_nc"))
        self.assertIsNotNone(scores.get("y_nc_oc"))
        self.assertGreater(float(scores["y_nc_oc"]), 0)
        self.assertLess(float(scores["y_nc"]), 0)

    def test_path_panel_first_touch_label(self):
        from core.research.path_panel import first_touch_path_label

        mins = [
            {"high": 100.5, "low": 99.8},
            {"high": 102.5, "low": 100.0},
            {"high": 101.0, "low": 98.0},
        ]
        label, reason = first_touch_path_label(
            mins, ref=100.0, sell_trig_pct=2.0, buy_trig_pct=1.5
        )
        self.assertEqual(label, 100.0)
        self.assertEqual(reason, "sell_first")
        label2, _ = first_touch_path_label(
            [{"high": 100.2, "low": 98.0}], ref=100.0, sell_trig_pct=2.0, buy_trig_pct=1.5
        )
        self.assertEqual(label2, -100.0)


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
                "direction_features": {"y_tau": 0.8, "y_path": -20.0, "gap_pct": 0.5},
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
                "direction_features": {"y_tau": 0.6, "gap_pct": 0.2},
            },
        ]
        att = build_y_tau_attribution(days)
        self.assertEqual(att["summary"]["traded_with_tau"], 2)
        self.assertEqual(att["by_oc_hit"]["hit"]["n"], 1)
        self.assertEqual(att["by_oc_hit"]["miss"]["n"], 1)

    def test_y_path_attribution_agree_and_skip(self):
        from core.t0.viz import build_y_path_attribution, classify_t0_skip_reason

        days = [
            {
                "date": "2026-01-10",
                "direction": "buy_then_sell",
                "pnl": 10.0,
                "exposure_pnl": 0,
                "sold_qty": 0,
                "bought_qty": 100,
                "direction_features": {"y_tau": 0.8, "y_path": 20.0},
            },
            {
                "date": "2026-01-11",
                "skipped": True,
                "reason": "dual_y：y_τ=0.800% 与 y_path=-25.000% 异号跳过",
                "skip_category": classify_t0_skip_reason(
                    "dual_y：y_τ=0.800% 与 y_path=-25.000% 异号跳过"
                ),
            },
        ]
        att = build_y_path_attribution(days, path_enter=15.0)
        self.assertEqual(att["summary"]["path_agree_n"], 1)
        self.assertEqual(att["summary"]["path_skip_days"], 1)
        self.assertEqual(att["skip_by_category"][0]["id"], "y_path_disagree")

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
                    "y_tau": 0.5,
                    "y_path": 1.2,
                    "tau_realized": 5.0,
                    "path_realized": 3.0,
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
                    "y_tau": 0.4,
                    "y_path": -1.5,
                    "tau_realized": -5.0,
                    "path_realized": 2.0,
                },
            },
            {
                "date": "2026-01-12",
                "skipped": True,
                "signal_skip": True,
                "reason": "dual_y：y_τ↔y_path异号跳过",
                "scores": {
                    "y_tau": 0.5,
                    "y_path": -0.8,
                    "tau_realized": 1.0,
                    "path_realized": 2.0,
                },
            },
        ]
        port = build_traded_score_portrait(days)
        self.assertEqual(port["n_traded"], 2)
        self.assertEqual(port["label_tau"]["pos"], 1)
        self.assertEqual(port["label_tau"]["neg"], 1)
        self.assertEqual(port["label_path"]["pos"], 2)
        self.assertEqual(port["label_joint"]["same_sign"], 1)
        self.assertEqual(port["label_joint"]["opposite_sign"], 1)
        self.assertEqual(port["pred_joint"]["same_sign"], 1)
        self.assertEqual(port["pred_joint"]["opposite_sign"], 1)
        self.assertEqual(port["tau_hit"]["hit"], 1)
        self.assertEqual(port["tau_hit"]["miss"], 1)
        self.assertEqual(port["path_hit"]["hit"], 1)
        self.assertEqual(port["path_hit"]["miss"], 1)
        self.assertEqual(port["path_by_pred_sign"]["pred_neg"]["miss"], 1)

        all_port = build_backtest_score_portrait(days)
        self.assertEqual(all_port["scope"], "all")
        self.assertEqual(all_port["n_days"], 3)
        self.assertEqual(all_port["n_skipped"], 1)
        self.assertEqual(all_port["pred_joint"]["opposite_sign"], 2)
        self.assertEqual(all_port["path_hit"]["miss"], 2)
        self.assertEqual(all_port["traded"]["n_traded"], 2)
        self.assertEqual(all_port["traded"]["path_hit"]["miss"], 1)

    def test_score_portrait_uses_y_tau_oc_not_remaining(self):
        """τ 命中应对 OC 头；剩余映射后的 y_tau 若反号不得拖垮命中。"""
        from core.t0.viz import build_score_portrait

        # OC ŷ=+1 对；剩余映射后 y_tau=-0.5 若误用会对不上 OC=+2
        days = [
            {
                "date": "2026-02-01",
                "open": 10.0,
                "close": 10.2,
                "scores": {
                    "y_tau": -0.5,
                    "y_tau_oc": 1.0,
                    "y_path": 1.0,
                    "tau_realized": 2.0,
                    "path_realized": 2.0,
                },
            },
            {
                "date": "2026-02-02",
                "skipped": True,
                "scores": {
                    # 旧包仅有剩余ŷ + ret_ot：应还原为 OC ŷ≈+1.5 对上 +2
                    "y_tau": -0.5,
                    "y_path": 0.5,
                    "tau_realized": 2.0,
                    "path_realized": 1.0,
                    "features_tau": {"ret_open_to_tau": 2.01},
                },
            },
        ]
        port = build_score_portrait(days)
        self.assertEqual(port["tau_hit"]["hit"], 2)
        self.assertEqual(port["tau_hit"]["miss"], 0)

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
                sell_trigger_pct=1.0,
                buy_trigger_pct=1.0,
            ),
            compare_optimistic=False,
            stock_code="688047",
        )
        self.assertTrue(report.get("success"))
        self.assertIn("viz", report)
        self.assertIn("skip_categories", report["viz"])
        self.assertIn("daily_activity", report["viz"])

    def test_classify_skip_reason(self):
        from core.t0.viz import classify_t0_skip_reason

        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade（即时算分失败）"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_path（path_ridge 模型未 promote）"),
            "y_path_missing",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：缺 y_eod/y_τ/y_trade 快照"),
            "missing_scores",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116<0.25 横盘跳过"),
            "y_tau_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_τ|=0.116%<0.25% 横盘跳过"),
            "y_tau_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y：|y_τ|=0.450% 弱信号区（0.4%≤|y_τ|<0.6%）跳过"
            ),
            "y_tau_weak",
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
            classify_t0_skip_reason(
                "正T：可卖旧仓 0 股（持仓 500 全被 T+1 锁定），第二腿卖不掉旧仓"
            ),
            "tplus1",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T待固定前缀 2/12"),
            "prefix_segment",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T固定前缀后半上涨 0/2=0%<50%"),
            "prefix_segment",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "固定前缀未确认，放弃正T（正T固定前缀后半上涨 0/2=0%<50%）"
            ),
            "path_abandon",
        )
        self.assertEqual(
            classify_t0_skip_reason("振幅不足 0.30% < 1.00%"),
            "amplitude",
        )
        from core.t0.viz import summarize_skip_reason_label

        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.009%<0.02% 横盘跳过"),
            "y_τ横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_τ|=0.016%<0.02% 横盘跳过"),
            "y_τ横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("正T分钟路径未开成第一腿"),
            "正T未开第一腿",
        )
        self.assertEqual(
            summarize_skip_reason_label("正T分钟路径未触及低吸位"),
            "正T未触低吸",
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
            "y_path_flat",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "y_path_flat",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_path|=1.1<30.0 横盘跳过"),
            "y_path横盘",
        )
        self.assertEqual(
            summarize_skip_reason_label("dual_y：|y_trade|=0.002%<0.01% 未过入场"),
            "y_trade幅度不足",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：|y_trade|=0.085%<0.15% 未过入场"),
            "y_trade_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason("dual_y：y_trade=-0.381%<floor-0.15% 资格不足"),
            "y_trade_weak",
        )
        self.assertEqual(
            classify_t0_skip_reason(
                "dual_y：y_τ=0.450% 与 y_nowcast=-0.300% 异号跳过"
            ),
            "trade_tau_sign",
        )
        self.assertEqual(
            classify_t0_skip_reason("反T上移振幅 0.80%<2.00%"),
            "directional_amplitude",
        )
        self.assertEqual(
            classify_t0_skip_reason("正T：现金不够 1 手（现金 1000 …）"),
            "cash",
        )
        self.assertEqual(
            classify_t0_skip_reason("盘中已有成交腿，跳过整单回放（防重复落账）"),
            "intraday_legs_open",
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
        self.assertEqual(viz["summary"]["y_tau_enter"], 0.25)

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
                "y_tau": 0.132,
                "y_trade": 0.05,
            },
        }
        sc = extract_scores(day)
        self.assertAlmostEqual(sc["y_tau"], 0.132, places=3)
        self.assertAlmostEqual(sc["y_trade"], 0.05, places=3)

        viz = build_t0_viz_payload([day], stock_code="601898")
        scatter = viz.get("y_tau_scatter") or []
        self.assertEqual(len(scatter), 1)
        self.assertAlmostEqual(float(scatter[0]["y_tau"]), 0.132, places=3)
        self.assertEqual(scatter[0]["outcome"], "signal_skip")
        self.assertEqual(scatter[0].get("skip_category"), "y_trade_weak")

    def test_attach_compare_to_viz(self):
        from core.t0.viz import attach_compare_to_viz, build_t0_viz_payload

        report = {
            "t0_pnl_total": 100.0,
            "optimistic_compare": {
                "t0_pnl_total": 150.0,
                "delta_pnl": 50.0,
                "delta_pnl_ratio_pct": 50.0,
            },
            "participate_rate_pct": 12.5,
            "viz": build_t0_viz_payload([]),
        }
        attach_compare_to_viz(report)
        self.assertIn("compare", report["viz"])
        self.assertEqual(report["viz"]["compare"]["optimistic_pnl"], 150.0)
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
        with patch.object(deps.quant, "run_t0_backtest", return_value=mock):
            client = TestClient(web_app.app)
            res = client.post(
                "/api/quant/t0-backtest",
                json={"code": "茅台", "from_paper": False},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

    def test_index_has_t0_controls(self):
        path = os.path.join(ROOT, "web", "static", "partials", "follow_panel.html")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("paper-t0-backtest", text)
        self.assertIn("paper-t0-action-status", text)
        self.assertIn("paper-t0-run", text)
        self.assertIn("paper-t0-confirm", text)
        self.assertIn("paper-t0-worker-enabled", text)
        self.assertIn("paper-t0-worker-m-lastrun", text)
        self.assertIn("paper-t0-worker-trades", text)
        self.assertIn("paper-t0-worker-desk", text)
        self.assertIn("paper-t0-run", text)
        self.assertIn("dual_y", text)
        self.assertIn("手动预演", text)
        self.assertIn("自动做 T 后台进程", text)
        self.assertNotIn("paper-t0-auto-enabled", text)
        self.assertNotIn("paper-t0-auto-run", text)
        self.assertNotIn("启用自动做T", text)
        self.assertNotIn("落账控制", text)
        self.assertIn("持仓操作流水", text)
        self.assertIn("paper-holdings-table", text)
        self.assertNotIn('id="paper-rebalance"', text)
        self.assertNotIn("跑一日", text)


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
                compare_optimistic=False,
            )
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["initial_shares"], m.T0_BT_VIRTUAL_SHARES)
        self.assertEqual(kwargs["initial_cash"], m.T0_BT_VIRTUAL_CASH)
        self.assertIsNone(kwargs["initial_cost"])
        self.assertTrue(out.get("virtual_sizing"))
        self.assertTrue(kwargs.get("skip_quote"))
        self.assertEqual(kwargs.get("stock_name"), "茅台")
        self.assertAlmostEqual(float(out["cumulative_return_pct"]), 0.1)
        self.assertIn("虚拟每票", out.get("scope_label") or "")

    def test_fetch_minute_prefers_stale_local_cache(self):
        from unittest.mock import patch

        from quant.research import t0_backtest as m

        bars = [
            {
                "datetime": "2026-08-20 09:35:00",
                "date": "2026-08-20",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1,
            }
        ] * 12
        meta = {"data_source": "cache", "from_cache": True, "ok": True}
        with patch(
            "skills.common.minute_history._load_stale_minute",
            return_value=(bars, meta),
        ), patch(
            "skills.common.history.resolve_market_code", return_value=("CN", "600519")
        ), patch("core.ports.market.fetch_minute_bars") as remote:
            by_date, out_meta = m._fetch_minute_by_date("600519", period="5")
        remote.assert_not_called()
        self.assertIn("2026-08-20", by_date)
        self.assertTrue(out_meta.get("from_cache"))
        self.assertGreaterEqual(len(by_date["2026-08-20"]), 10)

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
        self.assertFalse(
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
        self.assertFalse(
            should_process_intraday_stock(
                {"phase": "done", "last_bar_ts": "2026-08-24 15:00:00"},
                "2026-08-24 15:00:00",
                force_session_close=True,
            )
        )

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
                "operation_log": [],
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


if __name__ == "__main__":
    unittest.main()
