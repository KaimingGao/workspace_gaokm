"""AO 轨：ops Skill / AgentOpsService 契约与安全闸。"""

import json
import os
import tempfile
import unittest
from unittest import mock

from services.agent_ops import (
    AVAILABLE_TASKS,
    OPS_SNAPSHOT_KEYS,
    AgentOpsService,
    truncate_tool_json_for_llm,
)


class TestAgentOpsContracts(unittest.TestCase):
    def test_available_tasks(self):
        self.assertIn("ops_snapshot", AVAILABLE_TASKS)
        self.assertIn("rebalance_preview", AVAILABLE_TASKS)
        self.assertIn("prior_brief", AVAILABLE_TASKS)
        self.assertIn("view_bundle", AVAILABLE_TASKS)

    def test_empty_task_lists_available(self):
        out = AgentOpsService().run({})
        self.assertFalse(out["success"])
        self.assertEqual(out["available_tasks"], list(AVAILABLE_TASKS))

    def test_ops_snapshot_missing_paper(self):
        svc = AgentOpsService(paper_path="/tmp/quantlab_no_such_paper.json")
        out = svc.ops_snapshot({})
        self.assertFalse(out["success"])
        self.assertIn("不存在", out["error"])

    def test_ops_snapshot_empty_report(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cash": 1e6, "holdings": [], "operation_log": []}, f)
            out = AgentOpsService(paper_path=path).ops_snapshot({})
            self.assertFalse(out["success"])
            self.assertIn("last_ops_report", out["error"])

    def test_ops_snapshot_ok(self):
        fixture = os.path.join(
            os.path.dirname(__file__), "fixtures", "agent_ops", "ops_snapshot_min.json"
        )
        with open(fixture, encoding="utf-8") as f:
            ops = json.load(f)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cash": 1e6,
                        "holdings": [],
                        "operation_log": [],
                        "last_ops_report": ops,
                    },
                    f,
                )
            out = AgentOpsService(paper_path=path).ops_snapshot({})
            self.assertTrue(out["success"])
            for k in OPS_SNAPSHOT_KEYS:
                self.assertIn(k, out["ops_report"])
            self.assertEqual(len(out["five_questions"]), 5)

    def test_preview_rejects_execute(self):
        out = AgentOpsService().rebalance_preview(
            {"path": "holding_cycle", "dry_run": False}
        )
        self.assertFalse(out["success"])
        self.assertIn("dry_run", out["error"])

    def test_preview_rejects_bad_path(self):
        out = AgentOpsService().rebalance_preview({"path": "live_oms"})
        self.assertFalse(out["success"])
        self.assertIn("未知 path", out["error"])

    def test_preview_holding_cycle_mocked(self):
        fake = {
            "ok": True,
            "preview": True,
            "buy_trades": [{"stock_code": "600519", "shares": 100}],
            "risk_blocks": ["single_name"],
            "buys_blocked": True,
            "ops_report": {
                "strategy_id": "short_conservative",
                "strategy_version": "t",
                "cost_model": "simple_cn",
                "data_quality": {},
                "risk_blocks": ["single_name"],
                "monitor_alerts": [],
                "buys_blocked": True,
                "fallback_count": 0,
            },
        }
        with mock.patch.object(AgentOpsService, "_preview_holding_cycle", return_value=fake):
            out = AgentOpsService().rebalance_preview({"path": "holding_cycle"})
        self.assertTrue(out["success"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["path"], "holding_cycle")
        self.assertTrue(out["buys_blocked"])
        self.assertIn("preview_fingerprint", out)

    def test_feedback_draft_has_checks(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cash": 1e6,
                        "holdings": [],
                        "operation_log": [],
                        "last_ops_report": {
                            "strategy_id": "short_conservative",
                            "strategy_version": "t",
                            "cost_model": "simple_cn",
                            "data_quality": {},
                            "risk_blocks": [],
                            "monitor_alerts": [{"code": "ic_decay", "message": "ic"}],
                            "buys_blocked": False,
                            "fallback_count": 0,
                        },
                    },
                    f,
                )
            out = AgentOpsService(paper_path=path).feedback_draft({})
            self.assertTrue(out["success"])
            self.assertFalse(out["applied"])
            self.assertTrue(out["suggested_checks"])
            self.assertEqual(out.get("scope"), "strategy")

    def test_prior_brief_fields(self):
        fake_summary = {
            "freshness": {"stale": False, "any_stale": False},
            "macro": {"overseas_tech_1d_pct": -2.1, "a50_1d_pct": -0.5},
            "sentiment": {"sentiment_cycle_score": 0.4},
            "regulatory": {"active": False, "count": 0},
            "ipo": {"extreme_ipo_day": False},
            "prior_active": True,
            "prior_warnings": ["跨市场·海外科技隔夜 -2%"],
            "prior_flags": {
                "cross_market": True,
                "market_sentiment": False,
                "regulatory": False,
                "ipo_drain": False,
            },
        }
        with mock.patch(
            "core.market.context.summarize_market_context",
            return_value=fake_summary,
        ):
            with mock.patch(
                "core.signal.live_features.fetch_live_index_bars",
                side_effect=RuntimeError("skip regime"),
            ):
                out = AgentOpsService().prior_brief({})
        self.assertTrue(out["success"])
        self.assertEqual(out["task"], "prior_brief")
        self.assertTrue(out["prior_active"])
        self.assertIn("checklist", out)
        self.assertEqual(len(out["checklist"]), 4)
        self.assertEqual(out["impact_note"], "不进 ŷ；仅纸面买/持仓缩放")
        self.assertIn("cross_market", out["prior_flags"])

    def test_feedback_draft_market_prior_scope(self):
        fake_summary = {
            "freshness": {},
            "macro": {},
            "sentiment": {},
            "regulatory": {},
            "ipo": {},
            "prior_active": True,
            "prior_warnings": ["跨市场·海外科技隔夜 -2%"],
            "prior_flags": {
                "cross_market": True,
                "market_sentiment": False,
                "regulatory": False,
                "ipo_drain": False,
            },
        }
        fake_cfg = {
            "cross_market": {"mode": "scale", "scale_buy_pct": 0.5},
            "market_sentiment_prior": {"mode": "off", "scale_buy_pct": 0.55},
            "regulatory_prior": {"mode": "off", "scale_buy_pct": 0.5},
            "ipo_drain_prior": {"mode": "off", "scale_buy_pct": 0.45},
            "market_prior_policy": {"merge_mode": "min_scale"},
        }
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cash": 1e6, "holdings": [], "trades": [], "operation_log": []}, f)
            with mock.patch(
                "core.market.context.summarize_market_context",
                return_value=fake_summary,
            ):
                with mock.patch(
                    "core.signal.market_prior_config.read_market_prior_public",
                    return_value=fake_cfg,
                ):
                    out = AgentOpsService(paper_path=path).feedback_draft(
                        {"scope": "market_prior"}
                    )
        self.assertTrue(out["success"])
        self.assertEqual(out["scope"], "market_prior")
        self.assertFalse(out["applied"])
        self.assertTrue(out["suggested_checks"])
        draft = out["draft"]
        self.assertEqual(draft["scope"], "market_prior")
        keys = {s["key"] for s in draft["suggestions"]}
        self.assertTrue(any(k.startswith("cross_market") for k in keys))
        for k in draft["suggestions"]:
            self.assertNotIn("weights", k["key"])
            self.assertNotIn("scoring", k["key"])

    def test_post_rebalance_review(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "paper.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cash": 1e6,
                        "holdings": [],
                        "operation_log": [],
                        "last_ops_report": {
                            "strategy_id": "short_conservative",
                            "strategy_version": "t",
                            "cost_model": "simple_cn",
                            "data_quality": {"fallback_count": 2},
                            "risk_blocks": [],
                            "monitor_alerts": [],
                            "buys_blocked": False,
                            "fallback_count": 2,
                        },
                    },
                    f,
                )
            out = AgentOpsService(paper_path=path).post_rebalance_review({})
            self.assertTrue(out["success"])
            self.assertTrue(any("fallback" in c for c in out["concerns"]))
            self.assertTrue(out["do_not_auto_patch"])

    def test_alpha_draft_requires_idea(self):
        out = AgentOpsService().alpha_draft({})
        self.assertFalse(out["success"])

    def test_alpha_draft_ok(self):
        out = AgentOpsService().alpha_draft({"idea": "动量因子增强横截面排序"})
        self.assertTrue(out["success"])
        self.assertFalse(out["promote_allowed"])
        self.assertFalse(out["applied"])

    def test_view_bundle_filters_bad(self):
        out = AgentOpsService().view_bundle(
            {
                "views": [
                    {"symbol": "600519", "direction": "bullish", "confidence": 0.8},
                    {"direction": "missing_symbol"},
                    "bad",
                ]
            }
        )
        self.assertTrue(out["success"])
        self.assertEqual(len(out["bundle"]["views"]), 1)
        self.assertFalse(out["applied"])
        self.assertFalse(out["fusion_enabled"])

    def test_truncate_tool_json(self):
        huge = json.dumps({"success": True, "blob": "x" * 8000, "rows": list(range(100))})
        slim = truncate_tool_json_for_llm(huge, max_chars=2000)
        self.assertLessEqual(len(slim), 2100)
        self.assertIn("_truncated_for_llm", slim)


class TestOpsSkillHandler(unittest.TestCase):
    def test_handler_lists_tasks(self):
        from skills.ops.handler import OpsHandler

        raw = OpsHandler().execute({"name": "ops", "parameters": {}})
        data = json.loads(raw)
        self.assertFalse(data["success"])
        self.assertIn("ops_snapshot", data["available_tasks"])

    def test_registry_includes_ops(self):
        from agent.registry import TOOL_NAMES, get_handler, load_tool_definitions

        self.assertIn("ops", TOOL_NAMES)
        self.assertEqual(len(TOOL_NAMES), 14)
        get_handler("ops")
        tools = load_tool_definitions()
        names = {t["function"]["name"] for t in tools}
        self.assertIn("ops", names)


class TestOpsRouting(unittest.TestCase):
    def test_is_ops_and_infer(self):
        from agent.routing import (
            infer_ops_task,
            is_ops_question,
            prepare_tool_params,
            resolve_tool_subset,
        )

        self.assertTrue(is_ops_question("解释今天调仓五问"))
        self.assertEqual(infer_ops_task("为什么没买成"), "ops_anomaly")
        self.assertTrue(is_ops_question("今日盘前简报"))
        self.assertEqual(infer_ops_task("今日盘前市场先验简报"), "prior_brief")
        params = prepare_tool_params("ops", {}, "请预演矩阵调仓")
        self.assertEqual(params.get("task"), "rebalance_preview")
        self.assertEqual(params.get("path"), "watching_matrix")
        prior_params = prepare_tool_params("ops", {}, "请给市场先验配置建议草稿")
        self.assertEqual(prior_params.get("task"), "feedback_draft")
        self.assertEqual(prior_params.get("scope"), "market_prior")
        subset = resolve_tool_subset("解释调仓五问")
        self.assertIsNotNone(subset)
        self.assertIn("ops", subset)

    def test_ops_hint_injected(self):
        from agent.prompts import OPS_HINT
        from agent.routing import build_user_hints

        text = build_user_hints("解释最近调仓五问")
        self.assertIn(OPS_HINT[:12], text)


if __name__ == "__main__":
    unittest.main()
