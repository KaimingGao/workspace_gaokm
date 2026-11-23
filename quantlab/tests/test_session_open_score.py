"""会话日还没有日线时，工厂打分要接上开盘立方那一行。"""

from __future__ import annotations

import unittest

import numpy as np

from core.research.yhat_factory_backtest import (
    _bars_plus_session_open,
    _panel_plus_session_open,
)
from core.research.yhat_transformer.factor_panel import FactorPanel


class _Cube:
    def __init__(self) -> None:
        self.codes = ["000001", "000002"]
        self.names = ["z_oo_volume_price", "gap_pct"]
        self.X_raw = np.array(
            [[1.5, 2.0], [3.0, -1.0]],
            dtype=np.float64,
        )


class TestSessionOpenScoreAttach(unittest.TestCase):
    def test_panel_appends_cube_row_for_missing_day(self) -> None:
        panel = FactorPanel(
            X=np.array([[0.2], [0.4]], dtype=np.float32),
            y=np.array([0.1, 0.2], dtype=np.float32),
            dates=["2026-11-19", "2026-11-20"],
            codes=["000001", "000001"],
            feature_names=["z_oo_volume_price"],
        )
        out = _panel_plus_session_open(panel, "2026-11-23", _Cube())
        self.assertEqual(out.dates[-1], "2026-11-23")
        self.assertEqual(out.codes[-1], "000001")
        self.assertAlmostEqual(float(out.X[-1, 0]), 1.5, places=5)
        self.assertEqual(float(out.y[-1]), 0.0)
        self.assertEqual(panel.dates, ["2026-11-19", "2026-11-20"])

    def test_panel_keeps_existing_day(self) -> None:
        panel = FactorPanel(
            X=np.array([[0.2]], dtype=np.float32),
            y=np.array([0.1], dtype=np.float32),
            dates=["2026-11-23"],
            codes=["000001"],
            feature_names=["z_oo_volume_price"],
        )
        out = _panel_plus_session_open(panel, "2026-11-23", _Cube())
        self.assertIs(out, panel)

    def test_bars_get_open_from_cube_gap(self) -> None:
        bars = {
            "000001": [
                {"date": "2026-11-20", "open": 10.0, "close": 10.0},
            ]
        }
        out = _bars_plus_session_open(bars, "2026-11-23", _Cube())
        last = out["000001"][-1]
        self.assertEqual(last["date"], "2026-11-23")
        self.assertAlmostEqual(last["open"], 10.2, places=5)
        self.assertEqual(len(bars["000001"]), 1)


if __name__ == "__main__":
    unittest.main()
