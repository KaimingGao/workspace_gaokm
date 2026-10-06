"""P2++ 专业差距补强：PIT / 撮合 / 归因 / 出站告警 / 行业覆盖。"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch


def _bars(n: int = 40, start: float = 10.0, step: float = 0.1):
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + step
        out.append(
            {
                "date": f"2024-01-{i + 1:02d}" if i < 28 else f"2024-02-{i - 27:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestDataPit(unittest.TestCase):
    def test_window_as_of_no_lookahead(self):
        from core.data.pit import assert_window_pit, window_as_of

        bars = _bars(20)
        window, meta = window_as_of(bars, 10, max_window=5)
        self.assertTrue(meta["ok"])
        self.assertEqual(len(window), 5)
        self.assertEqual(window[-1]["date"], bars[10]["date"])
        chk = assert_window_pit(window, bars[10]["date"])
        self.assertTrue(chk["ok"])
        self.assertEqual(chk["lookahead_bars"], 0)

    def test_bars_as_of_cutoff(self):
        from core.data.pit import bars_as_of

        bars = _bars(10)
        cut = bars[4]["date"]
        got = bars_as_of(bars, cut)
        self.assertEqual(len(got), 5)
        self.assertTrue(all(b["date"] <= cut for b in got))


class TestMatching(unittest.TestCase):
    def test_limit_up_blocks_buy(self):
        from core.backtest.matching import apply_match_filters

        bars = [
            {"date": "2024-01-01", "close": 10.0, "open": 10.0},
            {"date": "2024-01-02", "close": 11.0, "open": 10.5},  # +10%
        ]
        m = apply_match_filters(want_buy=True, entry_bars=bars, entry_index=1)
        self.assertTrue(m["blocked"])
        self.assertEqual(m["reason"], "limit_up_no_buy")

    def test_board_threshold_and_exit_defer(self):
        from core.backtest.matching import (
            apply_match_filters,
            limit_up_threshold_for_code,
            resolve_exit_index,
        )

        self.assertEqual(limit_up_threshold_for_code("600519"), 9.5)
        self.assertEqual(limit_up_threshold_for_code("300750"), 19.5)

        # 创业板 +15% 对主板会判涨停，对创业板不涨停
        bars = [
            {"date": "2024-01-01", "close": 10.0, "open": 10.0},
            {"date": "2024-01-02", "close": 11.5, "open": 10.5},
        ]
        m_main = apply_match_filters(
            want_buy=True, entry_bars=bars, entry_index=1, stock_code="600000"
        )
        m_chi = apply_match_filters(
            want_buy=True, entry_bars=bars, entry_index=1, stock_code="300750"
        )
        self.assertTrue(m_main["blocked"])
        self.assertFalse(m_chi["blocked"])

        # 计划卖出日跌停，次日可卖
        exit_bars = [
            {"date": "2024-01-01", "close": 10.0},
            {"date": "2024-01-02", "close": 9.0},  # -10% 跌停
            {"date": "2024-01-03", "close": 9.1},
        ]
        res = resolve_exit_index(
            exit_bars, 1, respect_limit=True, stock_code="600000", max_defer=2
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["deferred"])
        self.assertEqual(res["exit_index"], 2)

        blocked = apply_match_filters(
            want_buy=False, entry_bars=exit_bars, entry_index=1, stock_code="600000"
        )
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["reason"], "limit_down_no_sell")

    def test_slippage_tier(self):
        from core.backtest.matching import cost_config_for_slippage_tier

        cfg = cost_config_for_slippage_tier("high")
        self.assertEqual(cfg["slippage_tier"], "high")
        self.assertEqual(cfg["base_slippage_bps"], 8.0)


class TestAttribution(unittest.TestCase):
    def test_by_stock_and_sector(self):
        from core.backtest.attribution import attribute_portfolio_trades

        trades = [
            {
                "return_pct": 1.0,
                "legs": [
                    {"stock_code": "600519", "return_pct": 2.0, "sector": "白酒"},
                    {"stock_code": "300750", "return_pct": 0.0, "sector": "新能源"},
                ],
            }
        ]
        attr = attribute_portfolio_trades(trades)
        self.assertTrue(attr["ok"])
        self.assertEqual(attr["leg_count"], 2)
        self.assertTrue(any(x["sector"] == "白酒" for x in attr["by_sector"]))
        self.assertIsNotNone(attr["selection_excess_pct"])


class TestAlertOutbound(unittest.TestCase):
    def test_write_file_and_skip_empty(self):
        from core import alert_outbound as ao

        with tempfile.TemporaryDirectory() as td:
            with patch.object(ao, "ALERTS_DIR", td), patch.object(
                ao, "ALERTS_LAST_PATH", td + "/last.json"
            ):
                empty = ao.dispatch_monitor_alerts([])
                self.assertTrue(empty.get("skipped"))
                out = ao.dispatch_monitor_alerts(
                    [{"level": "warn", "code": "ic_decay", "message": "test"}],
                    source="unit",
                )
                self.assertTrue(out["ok"])
                self.assertTrue(out["path"].endswith(".json"))


class TestEnginePitAndMatch(unittest.TestCase):
    def test_backtest_includes_pit_report(self):
        from core.backtest.engine import backtest_signal_on_bars

        bars = _bars(40, step=0.05)
        # Force some limit-up days by bumping closes
        bars[20]["close"] = bars[19]["close"] * 1.1
        result = backtest_signal_on_bars(
            bars,
            min_score=0,
            min_history=10,
            horizon_days=2,
            respect_limit=True,
            execution_mode="next_open",
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertIn("pit_report", result)
        self.assertTrue(result["pit_report"]["bars_pit"])
        self.assertEqual(result["params"]["execution_mode"], "next_open")
        self.assertIn("skipped_limit_exit", result["params"])
        self.assertIn("exit_deferred", result["params"])

    def test_backtest_limit_off(self):
        from core.backtest.engine import backtest_signal_on_bars

        bars = _bars(40, step=0.05)
        result = backtest_signal_on_bars(
            bars,
            min_score=0,
            min_history=10,
            horizon_days=2,
            respect_limit=False,
            execution_mode="next_open",
        )
        self.assertTrue(result.get("success"), result.get("error"))


class TestPortfolioAttribution(unittest.TestCase):
    def test_portfolio_has_attribution_and_pit(self):
        from core.backtest.topk_backtest import backtest_topk_equal_weight

        stock_bars = {
            "600519": _bars(40, start=100, step=0.2),
            "300750": _bars(40, start=50, step=0.1),
            "601988": _bars(40, start=4, step=0.01),
        }
        result = backtest_topk_equal_weight(
            stock_bars,
            top_k=2,
            min_score=0,
            min_history=10,
            horizon_days=2,
            neutralize=False,
            execution_mode="next_open",
            respect_limit=False,
        )
        self.assertTrue(result.get("success"), result.get("error"))
        self.assertIn("attribution", result)
        self.assertIn("pit_report", result)
        self.assertEqual(result["params"]["execution_mode"], "next_open")


class TestSectorMapCoverage(unittest.TestCase):
    def test_hot_names_mapped(self):
        from core.portfolio_optimize import load_sector_map
        from core.strategy_monitor import sector_coverage_report

        smap = load_sector_map()
        self.assertGreaterEqual(len(smap), 30)
        codes = ["600519", "300750", "601988", "000858", "002594"]
        cov = sector_coverage_report(codes)
        self.assertEqual(cov["coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
