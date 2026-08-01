"""D1–D6 平台能力：Observation / Job / Memory / Decision / Feedback / Prefill。"""

from __future__ import annotations

import os
import tempfile
import unittest

from core.decision_record import build_decision_record, list_decisions, record_from_advice
from core.feedback_suggest import suggest_config_feedback
from core.job_progress import JobRegistry, paper_job
from core.memory_store import (
    clamp_horizon_days,
    effective_preferences,
    read_memory,
    write_memory,
)
from core.observation import make_observation, wrap_skill_result
from core.order_prefill import build_prefills_from_decisions, export_prefill_bundle


class TestD1ObservationJobs(unittest.TestCase):
    def test_observation_envelope(self):
        obs = make_observation(
            source="test",
            kind="unit",
            success=True,
            data={"x": 1},
            summary="ok",
        )
        self.assertTrue(obs["ok"])
        self.assertTrue(obs["success"])
        self.assertEqual(obs["data"]["x"], 1)
        wrapped = wrap_skill_result("quote", {"success": True, "summary": "茅台"})
        self.assertEqual(wrapped["meta"]["tool"], "quote")

    def test_job_registry_slots(self):
        reg = JobRegistry()
        a = reg.slot("paper")
        b = reg.slot("schedule")
        self.assertIsNot(a, b)
        jid = a.start(kind="t", total=2, message="go")
        self.assertTrue(jid)
        self.assertTrue(a.is_running())
        a.update(current=1, message="half")
        a.finish(result={"ok": True})
        self.assertEqual(a.get()["status"], "done")
        self.assertEqual(paper_job.name, "paper")


class TestD2Memory(unittest.TestCase):
    def test_memory_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "memory.json")
            out = write_memory({"risk_style": "conservative", "horizon_days": 5}, path=path)
            self.assertTrue(out.get("exists"))
            self.assertEqual(out["preferences"]["risk_style"], "conservative")
            again = read_memory(path)
            self.assertEqual(again["preferences"]["horizon_days"], 5)

    def test_clamp_horizon_and_effective(self):
        self.assertEqual(clamp_horizon_days(0), 1)
        self.assertEqual(clamp_horizon_days(99), 10)
        self.assertEqual(clamp_horizon_days("x", default=3), 3)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "memory.json")
            out = write_memory({"horizon_days": 99, "risk_style": "nope"}, path=path)
            self.assertEqual(out["preferences"]["horizon_days"], 10)
            self.assertEqual(out["preferences"]["risk_style"], "balanced")
            self.assertEqual(out["effective"]["horizon_days"], 10)
            eff = effective_preferences(path)
            self.assertEqual(eff["horizon_days"], 10)
            self.assertTrue(eff["memory_exists"])
            missing = effective_preferences(os.path.join(td, "absent.json"))
            self.assertEqual(missing["horizon_days"], 3)
            self.assertFalse(missing["memory_exists"])


class TestD3Decision(unittest.TestCase):
    def test_decision_record_persist(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "decisions.jsonl")
            advice = {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "horizon_days": 3,
                "stance_code": "wait",
                "stance_label": "观望",
                "confidence": 0.5,
                "reasons": ["单测"],
                "invalidation": {"stop_pct": 0.03},
                "facts": {},
            }
            rec = build_decision_record(advice, source="test")
            self.assertEqual(rec["stance_label"], "观望")
            out = record_from_advice(advice, source="test", persist=True, path=path)
            self.assertTrue(out["ok"])
            listed = list_decisions(limit=10, path=path)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["items"][0]["stock_code"], "600519")


class TestD4Feedback(unittest.TestCase):
    def test_feedback_suggest_drawdown(self):
        out = suggest_config_feedback(backtest_metrics={"max_drawdown_pct": 15, "win_rate_pct": 40})
        self.assertTrue(out["success"])
        self.assertFalse(out["auto_apply"])
        self.assertTrue(out["patch"])
        self.assertIn("rank", out["patch"])


class TestD6Prefill(unittest.TestCase):
    def test_prefill_export(self):
        rows = build_prefills_from_decisions(
            [
                {"id": "1", "stock_code": "1", "stock_name": "A", "stance_label": "试探性关注"},
                {"id": "2", "stock_code": "2", "stock_name": "B", "stance_label": "观望"},
                {"id": "3", "stock_code": "3", "stock_name": "C", "stance_label": "减仓"},
            ]
        )
        self.assertEqual(len(rows), 2)
        bundle = export_prefill_bundle(rows, fmt="csv")
        self.assertIn("csv", bundle)
        self.assertIn("非交易指令通道", bundle["disclaimer"])


if __name__ == "__main__":
    unittest.main()
