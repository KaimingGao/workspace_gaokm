"""P95 回测展示 · P96 多页壳 · 主线（观察/模拟/回溯）。"""

from __future__ import annotations

import unittest


class TestP95P96WebPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            from web.page_html import clear_html_cache
            from web.app import app

            clear_html_cache()
            cls.client = TestClient(app)
        except Exception:
            cls.client = None

    def test_index_redirects_to_watching(self):
        if self.client is None:
            self.skipTest("fastapi not installed")
        res = self.client.get("/", follow_redirects=False)
        self.assertIn(res.status_code, (301, 302, 303, 307, 308))
        self.assertEqual(res.headers.get("location"), "/watching")
        watching = self.client.get("/watching")
        self.assertEqual(watching.status_code, 200)
        self.assertIn('data-page="watching"', watching.text)
        self.assertIn("watching-watchlist-table", watching.text)
        self.assertIn("ai-drawer", watching.text)
        self.assertIn('id="btn-ai-open"', watching.text)
        self.assertIn("side-nav", watching.text)
        self.assertIn(">数据中心<", watching.text)
        self.assertNotIn("dashboard-root", watching.text)
        self.assertNotIn(">仪表盘<", watching.text)
        self.assertNotIn("workspace-main", watching.text)
        self.assertNotIn("topbar-nav", watching.text)

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
                "quant-ops-summary",
                "quant-factor-run",
                "quant-cross-run",
                "quant-ops-run-daily",
                "因子系数",
                "IC / OLS·因子系数说明",
                "return_model",
                "ŷ",
                "quant-ols-pool-run",
                "quant-ols-code",
                "quant-probe-run",
                "对照验证",
                "quant-ols-summary",
                "quant-ridge-lambda",
                "quant-daily-fold",
                "quant-daily-on-alpha",
                "quant-daily-rank-enter",
                "quant-daily-rank-strong",
                "quant-interpret-offline",
                "quant-interpret-neutral",
                "规则解读",
            ),
            "/watching": ("data-page=\"watching\"", "watching-search-input", "watching-watchlist-table", "观察", "加入纸面", "quant-watching-sync", "watching-build-layer"),
            "/strategy": ("data-page=\"strategy\"", "strategy-list", "strategy-factor-dict"),
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
        self.assertIn("paper-rebalance-confirm", follow.text)
        self.assertIn("预演调仓", follow.text)
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
        self.assertIn("生成日报", quant.text)
        self.assertIn('href="/follow"', quant.text)

    def test_page_html_helpers(self):
        from web.page_html import clear_html_cache, render_chat_html, render_tool_html

        clear_html_cache()
        chat = render_chat_html()
        self.assertIn("tab-panel-strategy", chat)
        self.assertNotIn("results-more", chat)
        self.assertIn("tab-btn-strategy", chat)
        self.assertIn("quant-bt-trades", chat)
        self.assertIn("tab-btn-follow", chat)
        self.assertNotIn("workspace-portfolio", chat)
        self.assertNotIn("tab-panel-portfolio", chat)
        self.assertNotIn("topbar-nav", chat)
        tool = render_tool_html("follow")
        self.assertNotIn("topbar-more", tool)
        self.assertNotIn('id="paper-run"', tool)
        self.assertIn("paper-holdings-table", tool)
        self.assertIn("btn-follow", tool)
        uni = render_tool_html("watching")
        self.assertIn("观察名单", uni)
        self.assertIn("btn-watching", uni)
        with self.assertRaises(ValueError):
            render_tool_html("nope")


if __name__ == "__main__":
    unittest.main()
