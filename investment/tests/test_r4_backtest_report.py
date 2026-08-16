"""R4 · 回测报告机构化：Brinson lite · 信号成交 · regime 分桶 · 导出。"""

from __future__ import annotations

import unittest


class TestBrinsonAttribution(unittest.TestCase):
    def test_brinson_and_factor_proxy(self):
        from core.backtest.attribution import attribute_portfolio_trades

        trades = [
            {
                "legs": [
                    {"stock_code": "600519", "return_pct": 2.0, "score": 80, "sector": "消费"},
                    {"stock_code": "000858", "return_pct": 1.0, "score": 70, "sector": "消费"},
                ]
            },
            {
                "legs": [
                    {"stock_code": "300750", "return_pct": -1.0, "score": 40, "sector": "新能源"},
                ]
            },
        ]
        out = attribute_portfolio_trades(trades)
        self.assertTrue(out["ok"])
        self.assertTrue(out["brinson"]["ok"])
        self.assertIn("allocation_pct", out["brinson"])
        self.assertIn("selection_pct", out["brinson"])
        self.assertTrue(out["factor_proxy"]["ok"])
        self.assertIsNotNone(out["factor_proxy"]["score_spread_pct"])


class TestRegimeBuckets(unittest.TestCase):
    def test_buckets_from_trades(self):
        from core.backtest.oos_report import regime_buckets_from_trades

        bars = []
        for i in range(40):
            bars.append({"date": f"2024-01-{i+1:02d}" if i < 28 else f"2024-02-{i-27:02d}", "close": 100 + i * 0.5})
        # fix dates to be valid-ish sequential using day numbers
        bars = [{"date": f"2024-03-{i+1:02d}", "close": 100 + (i % 7) * 2} for i in range(28)]
        trades = [
            {"signal_date": "2024-03-20", "return_pct": 1.5},
            {"signal_date": "2024-03-22", "return_pct": -0.5},
            {"signal_date": "2024-03-25", "return_pct": 0.8},
        ]
        out = regime_buckets_from_trades(trades, bars, window=10)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["tagged_trades"], 1)
        self.assertTrue(out["buckets"])


class TestSignalFillAndExport(unittest.TestCase):
    def test_export_markdown_contains_sections(self):
        from quant.services.quant_report_export import (
            build_portfolio_backtest_markdown_lines,
            export_portfolio_backtest_report,
        )

        result = {
            "success": True,
            "loaded_stocks": ["600519", "300750"],
            "metrics": {
                "total_return_pct": 5.0,
                "win_rate_pct": 55.0,
                "max_drawdown_pct": 3.0,
                "trade_count": 4,
            },
            "cost_compare": {"ok": True, "return_gap_pp": -0.4, "avg_impact_bps": 2.1},
            "attribution": {
                "ok": True,
                "selection_excess_pct": 0.2,
                "brinson": {
                    "ok": True,
                    "allocation_pct": 0.1,
                    "selection_pct": 0.05,
                    "interaction_pct": 0.05,
                },
            },
            "pit_report": {"bars_pit": True, "fundamentals_pit": False},
            "oos_summary": {
                "ok": True,
                "failed": True,
                "is_return_pct": 8.0,
                "oos_return_pct": -2.0,
                "fail_reason": "oos_underperform_gap_-10.0pp",
            },
            "regime_summary": {"ok": True, "regime": "chop", "vol": 0.012},
            "regime_buckets": {
                "ok": True,
                "buckets": [{"regime": "chop", "avg_return_pct": 0.5, "trade_count": 3}],
            },
            "signal_fill_sample": [
                {
                    "signal_date": "2024-03-20",
                    "stock_code": "600519",
                    "score": 0.72,
                    "predicted_score": 0.72,
                    "intent_price": 10.0,
                    "fill_price": 10.0,
                    "exit_price": 10.2,
                    "status": "filled",
                },
                {
                    "signal_date": "2024-03-21",
                    "stock_code": "000858",
                    "score": 72.0,
                    "intent_price": 11.0,
                    "fill_price": 11.0,
                    "exit_price": 11.1,
                    "status": "filled",
                },
            ],
        }
        lines = build_portfolio_backtest_markdown_lines(result)
        text = "\n".join(lines)
        self.assertIn("分数口径", text)
        self.assertIn("选股键=ŷ_EOD", text)
        self.assertIn("关 τ 闸", text)
        self.assertIn("ŷ_EOD", text)  # fill table header
        self.assertIn("成本对照", text)
        self.assertIn("Brinson", text)
        self.assertIn("OOS", text)
        self.assertIn("失败", text)
        self.assertIn("信号–成交", text)
        self.assertIn("0.720%", text)
        # heuristic 脏分不得以裸 72 / 72% 出现；标成 H72.0
        self.assertNotRegex(text, r"\| 000858 \| 72(\.0)?%? \|")
        self.assertIn("| 000858 | H72.0 |", text)

        # params 标明 τ 开时 note 切换
        result_tau = dict(result)
        result_tau["params"] = {"apply_tau_buy_gate": True}
        text_tau = "\n".join(build_portfolio_backtest_markdown_lines(result_tau))
        self.assertIn("选股键=ŷ_trade", text_tau)
        self.assertIn("τ 闸开", text_tau)

        exp = export_portfolio_backtest_report(result, fmt="markdown")
        self.assertTrue(exp["success"])
        self.assertIn("KPI", exp["content"])
        self.assertIn("portfolio_backtest_report.md", exp["filename"])
        self.assertIn("分数口径", exp["content"])


if __name__ == "__main__":
    unittest.main()
