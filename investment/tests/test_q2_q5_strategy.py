"""Q2–Q4：StrategySpec / promote / risk / manifest / 默认成本。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestStrategySpec(unittest.TestCase):
    def test_conservative_lifecycle(self):
        from core.strategy import get_strategy_spec

        spec = get_strategy_spec("short_conservative")
        self.assertEqual(spec["strategy_id"], "short_conservative")
        self.assertEqual(spec["version"], "1.2.0")
        self.assertEqual(spec["cost_model"], "simple_cn")
        self.assertNotIn("min_score", spec["paper_rules"])
        self.assertIn("max_positions", spec["paper_rules"])
        self.assertIn("max_drawdown_pct", spec["risk"])
        self.assertIn("execution", spec)
        self.assertIn("t0", (spec["execution"].get("overlays") or {}))

    def test_promote_writes_file(self):
        from core.strategy import load_promoted, promote_strategy

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "strategy_promoted.json")
            entry = promote_strategy("short_conservative", note="unit", path=path)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(entry["spec"]["strategy_id"], "short_conservative")
            loaded = load_promoted(path)
            self.assertEqual(loaded["note"], "unit")


class TestRiskGate(unittest.TestCase):
    def test_drawdown_blocks(self):
        from core.risk import check_account_risk

        paper = {"strategy_id": "short_conservative", "cash": 100000, "holdings": [], "cost_model": "simple_cn"}
        gate = check_account_risk(
            paper,
            {"equity": 80000, "max_drawdown_pct": 25.0, "holdings": []},
        )
        self.assertFalse(gate["ok"])
        self.assertTrue(any("回撤" in b for b in gate["blocks"]))

    def test_ok_when_healthy(self):
        from core.risk import check_account_risk

        paper = {"strategy_id": "short_conservative", "cash": 100000, "holdings": [], "cost_model": "simple_cn"}
        gate = check_account_risk(
            paper,
            {"equity": 100000, "max_drawdown_pct": 5.0, "holdings": []},
        )
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["blocks"], [])


class TestRunManifest(unittest.TestCase):
    def test_write_manifest(self):
        from core.run_manifest import build_run_manifest, write_run_manifest

        with tempfile.TemporaryDirectory() as td:
            m = build_run_manifest(
                kind="paper_rebalance",
                strategy_id="short_conservative",
                strategy_version="1.0.0",
                cost_model="simple_cn",
                rules={"min_score": 55},
            )
            path = write_run_manifest(m, dir_path=td)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["fingerprint"], m["fingerprint"])
            self.assertEqual(data["strategy_id"], "short_conservative")


class TestDefaultCost(unittest.TestCase):
    def test_example_and_load_default_simple_cn(self):
        from core.paper import EXAMPLE_PATH, load_paper
        from core.paths import ROOT_DIR

        with open(EXAMPLE_PATH, encoding="utf-8") as f:
            ex = json.load(f)
        self.assertEqual(ex.get("cost_model"), "simple_cn")

        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "paper.json")
            payload = {
                "cash": 1000,
                "holdings": [],
                "trades": [],
                "signal_log": [],
                "snapshots": [],
                "rules": {},
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            paper = load_paper(p)
            self.assertEqual(paper["cost_model"], "simple_cn")


class TestDeepAnalysisRouting(unittest.TestCase):
    def test_buy_not_deep(self):
        from agent.routing import is_analysis_question

        self.assertFalse(is_analysis_question("茅台能不能买"))
        self.assertTrue(is_analysis_question("请对茅台做深度分析"))


if __name__ == "__main__":
    unittest.main()
