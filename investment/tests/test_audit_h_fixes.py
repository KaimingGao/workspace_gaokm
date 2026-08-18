"""审计修复回归：H1/H2/H3/H6/H7/H8/H9 等。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars_with_limit_down(*, n: int = 25, down_at: int = -2) -> list:
    bars = []
    price = 10.0
    for i in range(n):
        if i == n + down_at:
            close = price * 0.90  # -10% 跌停
        else:
            close = price * 1.001
        bars.append(
            {
                "date": f"2026-01-{i + 1:02d}",
                "open": price,
                "high": max(price, close) * 1.01,
                "low": min(price, close) * 0.99,
                "close": close,
                "volume": 1_000_000,
                "amount": 1e8,
            }
        )
        price = close
    return bars


class TestH1LimitDownBoost(unittest.TestCase):
    def test_limit_down_raises_limit_score(self):
        from core.signal.factors.reversal import score_reversal

        bars = _bars_with_limit_down()
        score, meta = score_reversal(bars, stock_code="600000")
        self.assertGreaterEqual(meta["rev_limit_down_count"], 1)
        # 跌停抬分：综合分应明显高于无跌停基准
        flat = []
        p = 10.0
        for i in range(25):
            p *= 1.001
            flat.append(
                {
                    "date": f"2026-02-{i + 1:02d}",
                    "open": p,
                    "high": p * 1.01,
                    "low": p * 0.99,
                    "close": p,
                    "volume": 1_000_000,
                    "amount": 1e8,
                }
            )
        score_flat, _ = score_reversal(flat, stock_code="600000")
        self.assertGreater(score, score_flat)


class TestH2ConsecutiveWindow(unittest.TestCase):
    def test_old_limit_up_ignored_outside_window(self):
        from core.signal.factors.reversal import _calc_consecutive_limit_up

        bars = []
        p = 10.0
        # 开头连 3 板，之后很久无涨停
        for i in range(40):
            if 1 <= i <= 3:
                close = p * 1.10
            else:
                close = p * 1.001
            bars.append(
                {
                    "date": f"2025-01-{i + 1:02d}" if i < 28 else f"2025-02-{i - 27:02d}",
                    "open": p,
                    "high": close,
                    "low": p * 0.99,
                    "close": close,
                    "volume": 1e6,
                }
            )
            p = close
        self.assertEqual(
            _calc_consecutive_limit_up(bars, window=20, stock_code="600000"), 0
        )
        self.assertGreaterEqual(
            _calc_consecutive_limit_up(bars, window=40, stock_code="600000"), 3
        )


class TestH3BoardAwareLimits(unittest.TestCase):
    def test_st_and_chinext_thresholds(self):
        from core.backtest.matching import (
            is_limit_up,
            limit_up_threshold_for_code,
        )

        self.assertAlmostEqual(limit_up_threshold_for_code("600000"), 9.5)
        self.assertAlmostEqual(limit_up_threshold_for_code("300001"), 19.5)
        self.assertAlmostEqual(
            limit_up_threshold_for_code("600001", stock_name="ST示例"), 4.5
        )
        # ST 真涨停 5% 应判定
        self.assertTrue(
            is_limit_up(10.0, 10.5, stock_code="600001", stock_name="*ST退")
        )
        # 科创 15% 不应判涨停
        self.assertFalse(is_limit_up(10.0, 11.5, stock_code="688001"))
        self.assertTrue(is_limit_up(10.0, 12.0, stock_code="688001"))


class TestH6LegacyAnnMissing(unittest.TestCase):
    def test_legacy_snapshot_marks_ann_missing(self):
        from core.fundamentals_pit import load_fundamentals_panel

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "f.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    '{"code":"600000","fetched_at":"2024-04-10",'
                    '"data":{"pe":10,"roe":5,"as_of":"2024-03-31"}}'
                )
            with patch(
                "core.fundamentals_pit.snapshot_cache_path",
                return_value=path,
            ):
                panel = load_fundamentals_panel("600000")
            hist = panel.get("history") or []
            self.assertTrue(hist)
            self.assertTrue(hist[0].get("ann_missing"))
            self.assertTrue(hist[0].get("non_pit_origin"))

    def test_legacy_with_ann_date_not_missing(self):
        from core.fundamentals_pit import load_fundamentals_panel

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "f.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    '{"code":"600000","fetched_at":"2024-04-30",'
                    '"data":{"pe":10,"roe":5,"as_of":"2024-03-31",'
                    '"ann_date":"2024-04-28"}}'
                )
            with patch(
                "core.fundamentals_pit.snapshot_cache_path",
                return_value=path,
            ):
                panel = load_fundamentals_panel("600000")
            hist = panel.get("history") or []
            self.assertTrue(hist)
            self.assertFalse(hist[0].get("ann_missing"))
            self.assertEqual(hist[0].get("available_as_of"), "2024-04-28")


class TestH8RegimeConfig(unittest.TestCase):
    def test_weak_threshold_from_config(self):
        from core.signal.regime import assess_regime

        # 指数近 20 日约 -4%：默认 weak；若阈值改成 -5 则应 neutral
        bars = []
        p = 100.0
        for i in range(21):
            p *= 0.998  # ~ -0.2%/日 → 20 日约 -4%
            bars.append({"date": f"2026-01-{i+1:02d}", "close": p})
        weak = assess_regime(bars, {"enabled": True, "weak_trend_threshold_pct": -3.0})
        self.assertEqual(weak["regime"], "weak")
        neutral = assess_regime(
            bars, {"enabled": True, "weak_trend_threshold_pct": -5.0}
        )
        self.assertEqual(neutral["regime"], "neutral")


class TestH9FloorValidation(unittest.TestCase):
    def test_hold_above_buy_clamped(self):
        from core.signal.config import _validate_scoring_floors

        cfg = {
            "scoring": {
                "min_predicted_score": 1.0,
                "min_hold_predicted_score": 2.0,
            }
        }
        warns = _validate_scoring_floors(cfg)
        self.assertTrue(warns)
        self.assertEqual(cfg["scoring"]["min_hold_predicted_score"], 1.0)


class TestH7IdioAlign(unittest.TestCase):
    def test_common_dates_only(self):
        from core.signal.factors.idio_momentum import _aligned_returns

        stock = [
            {"date": f"2026-01-{i:02d}", "close": 10 + i * 0.1} for i in range(1, 25)
        ]
        # 指数缺后半段 → 只能用共同日
        index = [
            {"date": f"2026-01-{i:02d}", "close": 3000 + i} for i in range(1, 15)
        ]
        ys, xs = _aligned_returns(stock, index, window=20)
        self.assertEqual(len(ys), len(xs))
        self.assertLessEqual(len(ys), 13)


if __name__ == "__main__":
    unittest.main()
