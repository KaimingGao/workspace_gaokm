import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.costs import apply_trade_cost, round_trip_cost_pct
from core.backtest.engine import backtest_signal_on_bars, scan_signal_parameters_oos
from core.paper import init_from_example, load_paper, run_daily_cycle, simulate_sells
from core.signal.config import get_stance_thresholds, load_signal_config
from core.signal.factors.relative_strength import excess_return_pct, score_relative_strength
from core.signal.regime import assess_regime
from core.signal.scorer import score_bars
from research.split import time_series_split
from tests.test_signal import _rising_bars, _overheated_bars


def _index_bars(n=25, drift=-0.002):
    bars = []
    price = 3000.0
    for i in range(n):
        price *= 1 + drift
        bars.append(
            {
                "date": f"idx-{i}",
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1_000_000,
            }
        )
    return bars


class TestQuantUpgrade(unittest.TestCase):
    def test_signal_config_load(self):
        cfg = load_signal_config()
        self.assertIn("weights", cfg)
        self.assertAlmostEqual(
            sum(cfg["weights"].values()), 1.0, places=2
        )
        th = get_stance_thresholds(cfg)
        self.assertLess(th["avoid"], th["wait"])

    def test_relative_strength_excess(self):
        stock = _rising_bars()
        index = _index_bars(len(stock), drift=-0.003)
        excess = excess_return_pct(stock, index, window_days=10)
        self.assertIsNotNone(excess)
        rs_score, meta = score_relative_strength(
            stock_bars=stock,
            index_bars=index,
            last_change=1.0,
            fallback_to_last_change=False,
        )
        self.assertEqual(meta["rs_source"], "index_excess")
        self.assertGreater(rs_score, 50)

    def test_factor_contrib_and_regime(self):
        bars = _rising_bars()
        index = _index_bars(len(bars), drift=-0.004)
        result = score_bars(
            bars,
            quote={"change_raw": 1.0},
            index_bars=index,
        )
        self.assertIn("factor_contrib", result)
        self.assertIn("momentum", result["factor_contrib"])
        self.assertIn("regime", result)
        if result["regime"].get("regime") == "weak":
            self.assertGreater(result["factor_contrib"].get("regime_penalty", 0), -6)

    def test_backtest_data_modes(self):
        bars = _rising_bars() + _rising_bars()
        full = backtest_signal_on_bars(bars, min_score=40, min_history=10, data_mode="full")
        fb = backtest_signal_on_bars(
            bars, min_score=40, min_history=10, data_mode="quote_fallback"
        )
        self.assertTrue(full.get("success"))
        self.assertTrue(fb.get("success"))
        self.assertEqual(full["params"]["data_mode"], "full")
        self.assertEqual(fb["params"]["data_mode"], "quote_fallback")

    def test_apply_costs(self):
        self.assertGreater(round_trip_cost_pct(), 0)
        net = apply_trade_cost(5.0)
        self.assertLess(net, 5.0)

    def test_oos_scan(self):
        bars = _rising_bars()
        while len(bars) < 40:
            bars = bars + _rising_bars()
        out = scan_signal_parameters_oos(
            bars,
            min_scores=[40, 50],
            horizon_days_list=[2, 3],
        )
        self.assertTrue(out.get("success"))
        self.assertIn("best_params", out)
        self.assertIn("test", out)

    def test_time_series_split(self):
        sp = time_series_split(100)
        self.assertLess(sp.train_end, sp.valid_end)
        self.assertLess(sp.valid_end, sp.test_end)

    def test_paper_simulate_sells(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper.json")
            init_from_example(path)
            paper = load_paper(path)
            paper["holdings"] = [
                {
                    "stock_code": "600519",
                    "stock_name": "茅台",
                    "shares": 100,
                    "cost": 2000,
                    "bought_at": (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds"),
                }
            ]
            fake_quote = {
                "success": True,
                "stock_code": "600519",
                "price_raw": 1800.0,
            }
            with patch("skills.common.quote_api.StockAPI.query", return_value=fake_quote):
                sells = simulate_sells(paper)
            self.assertGreaterEqual(len(sells), 1)
            self.assertEqual(sells[0]["side"], "sell")

    def test_hard_reject_uses_config(self):
        cfg = load_signal_config(reload=True)
        cfg["hard_reject"]["mom3_gain_max_pct"] = 5
        result = score_bars(_overheated_bars(), config=cfg)
        self.assertTrue(result["hard_reject"])


if __name__ == "__main__":
    unittest.main()
