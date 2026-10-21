"""开盘面板 ATR / lag 整列与逐日口径对齐。"""

from __future__ import annotations

import unittest

from core.research.tau_panel import (
    GAP_ATR_CLIP,
    GAP_ATR_WINDOW,
    LABEL_LAG_WINDOW,
    gap_atr_from_hist,
    realized_tau_by_date,
    tau_lag_features,
    tau_lag_series_for_open,
)
from core.t0.rules import atr_pct_from_bars, atr_pct_series_before


def _bars(n: int = 80, start: float = 10.0, seed: int = 1):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestTauOpenSeries(unittest.TestCase):
    def test_atr_series_matches_per_day(self) -> None:
        bars = _bars(90, seed=2)
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        closes = [float(b["close"]) for b in bars]
        series = atr_pct_series_before(highs, lows, closes, window=GAP_ATR_WINDOW)
        for i in range(20, len(bars)):
            got = series[i]
            expect = atr_pct_from_bars(bars[:i], GAP_ATR_WINDOW)
            self.assertEqual(got, expect, msg=f"i={i}")

    def test_gap_atr_from_series_matches_hist(self) -> None:
        bars = _bars(90, seed=3)
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        closes = [float(b["close"]) for b in bars]
        atr_s = atr_pct_series_before(highs, lows, closes, window=GAP_ATR_WINDOW)
        for i in range(30, 70):
            gap = 1.5
            atr = atr_s[i]
            if atr is None or float(atr) < 1e-6:
                continue
            gv = float(gap) / float(atr)
            cap = float(GAP_ATR_CLIP)
            got = round(max(-cap, min(cap, gv)), 4)
            window = bars[max(0, i - 62) : i]
            expect = gap_atr_from_hist(gap, window)
            self.assertEqual(got, expect, msg=f"i={i}")

    def test_lag_series_matches_per_day(self) -> None:
        bars = _bars(100, seed=4)
        tau_map = realized_tau_by_date(bars)
        lag1_s, ma5_s = tau_lag_series_for_open(
            bars, tau_map, window=LABEL_LAG_WINDOW, hist_pad=62
        )
        for i in range(30, 90):
            asof = bars[i]["date"]
            window = bars[max(0, i - 62) : i]
            lags = tau_lag_features(
                hist_bars=window, tau_by_date=tau_map, asof_date=asof
            )
            self.assertEqual(lag1_s[i], lags.get("tau_lag1"), msg=f"lag1 i={i}")
            self.assertEqual(ma5_s[i], lags.get("tau_ma5"), msg=f"ma5 i={i}")


if __name__ == "__main__":
    unittest.main()
