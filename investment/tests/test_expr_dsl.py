"""因子表达式 DSL 与表达式因子注册表测试。"""

import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.factors.expr import (
    DSL_EXAMPLES, _BarsCtx, _eval, cross_section_rank_ic, eval_expr, eval_expr_cross_section,
    eval_expr_series, parse_expr,
)
from core.signal.factors.meta.registry import (
    compute_factor, list_expr_factors, register_expr_factor, registered_factor_names, unregister_expr_factor,
)


def _make_bars(n=30, seed=42):
    rng = np.random.RandomState(seed)
    bars = []
    base = 100.0
    for _ in range(n):
        base *= 1 + rng.randn() * 0.02
        bars.append({
            "open": base * 0.99, "high": base * 1.02, "low": base * 0.98,
            "close": base, "volume": 1e6 * (1 + rng.rand()),
        })
    return bars


class TestDSLCore(unittest.TestCase):
    def test_field_and_ref(self):
        bars = _make_bars(10)
        self.assertAlmostEqual(eval_expr("$close", bars), bars[-1]["close"])
        self.assertAlmostEqual(eval_expr("Ref($close, 1)", bars), bars[-2]["close"])
        self.assertAlmostEqual(eval_expr("Ref($close, 9)", bars), bars[0]["close"])

    def test_insufficient_history_returns_nan(self):
        bars = _make_bars(5)
        self.assertTrue(math.isnan(eval_expr("Ref($close, 10)", bars)))
        self.assertTrue(math.isnan(eval_expr("Mean($close, 10)", bars)))

    def test_roc_matches_manual(self):
        bars = _make_bars(30)
        expected = bars[-1]["close"] / bars[-6]["close"] - 1
        self.assertAlmostEqual(eval_expr("ROC($close, 5)", bars), expected)

    def test_delta(self):
        bars = _make_bars(30)
        expected = bars[-1]["close"] - bars[-2]["close"]
        self.assertAlmostEqual(eval_expr("Delta($close, 1)", bars), expected)

    def test_arithmatic(self):
        bars = _make_bars(30)
        b = bars[-1]
        expected = (b["high"] - b["low"]) / b["close"]
        self.assertAlmostEqual(eval_expr("($high - $low) / $close", bars), expected)

    def test_ts_rank_in_range(self):
        bars = _make_bars(30)
        r = eval_expr("Ts_Rank($volume, 20)", bars)
        self.assertTrue(0.0 <= r <= 1.0)

    def test_corr_in_range(self):
        bars = _make_bars(30)
        c = eval_expr("Corr($close, $volume, 10)", bars)
        self.assertTrue(-1.0 <= c <= 1.0)

    def test_series_nan_prefix(self):
        bars = _make_bars(10)
        s = eval_expr_series("ROC($close, 5)", bars)
        self.assertEqual(len(s), 10)
        self.assertTrue(all(math.isnan(x) for x in s[:5]))
        self.assertFalse(any(math.isnan(x) for x in s[5:]))

    def test_cross_section_rank(self):
        # 用不同 seed 保证 ROC 有差异
        bars_low = _make_bars(30, seed=1)
        bars_mid = _make_bars(30, seed=2)
        bars_high = _make_bars(30, seed=3)
        res = eval_expr_cross_section(
            "CS_Rank(ROC($close, 5))",
            {"low": bars_low, "mid": bars_mid, "high": bars_high},
        )
        vals = [res["low"], res["mid"], res["high"]]
        self.assertTrue(all(0 <= v <= 1 for v in vals))
        # 三票排名应互不相同（不同 seed 产生不同 ROC）
        self.assertEqual(len(set(round(v, 6) for v in vals)), 3)

    def test_all_examples_parse(self):
        for ex, _ in DSL_EXAMPLES:
            parse_expr(ex)

    def test_invalid_expr_raises(self):
        with self.assertRaises(ValueError):
            parse_expr("ROC($close,")

    def test_vwap_falls_back_to_typical_price(self):
        bars = _make_bars(6)
        b = bars[-1]
        self.assertAlmostEqual(
            eval_expr("$vwap", bars),
            (b["high"] + b["low"] + b["close"]) / 3.0,
        )
        bars[-1]["vwap"] = 123.0
        self.assertAlmostEqual(eval_expr("$vwap", bars), 123.0)

    def test_vector_matches_scalar(self):
        bars = _make_bars(40)
        exprs = [
            "$close",
            "-$close",
            "Ref($close, 1)",
            "Ref($close, -1)",
            "Ref(1, 5)",
            "ROC($close, 5)",
            "Mean($close, 5)",
            "Mean($close, 1+4)",
            "Std($close, 5)",
            "Var($close, 6)",
            "Delta($close, 3)",
            "Ts_Rank($volume, 10)",
            "RSV($close, 9)",
            "Corr($close, $volume, 10)",
            "Cov($close, $volume, 8)",
            "Mean(ROC($close, 5), 4)",
            "Max($high, 7)",
            "Min($low, 7)",
            "($high - $low) / $close",
            "$close / 0",
            "Ref(Mean($close, 5), 2)",
        ]
        ctx = _BarsCtx(bars)
        for ex in exprs:
            ast = parse_expr(ex)
            series = eval_expr_series(ex, bars)
            for i in range(len(bars)):
                a = _eval(ast, ctx, i)
                b = float(series[i])
                if math.isnan(a) and math.isnan(b):
                    continue
                self.assertAlmostEqual(a, b, places=6, msg=f"{ex} @{i}")

    def test_cross_section_rank_ic_perfect_lookahead(self):
        bars_by = {}
        for k in range(8):
            bars = _make_bars(36, seed=k + 1)
            for i, b in enumerate(bars):
                b["date"] = f"2024-03-{i + 1:02d}" if i < 31 else f"2024-04-{i - 30:02d}"
            bars_by[f"c{k}"] = bars
        res = cross_section_rank_ic("Ref($close, -5) / $close - 1", bars_by, 5)
        self.assertGreater(res["cs_rank_ic"], 0.99)
        self.assertGreaterEqual(res["cs_names"], 5)
        self.assertGreater(res["cs_days"], 0)
        self.assertEqual(len(res["cs_ic_path"]), res["cs_days"])
        self.assertGreater(res["cs_ic_path"][-1]["value"], 0.99)
        self.assertAlmostEqual(res["cs_positive_rate"], 1.0)


class TestExprFactorRegistry(unittest.TestCase):
    def test_register_and_compute(self):
        register_expr_factor("test_expr_roc5", "ROC($close, 5)", label="5日收益")
        self.assertIn("test_expr_roc5", registered_factor_names())
        bars = _make_bars(30)
        score, meta = compute_factor("test_expr_roc5", bars)
        self.assertEqual(score, 50.0)
        self.assertTrue(meta.get("omit_sub_score"))
        self.assertIn("raw_test_expr_roc5", meta)
        expected = bars[-1]["close"] / bars[-6]["close"] - 1
        self.assertAlmostEqual(meta["raw_test_expr_roc5"], expected)
        unregister_expr_factor("test_expr_roc5")

    def test_invalid_expr_rejected(self):
        with self.assertRaises(ValueError):
            register_expr_factor("test_bad", "ROC($close,")

    def test_unregister(self):
        register_expr_factor("test_tmp", "$close")
        names = [f["name"] for f in list_expr_factors()]
        self.assertIn("test_tmp", names)
        self.assertTrue(unregister_expr_factor("test_tmp"))
        names = [f["name"] for f in list_expr_factors()]
        self.assertNotIn("test_tmp", names)


if __name__ == "__main__":
    unittest.main()
