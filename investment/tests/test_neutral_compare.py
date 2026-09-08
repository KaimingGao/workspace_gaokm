"""中性化对照 compare / API / golden（合并原 P51·compare / P52 / P53 / P54·summarize / P60·routing）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest.mock import patch
from quant.research.portfolio_neutral_compare import compare_portfolio_neutralization
from tests.test_p10_quant import _aligned_bars
from core.backtest.topk_backtest import backtest_topk_equal_weight
from core.signal.config import load_signal_config
from core.signal.cross_section_batch import score_window_as_item
from core.signal.fundamentals_bridge import fetch_fundamentals_batch
from quant.research.portfolio_data import load_portfolio_stock_bars
from agent.routing import infer_quant_task, prepare_tool_params
from evals.run_checklist import check_routing_expect, load_cases, run_skills
from quant.ops.eval_routing_map import build_eval_routing_map
from quant.research.portfolio_neutral_compare import summarize_portfolio_neutral_compare
from quant.services.quant_report_export import build_report_executive_summary
from quant.services.quant_report_export import build_neutral_compare_export_section, render_quant_report_markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# --- test_p52_quant.py::TestP52PortfolioNeutralCompare ---
class TestP52PortfolioNeutralCompare(unittest.TestCase):
    def test_compare_api_shape(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        out = compare_portfolio_neutralization(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(out["success"])
        self.assertIn("neutralized", out)
        self.assertIn("absolute", out)
        self.assertIn("total_return_pct", out["delta"])

    def test_compare_with_fundamentals_count(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        fundamentals = {"600519": {"pe": 18.0, "roe": 15.0}}
        out = compare_portfolio_neutralization(
            stock_bars,
            fundamentals_by_code=fundamentals,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["fundamentals_count"], 1)

# --- test_p52_quant.py::TestP52NeutralCompareApi ---
class TestP52NeutralCompareApi(unittest.TestCase):
    def test_portfolio_neutral_compare_api(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        res = client.post(
            "/api/quant/portfolio-neutral-compare",
            json={"top_k": 2, "horizon_days": 3, "min_score": 40},
        )
        self.assertEqual(res.status_code, 410, res.text)

# --- p51_nc.py::TestP52NeutralCompare ---
class TestP52NeutralCompare(unittest.TestCase):
    def test_compare_runs_both_strategies(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        out = compare_portfolio_neutralization(
            stock_bars,
            top_k=2,
            horizon_days=3,
            min_score=40,
            min_history=10,
        )
        self.assertTrue(out["success"])
        self.assertTrue(out["neutralized"]["success"])
        self.assertTrue(out["absolute"]["success"])
        self.assertEqual(out["neutralized"]["strategy"], "cross_section_topk_neutral")
        self.assertEqual(out["absolute"]["strategy"], "cross_section_topk")
        self.assertIn("delta", out)
        self.assertIn(out["winner"], ("neutralized", "absolute", "tie"))

# --- test_p53_quant.py::TestP53GoldenNeutralCompare ---
class TestP53GoldenNeutralCompare(unittest.TestCase):
    def test_golden_case_removed(self):
        ids = {c["id"] for c in load_cases()}
        self.assertNotIn("quant_portfolio_neutral_compare", ids)

    def test_infer_falls_back_to_portfolio_backtest(self):
        q = "观察池组合中性化和绝对分回测差多少"
        self.assertEqual(infer_quant_task(q), "portfolio_backtest")
        params = prepare_tool_params("quant", {}, q)
        self.assertEqual(params.get("task"), "portfolio_backtest")

# --- p54_sum.py::TestPortfolioNeutralCompareSummary ---
class TestPortfolioNeutralCompareSummary(unittest.TestCase):
    def test_summarize_portfolio_neutral_compare(self):
        stock_bars = {
            "600519": _aligned_bars("a"),
            "600036": _aligned_bars("b", step=0.35),
            "300750": _aligned_bars("c", step=0.45),
        }
        with patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(stock_bars, [], {}),
        ):
            out = summarize_portfolio_neutral_compare(
                codes=["600519", "600036", "300750"],
                lookback=80,
                top_k=2,
                min_score=40,
                fetch_fundamentals=False,
            )
        self.assertTrue(out["success"])
        self.assertIn(out["winner"], ("neutralized", "absolute", "tie"))
        self.assertIn("delta", out)
        self.assertIsNotNone(out.get("interpretation"))

# --- p60_route.py::TestGoldenDailyNeutralSection ---
class TestGoldenDailyNeutralSection(unittest.TestCase):
    def test_golden_case_exists(self):
        ids = {c["id"] for c in load_cases()}
        self.assertIn("quant_daily_neutral_section", ids)
        self.assertEqual(len(load_cases()), 21)
    def test_routing_expect(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        self.assertEqual(check_routing_expect(case), [])
    def test_infer_daily_summary_for_section_question(self):
        q = "量化日报中性化对照专节包含什么"
        self.assertEqual(infer_quant_task(q), "daily_summary")
        self.assertEqual(prepare_tool_params("quant", {}, q).get("task"), "daily_summary")
    def test_offline_mock_skill_run(self):
        case = next(c for c in load_cases() if c["id"] == "quant_daily_neutral_section")
        run = run_skills(case, use_mock=True)
        result = run["skill_runs"][0]["result"]
        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("task"), "daily_summary")
        nc = result.get("portfolio_neutral_compare_summary") or {}
        self.assertTrue(nc.get("success"))
        self.assertIn(nc.get("winner"), ("neutralized", "absolute", "tie"))
    def test_eval_routing_map_includes_case(self):
        row = next(r for r in build_eval_routing_map()["cases"] if r["id"] == "quant_daily_neutral_section")
        self.assertTrue(row["ok"])
        self.assertEqual(row["inferred"].get("quant_task"), "daily_summary")


if __name__ == "__main__":
    unittest.main()
