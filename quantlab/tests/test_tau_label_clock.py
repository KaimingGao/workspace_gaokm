"""标签分母必须精确到 τ，以及窗头键与投票弃权。"""

from __future__ import annotations

import unittest


def _bar(hm: str, close: float) -> dict:
    hh, mm = hm.split(":")
    return {
        "datetime": f"2026-01-06 {hh}:{mm}:00",
        "date": "2026-01-06",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
    }


class TestLabelPriceExact(unittest.TestCase):
    def test_missing_tau_bar_drops_denominator(self):
        from core.research.tau_panel import (
            label_price_at_tau,
            price_at_tau_from_minutes,
        )

        bars = [_bar("09:55", 10.0), _bar("10:25", 11.0), _bar("10:30", 11.2), _bar("10:35", 11.1)]
        loose = price_at_tau_from_minutes(bars, trade_date="2026-01-06", tau_hm="10:00")
        exact = label_price_at_tau(bars, trade_date="2026-01-06", tau_hm="10:00")
        self.assertAlmostEqual(loose, 10.0)
        self.assertIsNone(exact)

    def test_exact_bar_is_that_close(self):
        from core.research.tau_panel import label_price_at_tau

        bars = [_bar("09:55", 10.0), _bar("10:00", 10.4)]
        px = label_price_at_tau(bars, trade_date="2026-01-06", tau_hm="10:00")
        self.assertAlmostEqual(px, 10.4)

    def test_family_and_offsets(self):
        from core.research.lake_bridge import family_for_tc
        from core.signal.minute_tau_grid import (
            T30_LABEL_OFFSETS,
            T45_LABEL_OFFSETS,
            T60_LABEL_OFFSETS,
            T75_LABEL_OFFSETS,
            T90_LABEL_OFFSETS,
        )

        self.assertEqual(family_for_tc(tau_hm="10:00"), "tc_1000")
        self.assertEqual(T30_LABEL_OFFSETS, (25, 30, 35))
        self.assertEqual(T45_LABEL_OFFSETS, (40, 45, 50))
        self.assertEqual(T60_LABEL_OFFSETS, (55, 60, 65))
        self.assertEqual(T75_LABEL_OFFSETS, (70, 75, 80))
        self.assertEqual(T90_LABEL_OFFSETS, (85, 90, 95))


class TestHorizonAbstain(unittest.TestCase):
    def test_cube_must_cover_every_model_name(self):
        from core.t0 import score_policy as sp

        class _Partial:
            names = ["gap_pct", "ret_open_to_tau", "loc_hl"]

        need = ["gap_pct", "ret_open_to_tau", "loc_hl", "raw_ath_GAP"]
        self.assertFalse(sp._cube_covers_horizon_names(_Partial(), need))

        class _Full:
            pass

        _Full.names = list(need)
        self.assertTrue(sp._cube_covers_horizon_names(_Full(), need))

    def test_finalize_zeros_uncovered_raw_ath(self):
        from core.t0.score_policy import _finalize_cs_row

        row = {"gap_pct": 1.2, "raw_ath_GAP": 3.0, "ret_open_to_tau": 0.7}
        done = _finalize_cs_row(row, ["gap_pct", "raw_ath_GAP", "ret_open_to_tau"], ["gap_pct"])
        self.assertIsNotNone(done)
        self.assertEqual(done["raw_ath_GAP"], 0.0)
        self.assertEqual(done["gap_pct"], 1.2)
        self.assertEqual(done["ret_open_to_tau"], 0.7)


if __name__ == "__main__":
    unittest.main()
