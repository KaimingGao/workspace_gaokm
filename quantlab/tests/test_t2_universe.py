"""调仓回测候选：显式名单，或股票池（大池 / 沪深300）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestResolveReplayCandidates(unittest.TestCase):
    def test_explicit_codes(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        out = resolve_replay_candidates(["600519", " 600036 "])
        self.assertEqual(out["codes"], ["600519", "600036"])
        self.assertEqual(out["source"], "explicit")

    def test_default_universe_is_csi300(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        hs = [f"{i:06d}" for i in range(1, 301)]
        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=hs,
        ):
            out = resolve_replay_candidates(None)
        self.assertEqual(out["codes"], hs)
        self.assertEqual(out["source"], "hs300")
        self.assertEqual(out["count"], 300)

    def test_blank_feature_set_is_hs300(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        hs = [f"{i:06d}" for i in range(1, 51)]
        with patch(
            "core.validation_universe.load_validation_universe",
        ) as load, patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=hs,
        ):
            out = resolve_replay_candidates(None, feature_set="")
        self.assertEqual(out["source"], "hs300")
        self.assertEqual(out["codes"], hs)
        load.assert_not_called()

    def test_missing_csi300_does_not_fall_back_to_watching(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=[],
        ):
            out = resolve_replay_candidates(None)
        self.assertFalse(out["ok"])
        self.assertEqual(out["source"], "hs300")
        self.assertEqual(out["codes"], [])

    def test_feature_set_pool(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        pool = ["600519", "000001", "000002"]
        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=pool,
        ) as picked:
            out = resolve_replay_candidates(None, feature_set="大池")
        self.assertEqual(out["source"], "pool")
        self.assertEqual(out["codes"], pool)
        picked.assert_called_once_with("pool", fetch=False)

    def test_missing_pool(self):
        from quant.services.quant_service_replay import resolve_replay_candidates

        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=["600519"],
        ):
            out = resolve_replay_candidates(None, feature_set="pool")
        self.assertFalse(out["ok"])
        self.assertEqual(out["source"], "pool")
        self.assertEqual(out["error"], "大池未就绪")


class TestPortfolioBacktestUsesHs300(unittest.TestCase):
    def test_scoring_reads_the_hs300_cube(self):
        from core.universe.feature_sets import active_feature_set
        from quant.services.quant_service_replay import QuantReplayMixin

        seen = {}

        def _replay(*_args, **_kwargs):
            seen["set"] = active_feature_set()
            return {"success": True, "metrics": {}, "params": {}}

        hs = [f"{i:06d}" for i in range(1, 301)]
        bars = {
            hs[0]: [
                {
                    "date": "2024-01-02",
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                }
            ]
        }
        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=hs,
        ), patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            side_effect=_replay,
        ):
            out = QuantReplayMixin().run_portfolio_backtest(
                lookback=5,
                exclude_st=False,
                include_benchmark=False,
                persist_curve=False,
                fill_clock="09:30",
                fetch_fundamentals=False,
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(seen.get("set"), "hs300")
        self.assertEqual(out.get("universe", {}).get("source"), "hs300")
        self.assertEqual(out.get("request", {}).get("feature_set"), "hs300")
        self.assertIn("沪深300", out.get("request", {}).get("score_axis_note", ""))

    def test_scoring_reads_the_pool_cube(self):
        from core.universe.feature_sets import active_feature_set
        from quant.services.quant_service_replay import QuantReplayMixin

        seen = {}

        def _replay(*_args, **_kwargs):
            seen["set"] = active_feature_set()
            return {"success": True, "metrics": {}, "params": {}}

        pool = ["600519", "000001", "000002"]
        bars = {
            pool[0]: [
                {
                    "date": "2024-01-02",
                    "open": 10.0,
                    "close": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                }
            ]
        }
        with patch(
            "core.universe.feature_sets.codes_for_feature_set",
            return_value=pool,
        ), patch(
            "quant.research.portfolio_data.load_portfolio_stock_bars",
            return_value=(bars, [], {}),
        ), patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            side_effect=_replay,
        ):
            out = QuantReplayMixin().run_portfolio_backtest(
                lookback=5,
                exclude_st=False,
                include_benchmark=False,
                persist_curve=False,
                fill_clock="09:30",
                fetch_fundamentals=False,
                feature_set="pool",
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(seen.get("set"), "pool")
        self.assertEqual(out.get("universe", {}).get("source"), "pool")
        self.assertEqual(out.get("request", {}).get("feature_set"), "pool")
        self.assertIn("大池", out.get("request", {}).get("score_axis_note", ""))


if __name__ == "__main__":
    unittest.main()
