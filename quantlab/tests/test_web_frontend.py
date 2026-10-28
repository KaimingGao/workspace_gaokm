"""前端/UI 测试：JS 门禁 + 回测展示 + artifacts。合并自 P102 + P95/P96 + P98。"""

from __future__ import annotations

import json
import unittest

from agent.artifacts import build_artifact, compact_payload, primary_tab, resolve_tab
from scripts.check_frontend_js import (
    has_illegal_nullish_logic_mix,
    run_checks,
)


# --- from test_p102_frontend_js.py ---
class TestP102FrontendJs(unittest.TestCase):
    def test_nullish_mix_detector_catches_original_bug(self):
        bad = 't.top_k ?? (t.legs || []).length || "—"'
        self.assertTrue(has_illegal_nullish_logic_mix(bad))
        # 模板字符串里的同一写法也应被扫到
        from scripts.check_frontend_js import STATIC, find_nullish_mix_issues

        bad_line = '`<td>${escapeHtml(String(t.top_k ?? (t.legs || []).length || "—"))}</td>`'
        issues = find_nullish_mix_issues(STATIC / "js" / "quant.js", bad_line)
        self.assertTrue(issues, "template 内非法混用应被检出")

        good = 't.top_k ?? ((t.legs || []).length || "—")'
        self.assertFalse(has_illegal_nullish_logic_mix(good))
        also_good = '(data.summary || {}).equity ?? "—"'
        self.assertFalse(has_illegal_nullish_logic_mix(also_good))
        nested_ok = 'a ?? (b || c)'
        self.assertFalse(has_illegal_nullish_logic_mix(nested_ok))

    def test_repo_frontend_js_passes(self):
        errors, _notes = run_checks(use_esbuild=True)
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_legacy_chat_workspace_files_removed(self):
        from pathlib import Path

        static = Path(__file__).resolve().parents[1] / "web" / "static"
        gone = (
            static / "js" / "chat.js",
            static / "js" / "chat_boot.js",
            static / "js" / "results.js",
            static / "js" / "quant" / "strategy_risk_ui.js",
            static / "partials" / "chat_dialogs.html",
            static / "partials" / "usage_panel.html",
            static / "templates" / "chat.html",
            static / "styles.css.bak",
        )
        missing = [str(p.relative_to(static.parents[1])) for p in gone if p.exists()]
        self.assertEqual(missing, [], msg="仍残留已下线文件: " + ", ".join(missing))

    def test_tool_html_marked_defer(self):
        from web.page_html import clear_html_cache, render_tool_html

        clear_html_cache()
        html = render_tool_html("watching")
        marked = html.find("marked/marked.min.js")
        app = html.find("/static/app.js")
        self.assertGreaterEqual(marked, 0)
        self.assertGreaterEqual(app, 0)
        self.assertRegex(
            html,
            r'<script[^>]+marked/marked\.min\.js[^>]*\sdefer',
        )
        self.assertNotIn("logo-mark", html)
        self.assertIn("logo-text", html)
        self.assertIn("QuantLab", html)


# --- from test_p95_p96_web.py ---
class TestP95P96WebPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient

            from web.app import app
            from web.page_html import clear_html_cache

            clear_html_cache()
            cls.client = TestClient(app)
        except Exception:
            cls.client = None

    def test_index_redirects_to_dashboard(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/", follow_redirects=False)
        self.assertIn(res.status_code, (301, 302, 303, 307, 308))
        self.assertEqual(res.headers.get("location"), "/dashboard")
        watching = self.client.get("/watching")
        self.assertEqual(watching.status_code, 200)
        self.assertIn('data-page="watching"', watching.text)
        self.assertIn("watching-watchlist-table", watching.text)
        self.assertIn("ai-drawer", watching.text)
        self.assertIn('id="btn-ai-open"', watching.text)
        self.assertIn("topbar-ai-btn", watching.text)
        self.assertNotIn('class="dialog-btn" id="btn-ai-open"', watching.text)
        self.assertIn("side-nav", watching.text)
        self.assertIn(">数据中心<", watching.text)
        self.assertIn(">仪表盘<", watching.text)
        self.assertNotIn("dashboard-root", watching.text)
        self.assertNotIn("workspace-main", watching.text)
        self.assertNotIn("topbar-nav", watching.text)
        self.assertNotIn("chat.js", watching.text)
        self.assertNotIn("results.js", watching.text)
        self.assertNotIn('id="btn-reset"', watching.text)

    def test_chat_redirects_offline(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/chat", follow_redirects=False)
        self.assertIn(res.status_code, (302, 307))
        loc = res.headers.get("location") or ""
        self.assertTrue(loc.endswith("/watching") or "/watching" in loc)

    def test_watching_has_ai_drawer(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/watching")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ai-drawer", res.text)
        self.assertIn("btn-ai-open", res.text)
        self.assertIn("ai-drawer-llm-model", res.text)
        self.assertIn('id="ai-drawer-title"', res.text)
        self.assertIn("ai-drawer-head", res.text)
        self.assertIn("只读解读", res.text)
        self.assertNotIn("AI Desk", res.text)
        self.assertNotIn("全屏对话", res.text)
        self.assertNotIn('href="/chat"', res.text)
        self.assertNotIn("topbar-nav", res.text)
        self.assertIn("side-nav", res.text)
        self.assertIn(">研究枢纽<", res.text)

    def test_tool_pages(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        cases = {
            "/quant": (
                "data-page=\"quant\"",
                "quant-page",
                "quant-section-factors",
                "quant-factor-run",
                "因子系数",
                "return_model",
                "ŷ",
                "quant-return-model-fit",
                "quant-expr-code",
                "quant-ols-summary",
                "quant-ridge-lambda",
                "quant-section-research-universe",
            ),
            "/watching": ("data-page=\"watching\"", "watching-search-input", "watching-watchlist-table", "观察", "加入纸面", "quant-watching-sync", "watching-build-layer"),
            "/strategy": ("data-page=\"strategy\"", "strategy-market-context", "strategy-factor-dict", "strategy-regime-board"),
            "/replay": (
                "data-page=\"replay\"",
                "quant-portfolio-run",
                "quant-portfolio-chart",
                "replay-kpi-row",
                "quant-bt-trades",
            ),
            "/follow": ("data-page=\"follow\"", "paper-holdings-table", "paper-trade-status"),
            "/paper": ("data-page=\"follow\"",),  # /paper → 302 /follow
        }
        for path, needles in cases.items():
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200)
                for n in needles:
                    self.assertIn(n, res.text)
                self.assertIn("side-nav", res.text)
                self.assertIn('id="btn-watching"', res.text)
                self.assertIn(">数据中心<", res.text)
                self.assertIn(">交易执行<", res.text)
                self.assertIn(">历史回测<", res.text)
                self.assertIn(">策略中心<", res.text)
                self.assertIn("ai-drawer", res.text)
                self.assertIn('id="btn-ai-open"', res.text)
                self.assertNotIn("topbar-nav", res.text)
                self.assertNotIn("topbar-more", res.text)
                self.assertNotIn(">更多<", res.text)
        follow = self.client.get("/follow")
        self.assertIn('id="btn-follow"', follow.text)
        self.assertIn("side-nav-item active", follow.text)
        self.assertNotIn("观察名单", follow.text)
        self.assertNotIn("paper-watchlist-table", follow.text)
        self.assertNotIn("paper-run", follow.text)
        self.assertNotIn("paper-buy", follow.text)
        # 旧「横截面调仓」按钮 id；进阶区的 paper-rebalance-section / confirm 可以存在
        self.assertNotIn('id="paper-rebalance"', follow.text)
        self.assertIn("paper-rebalance-section", follow.text)
        self.assertNotIn('id="paper-rebalance-confirm"', follow.text)
        self.assertIn("paper-t0-preview-fold", follow.text)
        self.assertIn("手动执行", follow.text)
        self.assertIn("手动预演", follow.text)
        self.assertNotIn("预演调仓", follow.text)
        self.assertNotIn("跑一日", follow.text)
        self.assertNotIn("模拟买入", follow.text)
        self.assertNotIn("横截面调仓", follow.text)
        self.assertNotIn("卖出勾选", follow.text)
        self.assertNotIn("paper-sell-selected", follow.text)
        paper = self.client.get("/paper")
        self.assertNotIn("paper-run", paper.text)
        self.assertNotIn('id="paper-rebalance"', paper.text)
        strategy = self.client.get("/strategy")
        self.assertNotIn("quant-ops-summary", strategy.text)
        self.assertNotIn("quant-factor-run", strategy.text)
        self.assertNotIn("strategy-weight-suggest-run", strategy.text)
        self.assertNotIn("strategy-sample-ops", strategy.text)
        self.assertNotIn("strategy-logic-ref", strategy.text)
        self.assertNotIn("策略逻辑参考", strategy.text)
        self.assertNotIn("样本与闸门", strategy.text)
        self.assertNotIn("strategy-factor-lab", strategy.text)
        self.assertIn('href="/quant"', strategy.text)
        self.assertNotIn('id="paper-daily"', self.client.get("/follow").text)
        quant = self.client.get("/quant")
        self.assertNotIn("quant-ops-run-ci", quant.text)
        self.assertNotIn("quant-ops-package", quant.text)
        self.assertNotIn("量化+调仓", quant.text)
        self.assertNotIn("生成日报", quant.text)
        self.assertNotIn("quant-daily-fold", quant.text)
        self.assertIn('href="/follow"', quant.text)

    def test_page_html_helpers(self):
        from web.page_html import clear_html_cache, render_tool_html

        clear_html_cache()
        tool = render_tool_html("follow")
        self.assertNotIn("topbar-more", tool)
        self.assertNotIn('id="paper-run"', tool)
        self.assertIn("paper-holdings-table", tool)
        self.assertIn("btn-follow", tool)
        uni = render_tool_html("watching")
        self.assertIn("观察名单", uni)
        self.assertIn("btn-watching", uni)
        self.assertIn("ai-drawer", uni)
        self.assertNotIn("workspace-main", uni)
        self.assertNotIn("chat_boot.js", uni)
        self.assertNotIn("chat.js", uni)
        self.assertNotIn("results.js", uni)
        self.assertNotIn("usage-dialog", uni)
        self.assertNotIn('id="btn-reset"', uni)
        self.assertNotIn("evals-dialog", uni)
        with self.assertRaises(ValueError):
            render_tool_html("nope")


# --- from test_p98_artifacts.py ---
class TestArtifacts(unittest.TestCase):
    def test_resolve_tab(self):
        self.assertEqual(resolve_tab("quant", {"task": "portfolio_backtest"}), "quant")
        self.assertEqual(resolve_tab("quant", {"task": "portfolio_bridge"}), "follow")
        self.assertEqual(resolve_tab("position", {}), "follow")
        self.assertEqual(resolve_tab("quote", {}), "reply")
        self.assertEqual(resolve_tab("backtest", {}), "quant")

    def test_build_artifact_portfolio(self):
        raw = json.dumps(
            {
                "success": True,
                "metrics": {"total_return_pct": 12.5, "win_rate_pct": 55},
                "trades_sample": [{"entry_date": "2024-01-01"}] * 30,
                "loaded_stocks": ["a"] * 5,
            },
            ensure_ascii=False,
        )
        art = build_artifact("quant", {"task": "portfolio_backtest"}, raw)
        self.assertEqual(art["tab"], "quant")
        self.assertTrue(art["success"])
        self.assertLessEqual(len(art["data"]["trades_sample"]), 12)
        self.assertIn("累计", art["summary"])

    def test_primary_tab(self):
        arts = [
            build_artifact("quote", {}, json.dumps({"price": 1})),
            build_artifact(
                "quant",
                {"task": "portfolio_backtest"},
                json.dumps({"success": True, "metrics": {"total_return_pct": 1}}),
            ),
        ]
        self.assertEqual(primary_tab(arts), "quant")
        self.assertEqual(primary_tab([]), "reply")

    def test_compact_keeps_metrics(self):
        out = compact_payload({"success": True, "metrics": {"a": 1}, "noise_big": {"x": 1}})
        self.assertEqual(out["metrics"]["a"], 1)

class TestChatArtifactsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient

            from web.app import app

            cls.client = TestClient(app)
        except Exception:
            cls.client = None

    def test_chat_returns_artifacts(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        from unittest.mock import MagicMock, patch

        import web.deps as deps

        agent = MagicMock()
        agent.llm.api_key = "x"
        agent.llm.is_available.return_value = True
        agent.chat.return_value = "回测完成"
        agent.get_last_turn_usage.return_value = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "calls": 1,
            "reasoning_tokens": 0,
        }
        agent.get_session_usage.return_value = agent.get_last_turn_usage.return_value
        agent.get_last_artifacts.return_value = [
            {
                "tool": "quant",
                "params": {"task": "portfolio_backtest"},
                "tab": "quant",
                "success": True,
                "summary": "quant · portfolio_backtest",
                "data": {"success": True, "metrics": {"total_return_pct": 3}},
            }
        ]
        with patch.object(deps.chat, "get_or_create", return_value=("s1", agent)):
            res = self.client.post(
                "/api/chat",
                json={"message": "组合回测一下"},
                headers={"X-Session-Id": "s1"},
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["primary_tab"], "quant")
        self.assertEqual(len(data["artifacts"]), 1)
        self.assertEqual(data["artifacts"][0]["tab"], "quant")


class TestPageCssScope(unittest.TestCase):
    def test_page_css_does_not_leak_typography_onto_body(self):
        """页级字号、行高、圆角不得写到 body，否则切页时侧栏和顶栏会跟着变。"""
        import re
        from pathlib import Path

        css_dir = Path(__file__).resolve().parents[1] / "web" / "static" / "css"
        for name in ("quant", "replay", "follow", "strategy"):
            css = (css_dir / f"{name}.css").read_text(encoding="utf-8")
            for prop in ("font-size", "line-height", "--radius"):
                self.assertIsNone(
                    re.search(
                        rf'body\[data-page="{name}"\]\s*\{{[^}}]*{re.escape(prop)}:',
                        css,
                    ),
                    msg=f"{name}: {prop} leaked onto body",
                )


if __name__ == "__main__":
    unittest.main()
