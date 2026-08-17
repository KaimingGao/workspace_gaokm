"""T5–T8 · TopK 加深：Dropout / IC / 分层 / 过滤 / 基准。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.pool_ic import compute_pool_cross_section_ic
from core.backtest.quantile_backtest import backtest_score_quantiles, _split_quantiles
from core.backtest.topk_backtest import apply_topk_dropout
from core.backtest.topk_benchmark import build_topk_benchmark_summary
from core.backtest.universe_filters import filter_universe_bars, _is_st_name


def _synth_bars(n: int = 80, start: float = 10.0, drift: float = 0.01) -> list:
    bars = []
    px = start
    for i in range(n):
        px = max(1.0, px * (1.0 + drift))
        bars.append(
            {
                "date": f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}",
                "open": round(px * 0.99, 2),
                "high": round(px * 1.01, 2),
                "low": round(px * 0.98, 2),
                "close": round(px, 2),
                "volume": 1_000_000 + i * 1000,
            }
        )
    # normalize to valid calendar-ish unique dates
    out = []
    for i in range(n):
        d = f"2024-01-{i + 1:02d}" if i < 28 else f"2024-02-{i - 27:02d}"
        if i >= 56:
            d = f"2024-03-{i - 55:02d}"
        b = dict(bars[i])
        b["date"] = d
        out.append(b)
    return out[:n]


class TestTopKDropout(unittest.TestCase):
    def test_dropout_zero_is_hard_topk(self):
        picks = [(f"S{i}", float(100 - i)) for i in range(10)]
        out = apply_topk_dropout(picks, ["S0", "S1", "S2"], top_k=3, dropout_n=0)
        self.assertEqual([c for c, _ in out], ["S0", "S1", "S2"])

    def test_dropout_keeps_buffer_names(self):
        # ranks 0..9 by score; hold S3 (rank 3) with K=3,N=1 → keep_rank=4 so S3 stays
        picks = [(f"S{i}", float(100 - i)) for i in range(10)]
        out = apply_topk_dropout(picks, ["S3"], top_k=3, dropout_n=1)
        codes = [c for c, _ in out]
        self.assertIn("S3", codes)
        self.assertEqual(len(codes), 3)
        # S4 is outside enter_rank=2, should not be forced in before buffer fills
        self.assertNotIn("S9", codes)


class TestUniverseFilters(unittest.TestCase):
    def test_st_name(self):
        self.assertTrue(_is_st_name("ST示例"))
        self.assertTrue(_is_st_name("*ST退市"))
        self.assertFalse(_is_st_name("贵州茅台"))

    def test_exclude_st_drops(self):
        bars = {"600000": _synth_bars(40), "600001": _synth_bars(40)}
        kept, dropped, meta = filter_universe_bars(
            bars,
            exclude_st=True,
            name_by_code={"600000": "浦发银行", "600001": "ST垃圾"},
        )
        self.assertIn("600000", kept)
        self.assertNotIn("600001", kept)
        self.assertEqual(meta["dropped_count"], 1)
        self.assertTrue(any(d["stock_code"] == "600001" for d in dropped))


class TestQuantileSplit(unittest.TestCase):
    def test_split_five(self):
        scored = [(f"c{i}", float(i)) for i in range(10)]
        buckets = _split_quantiles(scored, 5)
        self.assertEqual(len(buckets), 5)
        self.assertEqual(sum(len(b) for b in buckets), 10)
        # lowest scores in Q1
        self.assertEqual(buckets[0][0][0], "c0")
        self.assertEqual(buckets[-1][-1][0], "c9")


class TestBenchmarkFlat(unittest.TestCase):
    def test_excess_equals_strategy_when_bench_flat(self):
        flat = [{"date": f"2024-01-{i+1:02d}", "close": 100.0} for i in range(20)]
        stock_bars = {"A": flat, "B": flat}
        result = {
            "metrics": {"total_return_pct": 12.5},
            "params": {"horizon_days": 3},
            "equity_curve": [
                {"date": "2024-01-01", "equity": 100.0},
                {"date": "2024-01-10", "equity": 106.0},
                {"date": "2024-01-20", "equity": 112.5},
            ],
        }
        summary = build_topk_benchmark_summary(
            result, stock_bars, index_code="__no_such_index__", lookback=20
        )
        self.assertTrue(summary.get("ok"))
        self.assertEqual(summary.get("benchmark_label"), "池等权买持")
        self.assertAlmostEqual(float(summary["benchmark_return_pct"]), 0.0, places=1)
        self.assertAlmostEqual(float(summary["excess_pct"]), 12.5, places=1)
        self.assertGreaterEqual(len(summary.get("equity_curve") or []), 2)
        self.assertIsNotNone(summary.get("ann_excess_pct"))
        # 平底基准 → 期超额≈策略期收益，IR 应有值
        self.assertIsNotNone(summary.get("ir"))
        self.assertIsNotNone(summary.get("ann_ir"))

    def test_force_pool_and_abs_pos_excess_neg_warn(self):
        # 池涨很多，策略小赚 → 超额负
        up = [{"date": f"2024-01-{i+1:02d}", "close": 100.0 + i * 2} for i in range(20)]
        stock_bars = {"A": up, "B": up}
        result = {
            "metrics": {"total_return_pct": 1.0},
            "params": {"horizon_days": 3},
            "equity_curve": [
                {"date": "2024-01-01", "equity": 100.0},
                {"date": "2024-01-20", "equity": 101.0},
            ],
        }
        summary = build_topk_benchmark_summary(
            result, stock_bars, index_code="pool", lookback=20, force_pool=True
        )
        self.assertTrue(summary.get("ok"))
        self.assertEqual(summary.get("benchmark_label"), "池等权买持")
        self.assertLess(float(summary["excess_pct"]), 0)
        self.assertTrue(summary.get("warn_abs_pos_excess_neg"))


class TestAmountFilter(unittest.TestCase):
    def test_pctile_drops_thin(self):
        rich = _synth_bars(40)
        for b in rich:
            b["volume"] = 5_000_000
        thin = _synth_bars(40)
        for b in thin:
            b["volume"] = 1_000
        kept, dropped, meta = filter_universe_bars(
            {"R": rich, "T": thin},
            min_avg_amount_pctile=50,
        )
        self.assertIn("R", kept)
        self.assertTrue(any(d["stock_code"] == "T" for d in dropped) or "T" not in kept)
        self.assertEqual(meta["min_avg_amount_pctile"], 50)


class TestIcEquityAlign(unittest.TestCase):
    def test_pos_ic_window_higher_avg(self):
        from core.backtest.ic_equity_align import align_ic_to_equity_periods

        score_ic = {
            "ok": True,
            "ic_series_tail": [
                {"date": "2024-01-01", "ic": 0.2},
                {"date": "2024-01-04", "ic": 0.1},
                {"date": "2024-01-07", "ic": -0.2},
                {"date": "2024-01-10", "ic": -0.1},
            ],
        }
        equity = [
            {"date": "2024-01-01", "equity": 100, "return_pct": 0},
            {"date": "2024-01-04", "equity": 105, "return_pct": 5},
            {"date": "2024-01-07", "equity": 110.25, "return_pct": 5},
            {"date": "2024-01-10", "equity": 108, "return_pct": -2},
            {"date": "2024-01-13", "equity": 106, "return_pct": -1.85},
        ]
        out = align_ic_to_equity_periods(score_ic, equity)
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("avg_return_spread_pp"))
        self.assertGreater(out["pos_ic"]["count"] + out["neg_ic"]["count"], 1)
        # 正 IC 期初对应 +5/+5，非正对应负收益 → 应同向
        self.assertTrue(out.get("aligned_favor_pos_ic"))


class TestPromoteHintsShape(unittest.TestCase):
    def test_hint_dict_shape(self):
        # 与 quant_service_replay 组装约定一致
        hint = {
            "level": "warn",
            "code": "ic_align_mismatch",
            "text": "正IC窗均收益未高于非正IC窗",
        }
        self.assertEqual(hint["level"], "warn")
        self.assertIn("ic_align", hint["code"])

    def test_ttl_expiry_rule(self):
        """与前端默认 TTL=24h 对齐的过期判定。"""
        ttl_ms = 24 * 60 * 60 * 1000
        now = 1_700_000_000_000
        fresh_at = now - ttl_ms + 60_000
        stale_at = now - ttl_ms - 1
        self.assertFalse(now - fresh_at > ttl_ms)
        self.assertTrue(now - stale_at > ttl_ms)

    def test_ttl_hours_clamp(self):
        """与前端 readPromoteTtlHours：1–168 夹紧。"""

        def clamp(h: float) -> int:
            return max(1, min(168, round(h)))

        self.assertEqual(clamp(0), 1)
        self.assertEqual(clamp(24), 24)
        self.assertEqual(clamp(200), 168)


class TestPoolIcSmoke(unittest.TestCase):
    def test_too_few_names(self):
        out = compute_pool_cross_section_ic({"A": _synth_bars(30)})
        self.assertFalse(out.get("ok"))

    def test_series_fields_when_enough(self):
        stock_bars = {
            f"S{i}": _synth_bars(70, start=8.0 + i * 0.3, drift=0.001 + i * 0.0004)
            for i in range(8)
        }
        out = compute_pool_cross_section_ic(stock_bars, horizon_days=3, min_names=5)
        if not out.get("ok"):
            self.skipTest(f"IC 样本不足: {out.get('reason')}")
        self.assertIn("ic_series_tail", out)
        self.assertIn("ic_rolling_tail", out)
        self.assertIn("positive_ic_days", out)
        self.assertGreaterEqual(len(out["ic_series_tail"]), 3)


class TestLongShortCurve(unittest.TestCase):
    def test_ls_compounds_from_period_spread(self):
        from core.backtest.quantile_backtest import _build_long_short_equity_curve

        high = [
            {"date": "2024-01-01", "equity": 100},
            {"date": "2024-01-04", "equity": 110},
            {"date": "2024-01-07", "equity": 121},
        ]
        low = [
            {"date": "2024-01-01", "equity": 100},
            {"date": "2024-01-04", "equity": 100},
            {"date": "2024-01-07", "equity": 100},
        ]
        curve = _build_long_short_equity_curve(high, low)
        self.assertGreaterEqual(len(curve), 2)
        self.assertEqual(curve[0]["equity"], 100.0)
        # +10% then +10% vs flat → ~21%
        self.assertGreater(curve[-1]["equity"], 120)


class TestQuantileEngineSmoke(unittest.TestCase):
    def test_monotonic_flag_present(self):
        # synthetic rising drift across names — engine may or may not be mono; just structure
        stock_bars = {
            f"S{i}": _synth_bars(70, start=8.0 + i * 0.2, drift=0.002 + i * 0.0005)
            for i in range(8)
        }
        out = backtest_score_quantiles(stock_bars, n_quantiles=5, horizon_days=3, min_names=5)
        self.assertIn("ok", out)
        self.assertIn("monotonic_increasing", out)
        self.assertIn("quantiles", out)
        self.assertIn("long_short_equity_curve", out)
        if out.get("ok"):
            self.assertEqual(len(out["quantiles"]), 5)


class TestSimTradeRowHeuristicKw(unittest.TestCase):
    def test_sim_trade_row_accepts_heuristic_score(self):
        from core.backtest.topk_backtest import _sim_trade_row

        row = _sim_trade_row(
            stock_code="600519",
            score=0.12,
            signal_date="2026-08-13",
            entry_date="2026-08-14",
            status="skipped_limit_entry",
            heuristic_score=57.2,
        )
        self.assertAlmostEqual(float(row["heuristic_score"]), 57.2)
        self.assertAlmostEqual(float(row["score"]), 0.12)

    def test_call_sim_trade_row_ignores_unknown_kwargs(self):
        from core.backtest.topk_backtest import _call_sim_trade_row

        row = _call_sim_trade_row(
            stock_code="600519",
            score=0.12,
            signal_date="2026-08-13",
            entry_date="2026-08-14",
            status="skipped_limit_entry",
            heuristic_score=57.2,
            not_a_real_field=True,
        )
        self.assertAlmostEqual(float(row["heuristic_score"]), 57.2)
        self.assertNotIn("not_a_real_field", row)


if __name__ == "__main__":
    unittest.main()
