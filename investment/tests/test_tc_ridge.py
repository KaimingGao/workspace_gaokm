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

    def test_relabel_keeps_price_tau_rows(self):
        from core.research.tau_panel import relabel_tau_panels_as_r, y_r_pct

        yr = y_r_pct(10.5, 10.0)
        panel = {
            "xs": [
                {"gap_pct": 1.0},
                {"gap_pct": 0.5},
                {"gap_pct": -0.2},
                {"gap_pct": 0.1},
                {"gap_pct": 0.3},
            ],
            "ys": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dates": [
                "2025-06-02",
                "2025-06-03",
                "2025-06-04",
                "2025-06-05",
                "2025-06-06",
            ],
            "metas": [
                {"price_tau": 10.5, "close": 11.0, "close_minute": 10.0},
                {"price_tau": 9.8, "close": 11.0, "close_minute": 10.0},
                {"price_tau": None, "close": 11.0, "close_minute": 10.0},
                {"price_tau": 10.2, "close": 11.0, "close_minute": 10.1},
                {"price_tau": 10.1, "close": 11.0, "close_minute": 10.0},
            ],
        }
        out = relabel_tau_panels_as_r([panel])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["ys"]), 4)
        self.assertAlmostEqual(out[0]["ys"][0], yr, places=6)
        self.assertIn("y_r", out[0]["metas"][0])

    def test_relabel_drops_daily_close_only_rows(self):
        from core.research.tau_panel import relabel_tau_panels_as_r

        panel = {
            "xs": [{"gap_pct": 1.0}] * 5,
            "ys": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dates": [f"2025-06-0{i}" for i in range(2, 7)],
            "metas": [{"price_tau": 10.0, "close": 11.0} for _ in range(5)],
        }
        self.assertEqual(relabel_tau_panels_as_r([panel]), [])


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
