"""D 轨 · 数据层深化补强（D0–D4）主干验收。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestD0AnnIngest(unittest.TestCase):
    def test_merge_writes_ann_date(self):
        from core.fundamentals_pit import merge_history_point, select_point_as_of

        hist = merge_history_point(
            [],
            as_of="2024-03-31",
            metrics={"pe": 10},
            ann_date="2024-04-20",
        )
        self.assertEqual(hist[0].get("ann_date"), "2024-04-20")
        self.assertEqual(hist[0].get("available_as_of"), "2024-04-20")
        chosen, meta = select_point_as_of(hist, "2024-04-10")
        self.assertIsNone(chosen)
        chosen, meta = select_point_as_of(hist, "2024-04-21")
        self.assertIsNotNone(chosen)

    def test_pick_financial_row_ann(self):
        from skills.fundamentals.engine import _pick_financial_row

        row = {
            "日期": "2024-06-30",
            "公告日期": "2024-08-15",
            "净资产收益率(%)": 12.0,
            "净利润增长率(%)": 5.0,
        }
        parsed = _pick_financial_row(row)
        self.assertEqual(parsed.get("ann_date"), "2024-08-15")
        self.assertNotIn("ann_missing", parsed)


class TestD1SourceAuditDefault(unittest.TestCase):
    def test_attach_source_audit_shape(self):
        from core.data.consistency import attach_source_audit

        with patch(
            "core.data.consistency.audit_code_sources",
            return_value={"ok": True, "status": "ok", "codes": ["600519"]},
        ):
            out = attach_source_audit({"success": True}, codes=["600519"])
        self.assertIn("source_audit", out)
        self.assertEqual(out["source_audit"]["status"], "ok")


class TestD2Adjust(unittest.TestCase):
    def test_normalize_adjust(self):
        from core.data.facade import normalize_adjust_policy

        self.assertEqual(normalize_adjust_policy("raw"), "raw")
        self.assertEqual(normalize_adjust_policy("none"), "raw")
        self.assertEqual(normalize_adjust_policy("hfq"), "hfq")
        self.assertEqual(normalize_adjust_policy(None), "qfq")

    def test_get_bars_passes_adjust(self):
        from core.data import facade as ds

        with patch(
            "core.ports.market.fetch_daily_bars",
            return_value=([], "empty"),
        ) as m:
            pack = ds.get_bars("600519", limit=5, adjust="raw", use_cache=False)
        self.assertEqual(pack.get("adjust_policy"), "raw")
        kwargs = m.call_args.kwargs if m.call_args else {}
        self.assertEqual(kwargs.get("adjust"), "raw")


class TestD3Calendar(unittest.TestCase):
    def test_weekend_not_trading(self):
        from core.market.calendar import filter_trading_dates, is_trading_day

        self.assertFalse(is_trading_day("2024-01-06"))  # Sat
        self.assertTrue(is_trading_day("2024-01-08", holidays=set()))  # Mon
        out = filter_trading_dates(["2024-01-06", "2024-01-08", "2024-01-09"])
        self.assertEqual(out, ["2024-01-08", "2024-01-09"])


class TestD4DataQuality(unittest.TestCase):
    def test_build_report(self):
        from core.data.quality_center import build_data_quality_report

        with patch(
            "core.data.coverage.build_data_coverage",
            return_value={"ok": True, "mapped": 1},
        ), patch(
            "core.sample_ops.fundamentals_history_coverage",
            return_value={"real_multi_coverage": 0.6, "real_multi_point": 3},
        ), patch(
            "core.sample_ops.sample_status",
            return_value={"discipline": {"warnings": []}},
        ), patch(
            "core.data.consistency.audit_code_sources",
            return_value={"ok": True, "status": "ok", "fallback_codes": [], "thin_codes": []},
        ):
            out = build_data_quality_report(codes=["600519"], include_source_audit=True)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("kind"), "data_quality_center")
        self.assertIn("bars_coverage", out)
        self.assertIn("calendar", out)

    def test_api_route(self):
        try:
            from fastapi.testclient import TestClient
            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        with patch.object(
            web_app.deps.platform,
            "get_data_quality",
            return_value={"ok": True, "status": "ok", "kind": "data_quality_center"},
        ):
            res = client.get("/api/ops/data-quality")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("ok"))

    def test_panel_has_button(self):
        path = os.path.join(ROOT, "web/static/partials/platform_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("data-quality-refresh", html)
        self.assertNotIn("data-quality-fold", html)
        self.assertNotIn("sample-status-fold", html)
        self.assertNotIn("sample-status-refresh", html)
        self.assertNotIn("fundamentals-ingest-nudge", html)
        self.assertNotIn("maturity-gate-refresh", html)
        self.assertNotIn("fit-gap-refresh", html)
        self.assertNotIn("platform-ops-section", html)
        self.assertNotIn("样本与数据质量", html)
        self.assertIn("audit-timeline-list", html)
        self.assertIn("audit-timeline-clear", html)
        self.assertIn("platform-desk-badge", html)
        self.assertIn("platform-section-overview", html)
        self.assertNotIn("platform-aux-fold", html)
        self.assertNotIn("feedback-run", html)
        self.assertNotIn("feedback-from-alerts", html)
        self.assertNotIn("prefill-json", html)
        self.assertNotIn("配置反馈建议", html)
        self.assertNotIn("非交易订单预填", html)
        self.assertNotIn("decision-list", html)
        self.assertNotIn("schedule-paper-strategy", html)
        self.assertNotIn("决策记录", html)
        self.assertNotIn("memory-risk", html)
        self.assertNotIn("memory-notes", html)
        self.assertNotIn("memory-llm-model", html)
        self.assertNotIn("north-star-kpi", html)
        self.assertNotIn("north-star-fit-banner", html)
        self.assertNotIn("north-star-refresh", html)
        self.assertNotIn("偏好与默认", html)
        self.assertNotIn("platform-ops-status", html)
        self.assertNotIn("memory-horizon-display", html)
        self.assertNotIn("memory-horizon-readonly", html)

        drawer = os.path.join(ROOT, "web/static/partials/ai_drawer.html")
        with open(drawer, encoding="utf-8") as f:
            drawer_html = f.read()
        self.assertIn("ai-drawer-llm-model", drawer_html)
        self.assertIn("ai-drawer-llm-save", drawer_html)
        self.assertIn("ai-drawer-settings-btn", drawer_html)
        self.assertIn("ai-drawer-settings-panel", drawer_html)
        self.assertIn('id="ai-drawer-title"', drawer_html)
        self.assertNotIn("AI Desk", drawer_html)
        self.assertNotIn("dashboard-eyebrow", drawer_html)
        self.assertNotIn("ai-drawer-head-path", drawer_html)
        self.assertNotIn("ai-drawer-market", drawer_html)
        self.assertNotIn("ai-drawer-close", drawer_html)
        self.assertNotIn("研究 / 纸面", drawer_html)
        self.assertNotIn('data-ctx="page"', drawer_html)
        self.assertLess(
            drawer_html.find("ai-drawer-chips"),
            drawer_html.find("ai-drawer-settings-btn"),
        )
        self.assertLess(
            drawer_html.find("ai-drawer-settings-panel"),
            drawer_html.find("ai-drawer-llm-model"),
        )

    def test_platform_js_drops_legacy_copy(self):
        path = os.path.join(ROOT, "web/static/js/platform.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("loadClusterLiveHint", js)
        self.assertNotIn("loadDecisions", js)
        self.assertNotIn("Top-K", js)
        self.assertNotIn("纸面日更 · P2", js)
        self.assertNotIn("loadNorthStar", js)
        self.assertNotIn("renderFitGuide", js)
        self.assertNotIn("renderNorthStar", js)
        self.assertNotIn("memory-llm-model", js)
        self.assertNotIn("loadMemory", js)
        self.assertNotIn("loadSampleStatus", js)
        self.assertNotIn("loadDataQuality", js)
        self.assertNotIn("/api/ops/sample-status", js)
        self.assertIn('strategy: "short_conservative"', js)
        self.assertIn("/api/audit/timeline/clear", js)
        self.assertNotIn("/api/feedback/suggest", js)
        self.assertNotIn("/api/orders/prefill", js)
        self.assertNotIn("runFeedback", js)
        self.assertNotIn("runPrefill", js)
        self.assertNotIn("memory-risk", js)
        self.assertNotIn("memory-notes", js)
        self.assertNotIn("risk_style", js)
        self.assertNotIn("horizon_days", js)
        self.assertNotIn("cachedHorizonDays", js)
        self.assertNotIn("memory-horizon-display", js)
        self.assertNotIn('ev.href || "/platform"', js)

    def test_nav_copy_says_platform(self):
        pal = os.path.join(ROOT, "web/static/js/command_palette.js")
        with open(pal, encoding="utf-8") as f:
            pal_js = f.read()
        self.assertIn('label: "前往 · 平台"', pal_js)
        self.assertIn('id: "evals-open"', pal_js)
        self.assertNotIn("前往 · 系统设置", pal_js)
        drawer = os.path.join(ROOT, "web/static/js/ai_drawer.js")
        with open(drawer, encoding="utf-8") as f:
            drawer_js = f.read()
        self.assertIn('label: "去平台"', drawer_js)
        self.assertNotIn("去系统设置", drawer_js)
        self.assertNotIn("refreshAiMarketStrip", drawer_js)
        self.assertNotIn("ai-drawer-market", drawer_js)
        self.assertNotIn("paintContext", drawer_js)
        self.assertNotIn("ai-drawer-close", drawer_js)
        self.assertNotIn("ai-msg-progress-bar", drawer_js)
        self.assertNotIn('data-page === "chat"', drawer_js)


if __name__ == "__main__":
    unittest.main()
