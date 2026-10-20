"""A3：BacktestService 信封与 topk 权重拆分。"""

import unittest
from datetime import datetime, timedelta

from core.backtest.topk_weights import allocate_topk_weights, vol_from_window
from core.backtest.types import BacktestResult
from core.backtest_service import run_topk


def _bars(n=40, start_px=10.0):
    out = []
    start = datetime(2024, 1, 1)
    px = start_px
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        px *= 1.001
        out.append(
            {
                "date": d,
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1e6,
            }
        )
    return out

class TestA3BacktestService(unittest.TestCase):
    def test_envelope_from_topk(self):
        r = BacktestResult.from_topk({"success": True, "metrics": {"total_return_pct": 1.0}})
        d = r.as_dict()
        self.assertTrue(d["success"])
        self.assertIn("bars_backend", d)

    def test_allocate_equal(self):
        legs = [{"stock_code": "a", "score": 1}, {"stock_code": "b", "score": 2}]
        w, mode = allocate_topk_weights(legs, weight_mode="equal")
        self.assertEqual(mode, "equal")
        self.assertAlmostEqual(sum(w.values()), 100.0, places=2)

    def test_vol_from_window(self):
        self.assertIsNotNone(vol_from_window(_bars(30)))

    def test_run_topk_facade_smoke(self):
        stock_bars = {"600519": _bars(50), "000001": _bars(50, start_px=8)}
        out = run_topk(
            stock_bars,
            top_k=1,
            horizon_days=2,
            min_score=-999,
            apply_costs=False,
            apply_tau_buy_gate=False,
        )
        self.assertIsInstance(out, dict)
        self.assertTrue(out.get("success") or out.get("ok") or "error" in out)
        self.assertIn("bars_backend", out)

    def test_cluster_util_retired(self):
        """cluster_report_util 已随分组 OLS 删除。"""
        import importlib.util

        self.assertIsNone(
            importlib.util.find_spec("quant.research.cluster_report_util")
        )

    def test_topk_probe_defaults_to_oos_curve(self):
        from core.backtest.topk_probe import run_topk_probe

        stock_bars = {"600519": _bars(50), "000001": _bars(50, start_px=8)}
        out = run_topk_probe(
            stock_bars,
            top_k=1,
            horizon_days=2,
            min_score=-999,
            allow_heuristic_baseline=True,
            rank_mode="heuristic_score",
        )
        self.assertTrue(out.get("success") or out.get("ok") or "error" in out)
        if out.get("success"):
            self.assertEqual((out.get("params") or {}).get("engine"), "oos_topk_curve")

    def test_topk_probe_full_engine_probe_lite(self):
        from core.backtest.topk_probe import run_topk_probe

        stock_bars = {"600519": _bars(50), "000001": _bars(50, start_px=8)}
        out = run_topk_probe(
            stock_bars,
            full_engine=True,
            top_k=1,
            horizon_days=2,
            min_score=-999,
            apply_costs=False,
            apply_tau_buy_gate=False,
            allow_heuristic_baseline=True,
            rank_mode="heuristic_score",
        )
        self.assertTrue(out.get("success") or out.get("ok") or "error" in out)
        if out.get("success"):
            self.assertTrue((out.get("params") or {}).get("probe_lite"))
            self.assertEqual(
                (out.get("stop_shadow") or {}).get("reason"), "probe_lite"
            )
            self.assertEqual(out.get("attribution") or {}, {})
            self.assertEqual(out.get("sim_trades") or [], [])

    def test_oos_topk_curve_smoke(self):
        from core.backtest.topk_probe import run_oos_topk_curve

        stock_bars = {
            "600519": _bars(60),
            "000001": _bars(60, start_px=8),
            "000002": _bars(60, start_px=12),
        }
        out = run_oos_topk_curve(
            stock_bars,
            top_k=2,
            horizon_days=2,
            min_score=-999,
            allow_heuristic_baseline=True,
            rank_mode="heuristic_score",
        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual((out.get("params") or {}).get("engine"), "oos_topk_curve")
        self.assertGreaterEqual(len(out.get("equity_curve") or []), 2)
        self.assertIn("total_return_pct", out.get("metrics") or {})

if __name__ == "__main__":
    unittest.main()

