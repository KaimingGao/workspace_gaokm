"""ŷ_τc：close[T]/price(τ)−1 标签与 Ridge。"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bar(dkey: str, hm: str, close: float, open_px: float | None = None):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": 1000,
    }


def _times_am_pm():
    out = []
    for total in range(9 * 60 + 30, 11 * 60 + 30 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    for total in range(13 * 60, 15 * 60 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    return out


class YRPctTests(unittest.TestCase):
    def test_y_r_pct_matches_geometry(self):
        from core.research.tau_panel import y_r_pct

        self.assertAlmostEqual(y_r_pct(69.20, 68.12), (68.12 / 69.20 - 1.0) * 100.0, places=6)
        self.assertIsNone(y_r_pct(0, 68.12))
        self.assertIsNone(y_r_pct(69.20, None))


class YRDisplayOnlyTests(unittest.TestCase):
    def test_scores_from_item_passes_y_r(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item(
            {
                "y_tau_oc": 1.2,
                "predicted_score_r": 0.85,
                "y_r_hat": 0.85,
                "y_r": 0.85,
                "r_realized": 0.4,
            }
        )
        self.assertAlmostEqual(sc.get("y_tau"), 1.2)
        self.assertAlmostEqual(sc.get("predicted_score_r"), 0.85)
        self.assertAlmostEqual(sc.get("y_r"), 0.85)
        self.assertAlmostEqual(sc.get("y_τc"), 0.85)
        self.assertAlmostEqual(sc.get("r_realized"), 0.4)

    def test_scores_from_item_does_not_invert_bare_y_r(self):
        from core.t0.score_policy import scores_from_item

        sc = scores_from_item({"y_tau_oc": 0.1, "y_r": -0.8})
        self.assertAlmostEqual(sc.get("y_τc"), -0.8, places=6)
        self.assertAlmostEqual(sc.get("y_r"), -0.8, places=6)

    def test_close_band_enter_does_not_read_y_r(self):
        from core.t0 import close_band

        src = inspect.getsource(close_band.close_band_y_tw_skip_reason)
        self.assertNotIn("y_r", src)
        src2 = inspect.getsource(close_band.blend_y_tw_from_scores)
        self.assertNotIn("y_r", src2)


if __name__ == "__main__":
    unittest.main()
