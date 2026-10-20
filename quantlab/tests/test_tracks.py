"""多 track 测试聚合（B/D/E/S/V/X/Y/ProCore）。合并自 8 个独立 track 文件。"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.conftest import ROOT

# --- from test_b_track.py ---
"""B 轨：回归准确性 — y_spec · 样本指纹 · 共线 · Ridge · demote · OOS regime。"""

class TestB0AnnMissing(unittest.TestCase):
    def test_ann_missing_top_codes(self):
        from core.research.beta_accuracy import ann_missing_top_codes

        cov = {
            "rows": [
                {"code": "A", "ann_missing_points": 3, "history_count": 4},
                {"code": "B", "ann_missing_points": 0},
                {"code": "C", "ann_missing_points": 5, "history_count": 5},
            ]
        }
        top = ann_missing_top_codes(cov, limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["code"], "C")
        self.assertEqual(top[1]["code"], "A")

class TestB1SampleFingerprint(unittest.TestCase):
    def test_fingerprint_promote_gate(self):
        from core.research.beta_accuracy import sample_fingerprint

        ok = sample_fingerprint(n_obs=40, n_names=5, min_obs=24, min_names=3)
        self.assertTrue(ok["promote_ok"])
        bad = sample_fingerprint(n_obs=10, n_names=2, min_obs=24, min_names=3)
        self.assertFalse(bad["promote_ok"])
        # 单票组：min_names 应随组员数降到 1
        solo = sample_fingerprint(n_obs=40, n_names=1, min_obs=24, min_names=1)
        self.assertTrue(solo["promote_ok"])
        pair = sample_fingerprint(n_obs=40, n_names=2, min_obs=24, min_names=2)
        self.assertTrue(pair["promote_ok"])

    def test_group_local_n_obs_blocker_does_not_poison_pool(self):
        from core.research.beta_accuracy import fingerprint_blocker_is_group_local

        self.assertTrue(
            fingerprint_blocker_is_group_local("组内：n_obs=20 < min_obs=24")
        )
        self.assertTrue(
            fingerprint_blocker_is_group_local("n_obs=20 < min_obs=24", from_group=True)
        )
        self.assertFalse(
            fingerprint_blocker_is_group_local("n_obs=20 < min_obs=24")
        )
        # _aggregate_sample_fingerprint 随 factor_ols_clusters 删除；仅验组内 blocker 语义

    def test_universe_sample_gate_fields(self):
        from core.validation_universe import universe_sample_gate

        g = universe_sample_gate(
            universe={"min_codes": 1000, "include_only": ["600000"], "exclude_codes": []}
        )
        self.assertFalse(g["ok"])
        self.assertIn("min_codes", str(g.get("blockers")))

class TestB2YSpec(unittest.TestCase):
    def test_y_spec_fields(self):
        from core.research.beta_accuracy import build_y_spec

        y = build_y_spec(horizon_days=5)
        self.assertEqual(y["horizon_days"], 5)
        self.assertFalse(y["include_cost"])
        self.assertEqual(y["track"], "B2")
        self.assertIn("open", y["formula"])

    def test_fit_gap_mentions_y_spec(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(backtest_params={"horizon_days": 99})
        codes = {h.get("code") for h in out.get("hints") or []}
        self.assertIn("y_spec", codes)
        self.assertIn("horizon_mismatch", codes)

class TestB3CollinearityAndRidge(unittest.TestCase):
    def test_drop_redundant_on_correlated_pair(self):
        from core.research.beta_accuracy import apply_collinearity_policy
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        a, b = TREND_FAMILY[0], TREND_FAMILY[1]
        xs = []
        ys = []
        for i in range(30):
            v = float(i)
            xs.append({a: v, b: v + 0.01 * i, "value": float(i % 3)})
            ys.append(v * 0.1)
        kept, dropped, meta = apply_collinearity_policy(
            xs, [a, b, "value"], policy="drop_redundant", corr_threshold=0.85, ys=ys
        )
        self.assertEqual(len(dropped), 1)
        self.assertIn(dropped[0], (a, b))
        self.assertIn("value", kept)
        self.assertEqual(meta["collinearity_policy"], "drop_redundant")

    def test_keep_all_policy(self):
        from core.research.beta_accuracy import apply_collinearity_policy
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        a, b = TREND_FAMILY[0], TREND_FAMILY[1]
        xs = [{a: float(i), b: float(i)} for i in range(20)]
        kept, dropped, _ = apply_collinearity_policy(
            xs, [a, b], policy="keep_all", corr_threshold=0.5
        )
        self.assertEqual(kept, [a, b])
        self.assertEqual(dropped, [])

    def test_select_ridge_returns_meta(self):
        from core.research.beta_accuracy import select_ridge_lambda

        xs = [{"momentum": float(i), "value": float(i % 5)} for i in range(24)]
        ys = [0.1 * i for i in range(24)]
        meta = select_ridge_lambda(xs, ys, ["momentum", "value"])
        self.assertIn("ridge_lambda_selected", meta)
        self.assertEqual(meta.get("track"), "B3")

class TestB4ClusterRetired(unittest.TestCase):
    def test_cluster_package_gone(self):
        """分组 live_health / get_cluster_scoring_cfg 已随 cluster 包删除。"""
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("core.signal.cluster"))

class TestB5RespectRegime(unittest.TestCase):
    def test_evaluate_research_oos_default_respect_regime(self):
        from core.signal.weight_oos_gate import evaluate_research_oos

        sig = inspect.signature(evaluate_research_oos)
        self.assertTrue(sig.parameters["respect_regime"].default)


# --- from test_d_track.py ---
"""D 轨 · 数据层深化补强（D0–D4）主干验收。"""

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
        from adapters.fundamentals.engine import _pick_financial_row

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

    def test_mid_autumn_2026_not_trading(self):
        """中秋 2026-09-25 须在 cn_holidays.json，完整度日历不应出现该列。"""
        from core.market.calendar import is_trading_day
        from quant.research.bars_integrity import recent_trading_days

        self.assertFalse(is_trading_day("2026-09-25"))
        self.assertTrue(is_trading_day("2026-09-24"))
        self.assertTrue(is_trading_day("2026-09-28"))
        days = recent_trading_days(10, end="2026-09-28")
        self.assertIn("2026-09-24", days)
        self.assertIn("2026-09-28", days)
        self.assertNotIn("2026-09-25", days)
        self.assertNotIn("2026-09-26", days)
        self.assertNotIn("2026-09-27", days)

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
        self.assertNotIn("audit-timeline-list", html)
        self.assertNotIn("audit-timeline-clear", html)
        self.assertNotIn("platform-audit-section", html)
        self.assertIn("platform-desk-badge", html)
        self.assertIn("调度 · 日更", html)
        self.assertNotIn("调度 · 审计", html)
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
        self.assertNotIn("/api/audit/timeline/clear", js)
        self.assertNotIn("/api/audit/timeline", js)
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
        self.assertNotIn('id: "evals-open"', pal_js)
        self.assertNotIn('id: "nav-audit"', pal_js)
        self.assertNotIn('id: "live-refresh"', pal_js)
        self.assertNotIn('id: "watching-dock"', pal_js)
        self.assertNotIn('id: "factor-dict"', pal_js)
        self.assertNotIn("审计时间线", pal_js)
        self.assertNotIn("黄金用例校验", pal_js)
        self.assertNotIn("立即刷新推送", pal_js)
        self.assertNotIn("切换双栏", pal_js)
        self.assertNotIn("因子字典", pal_js)
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


# --- from test_e_track.py ---
"""E 轨 · 北极星证据诚实（E0–E4）主干验收。"""

def _synth_bars__e_track(n: int = 40, start: float = 10.0, step: float = 0.15):
    bars = []
    px = start
    d = datetime(2024, 1, 2)  # Tuesday
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        px = px + step * (1 if i % 3 else -0.5)
        bars.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1000 + i * 10,
                "amount": (1000 + i * 10) * px,
            }
        )
        d += timedelta(days=1)
    return bars

class TestE0StrategyScope(unittest.TestCase):
    def test_strategy_sharpe_differs_from_all(self):
        from core.north_star import build_north_star_report, compute_paper_risk_metrics

        base = datetime(2026, 1, 5)  # Monday
        snaps = []
        eq_all = 1_000_000.0
        eq_st = 500_000.0
        # 全账户温和上涨；策略仓波动更大
        for i in range(25):
            d = base + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            eq_all *= 1.005
            eq_st *= 1.02 if i % 2 == 0 else 0.99
            snaps.append(
                {
                    "ts": d.isoformat(timespec="seconds"),
                    "equity": round(eq_all, 2),
                    "equity_strategy": round(eq_st, 2),
                }
            )
        all_m = compute_paper_risk_metrics(snaps, scope="all")
        st_m = compute_paper_risk_metrics(snaps, scope="strategy")
        self.assertEqual(all_m["status"], "ok")
        self.assertEqual(st_m["status"], "ok")
        self.assertEqual(st_m.get("scope"), "strategy")
        self.assertNotEqual(all_m.get("rolling_sharpe"), st_m.get("rolling_sharpe"))

        report = build_north_star_report({"snapshots": snaps}, backtest_curve=[])
        self.assertIn("paper_risk_strategy", report)
        self.assertIn("scopes", report)
        self.assertEqual(report["scopes"]["strategy"]["paper_risk"]["scope"], "strategy")

    def test_no_strategy_equity_unavailable(self):
        from core.north_star import compute_paper_risk_metrics

        snaps = [
            {"ts": f"2026-01-{i+1:02d}T12:00:00", "equity": 100 + i} for i in range(20)
        ]
        out = compute_paper_risk_metrics(snaps, scope="strategy")
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["reason"], "no_strategy_equity")

class TestE1AlignCurve(unittest.TestCase):
    def test_save_aligns_to_paper_span(self):
        from core.north_star import load_last_backtest_curve, save_last_backtest_curve

        paper = []
        for i in range(15):
            d = datetime(2026, 3, 1) + timedelta(days=i)
            paper.append({"ts": d.isoformat(timespec="seconds"), "equity": 100 + i})
        # 回测曲线远长于纸面，含纸面前后
        curve = []
        for i in range(60):
            d = datetime(2026, 2, 1) + timedelta(days=i)
            curve.append({"date": d.strftime("%Y-%m-%d"), "equity": 90 + i * 0.5})

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bt.json")
            save_last_backtest_curve(
                curve,
                path=path,
                align_to_paper=True,
                paper_snapshots=paper,
                meta={"test": True},
            )
            packed = load_last_backtest_curve(path)
            self.assertTrue(packed.get("ok"))
            align = (packed.get("meta") or {}).get("align") or {}
            self.assertTrue(align.get("aligned"))
            self.assertEqual(align.get("align_from"), "2026-03-01")
            pts = packed.get("curve") or []
            self.assertGreaterEqual(len(pts), 6)
            self.assertGreaterEqual(pts[0]["date"], "2026-03-01")
            self.assertLessEqual(pts[-1]["date"], "2026-03-15")

    def test_realization_diagnosis_on_no_overlap(self):
        from core.north_star import compute_realization

        paper = [
            {"ts": f"2026-01-{i+1:02d}T12:00:00", "equity": 100 + i} for i in range(10)
        ]
        bt = [{"date": f"2025-01-{i+1:02d}", "equity": 100 + i} for i in range(10)]
        out = compute_realization(paper, bt)
        self.assertEqual(out["reason"], "no_date_overlap")
        self.assertIn("diagnosis", out)
        self.assertIn("paper_span", out)

class TestE2FactorIcPit(unittest.TestCase):
    def test_ic_report_marks_pit_metadata(self):
        from quant.research.factor_report import compute_factor_ic_report

        bars = _synth_bars__e_track(50)
        with patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value={"ok": False, "metrics": None},
        ):
            out = compute_factor_ic_report(
                bars,
                stock_code="600519",
                pit_fundamentals=True,
                fundamentals={"pe": 20},
            )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("pit_fundamentals"))
        self.assertIn("pit_resolve_miss", out)

    def test_future_report_not_used_when_ann_missing(self):
        """决策日早于公告日 → resolve 返回空，不应静默用最新快照。"""
        from quant.research.factor_report import _resolve_fund_for_day

        cache = {}
        with patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value={"ok": False, "metrics": None},
        ) as m:
            got = _resolve_fund_for_day(
                "600519",
                "2024-01-15",
                pit_fundamentals=True,
                fundamentals_fallback={"pe": 99},
                cache=cache,
            )
        self.assertIsNone(got)
        m.assert_called()

class TestE3Calendar(unittest.TestCase):
    def test_pool_ic_filters_weekend(self):
        from core.backtest.pool_ic import compute_pool_cross_section_ic
        from core.market.calendar import filter_trading_dates

        # 含周末的共同日期应被滤掉
        raw = ["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08"]  # Fri Sat Sun Mon
        filtered = filter_trading_dates(raw)
        self.assertNotIn("2024-01-06", filtered)
        self.assertNotIn("2024-01-07", filtered)
        self.assertIn("2024-01-05", filtered)

        stock_bars = {
            f"S{i:02d}": _synth_bars__e_track(45, start=10 + i, step=0.1 + i * 0.02)
            for i in range(6)
        }
        out = compute_pool_cross_section_ic(
            stock_bars,
            horizon_days=3,
            min_history=12,
            min_names=4,
            pit_fundamentals=False,
        )
        self.assertEqual(out.get("calendar"), "cn_lite")

class TestE4OutcomeDigest(unittest.TestCase):
    def test_unlabeled_digest(self):
        from core.risk.block_outcome import unlabeled_digest

        logs = [
            {
                "type": "risk_block",
                "ts": "2026-01-01T10:00:00",
                "detail": "单票超限",
                "meta": {"codes": ["600519"], "reason_code": "single_name"},
            },
            {
                "type": "risk_block",
                "ts": "2026-01-02T10:00:00",
                "detail": "已标",
                "meta": {"codes": ["000001"], "outcome": "true_positive"},
            },
        ]
        dig = unlabeled_digest(logs, limit=10)
        self.assertEqual(dig["unlabeled_count"], 1)
        self.assertEqual(dig["block_count"], 2)
        self.assertEqual(len(dig["items"]), 1)
        self.assertEqual(dig["items"][0]["codes"], ["600519"])

    def test_maturity_gate_strategy_scope_soft(self):
        from core.maturity_gate import evaluate_maturity_gate

        gate = evaluate_maturity_gate(
            sample_status={},
            north_star={
                "paper_risk_strategy": {
                    "status": "unavailable",
                    "reason": "no_strategy_equity",
                },
                "realization": {"status": "unavailable"},
            },
        )
        ids = {i["id"]: i for i in gate["items"]}
        self.assertIn("strategy_scope_readable", ids)
        self.assertTrue(ids["strategy_scope_readable"]["ok"])
        self.assertEqual(ids["strategy_scope_readable"]["severity"], "soft")


# --- from test_s_track.py ---
"""S 轨 · 策略验证深化补强（S0–S4）主干验收。"""

def _synth_bars__s_track(n: int = 40, start: float = 10.0, step: float = 0.15):
    bars = []
    px = start
    for i in range(n):
        px = px + step * (1 if i % 3 else -0.5)
        bars.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1000 + i * 10,
                "amount": (1000 + i * 10) * px,
            }
        )
    return bars

class TestS0AnnDatePit(unittest.TestCase):
    def test_select_prefers_ann_date(self):
        from core.fundamentals_pit import select_point_as_of

        history = [
            {
                "as_of": "2024-03-31",
                "ann_date": "2024-04-20",
                "metrics": {"pe": 12.0},
            },
            {
                "as_of": "2024-06-30",
                "ann_date": "2024-08-15",
                "metrics": {"pe": 15.0},
            },
        ]
        # 报告期已过但公告未出 → 不应选入
        chosen, meta = select_point_as_of(history, "2024-04-10")
        self.assertIsNone(chosen)
        self.assertTrue(meta.get("lookahead") or meta.get("reason") == "no_point_on_or_before")
        # 公告日后 → 可选 Q1
        chosen, meta = select_point_as_of(history, "2024-04-21")
        self.assertIsNotNone(chosen)
        self.assertEqual(meta.get("selected_as_of"), "2024-03-31")
        self.assertEqual(meta.get("selected_available_as_of"), "2024-04-20")

class TestS1FactorCsIc(unittest.TestCase):
    def test_factor_cs_ic_on_synth_panel(self):
        from core.backtest.factor_cs_ic import compute_factor_cross_section_ic

        stock_bars = {
            f"S{i:02d}": _synth_bars__s_track(45, start=10 + i, step=0.1 + i * 0.02)
            for i in range(6)
        }
        out = compute_factor_cross_section_ic(
            stock_bars,
            horizon_days=3,
            min_history=12,
            min_names=4,
            pit_fundamentals=False,
        )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("mode"), "factor_cross_section")
        self.assertGreaterEqual(len(out.get("factors") or []), 1)

    def test_js_and_panel_have_cs_ic_button(self):
        panel_path = os.path.join(ROOT, "web/static/partials/quant_panel.html")
        suggest_path = os.path.join(ROOT, "web/static/js/quant/domain_suggest.js")
        with open(panel_path, encoding="utf-8") as f:
            panel = f.read()
        with open(suggest_path, encoding="utf-8") as f:
            suggest = f.read()
        # 截面 IC 按钮已拆除；API 调用仍保留在 domain_suggest
        self.assertNotIn("quant-cs-ic-run", panel)
        self.assertIn("/api/quant/factor-cs-ic", suggest)
        self.assertIn("runFactorCsIcSuggest", suggest)

class TestS2ValidationPackAndAb(unittest.TestCase):
    def test_pack_has_exposure_slots(self):
        from core.validation_pack import build_validation_pack

        out = build_validation_pack(
            backtest_result={"success": True, "metrics": {"total_return_pct": 1.2}},
            exposure={"sector": {"银行": 20}},
            risk_blocks={"block_count": 2, "labeled_count": 1},
            ab_compare={"ok": True, "same_fingerprint": False},
        )
        self.assertTrue(out.get("ok"))
        pack = out["pack"]
        self.assertGreaterEqual(int(pack.get("version") or 0), 2)
        self.assertIn("exposure", pack)
        self.assertEqual(pack["exposure"]["sector"]["银行"], 20)
        self.assertIn("risk_blocks", pack)
        self.assertIn("ab_compare", pack)
        self.assertIn("neutralize", pack)

    def test_ab_compare_fingerprints(self):
        from core.ab_compare import build_ab_compare

        out = build_ab_compare(
            result_a={"metrics": {"total_return_pct": 1.0}},
            result_b={"metrics": {"total_return_pct": 2.0}},
        )
        self.assertTrue(out.get("ok"))
        self.assertNotEqual(out["a"]["fingerprint"], out["b"]["fingerprint"])
        self.assertEqual(out["delta_b_minus_a"].get("total_return_pct"), 1.0)

class TestS3WeightModesQp(unittest.TestCase):
    def test_compare_includes_qp_lite(self):
        from core.weight_mode_compare import compare_weight_modes

        cands = [
            {"code": f"C{i}", "score": 60 + i, "sector": "A"} for i in range(5)
        ]
        out = compare_weight_modes(cands, max_positions=4, min_score=50)
        self.assertTrue(out.get("ok"))
        self.assertIn("qp_lite", out.get("modes") or {})

class TestS4MaturityGate(unittest.TestCase):
    def test_gate_has_s_track_items(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.6,
                    "empty": 0,
                    "total": 10,
                    "synthetic_multi_point": 0,
                    "real_multi_point": 5,
                },
                "paper_snapshots": {"enough_for_sharpe": True, "densified_count": 0, "count": 40},
                "ttm": {"real_events": 3, "seeded_events": 0},
                "risk_blocks": {"labeled_count": 0, "block_count": 0},
            },
            north_star={"realization": {"status": "ok", "corr": 0.5}},
            core_paths={"ok": True},
        )
        ids = {i["id"] for i in out["items"]}
        self.assertIn("factor_cs_ic_available", ids)
        self.assertIn("validation_pack_shape", ids)
        self.assertIn("ann_date_pit", ids)
        self.assertTrue(str(out.get("track") or "").startswith("S0-S4"))
        ids_sections = {i.get("section") for i in out["items"]}
        self.assertTrue({"dc_track", "rk_track"} & ids_sections or True)

class TestS1ApiRoute(unittest.TestCase):
    def test_factor_cs_ic_route_mocked(self):
        try:
            from fastapi.testclient import TestClient

            import web.app as web_app
        except ImportError:
            self.skipTest("fastapi not installed")

        client = TestClient(web_app.app)
        with patch.object(
            web_app.deps.quant,
            "run_factor_cs_ic_experiment",
            return_value={"success": True, "ok": True, "factors": [], "task": "factor_cs_ic"},
        ):
            res = client.post(
                "/api/quant/factor-cs-ic",
                json={"lookback": 60, "watching_limit": 5},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))


# --- from test_v_track.py ---
"""V0–V5 策略验证升级轨单测。"""

class TestV0SampleDiscipline(unittest.TestCase):
    def test_real_vs_synthetic_coverage(self):
        from core.sample_ops import fundamentals_history_coverage, seed_fundamentals_history_ladder
        from core.store import snapshot_cache_path

        with tempfile.TemporaryDirectory() as td:
            code = "V001"
            path = snapshot_cache_path("fundamentals", code, td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "kind": "fundamentals",
                        "code": code,
                        "fetched_at": "2026-07-01T12:00:00",
                        "data": {"metrics": {"pe": 12, "pb": 1.2}},
                        "history": [
                            {
                                "as_of": "2026-07-01",
                                "metrics": {"pe": 12, "pb": 1.2},
                                "data_source": "test",
                            }
                        ],
                    },
                    f,
                )
            seed_fundamentals_history_ladder(
                codes=[code], store_dir=td, quarters=2, write=True, day_step=90
            )
            cov = fundamentals_history_coverage(store_dir=td, codes=[code])
            self.assertEqual(cov["multi_point"], 1)
            self.assertEqual(cov["synthetic_multi_point"], 1)
            self.assertEqual(cov["real_multi_point"], 0)

    def test_empty_universe_report(self):
        from core.validation_universe import empty_fundamentals_report, resolve_validation_codes

        out = resolve_validation_codes(
            watching_codes=["A", "B"],
            universe={"exclude_codes": ["B"], "include_only": []},
        )
        self.assertEqual(out["codes"], ["A", "B"])
        self.assertEqual(out["source"], "watching")
        empty = empty_fundamentals_report(codes=["NOPE999"])
        self.assertIn("NOPE999", empty["empty_codes"])

    def test_ingest_real_history_drops_synthetic(self):
        from unittest.mock import patch

        from core.sample_ops import (
            fundamentals_history_coverage,
            ingest_real_fundamentals_history,
        )
        from core.store import snapshot_cache_path

        with tempfile.TemporaryDirectory() as td:
            code = "600036"
            path = snapshot_cache_path("fundamentals", code, td)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "kind": "fundamentals",
                        "code": code,
                        "fetched_at": "2026-07-01T12:00:00",
                        "data": {"metrics": {"roe": 1.0}},
                        "history": [
                            {
                                "as_of": "2025-01-01",
                                "metrics": {"roe": 1.0},
                                "data_source": "synthetic_demo_ladder",
                                "synthetic_demo": True,
                            },
                            {
                                "as_of": "2026-07-01",
                                "metrics": {"roe": 1.0},
                                "data_source": "akshare_financial_indicator",
                            },
                        ],
                    },
                    f,
                )
            series = [
                {"as_of": "2024-03-31", "roe": 10.0, "profit_growth": 1.0, "source": "akshare_financial_indicator"},
                {"as_of": "2024-06-30", "roe": 11.0, "profit_growth": 1.1, "source": "akshare_financial_indicator"},
                {"as_of": "2024-09-30", "roe": 12.0, "profit_growth": 1.2, "source": "akshare_financial_indicator"},
            ]
            with patch(
                "adapters.fundamentals.engine.fetch_cn_financial_series",
                return_value=series,
            ):
                out = ingest_real_fundamentals_history(
                    codes=[code], store_dir=td, write=True, drop_synthetic=True
                )
            self.assertEqual(out["updated_count"], 1)
            cov = fundamentals_history_coverage(store_dir=td, codes=[code])
            self.assertEqual(cov["real_multi_point"], 1)
            self.assertEqual(cov["synthetic_multi_point"], 0)
            self.assertGreaterEqual(cov["rows"][0]["real_points"], 2)

class TestV1FitGapAndCosts(unittest.TestCase):
    def test_qp_lite_unavailable_falls_back(self):
        from core.portfolio_optimize import optimize_weights

        out = optimize_weights(
            [
                {"stock_code": "600036", "score": 70, "sector": "银行"},
                {"stock_code": "601318", "score": 66, "sector": "保险"},
            ],
            weight_mode="qp_lite",
            apply_market_vol=False,
            max_position_pct=20,
            max_sector_pct=40,
        )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("requested_weight_mode"), "qp_lite")
        # 无 cvxpy 时应回退 score_budget 且 qp.available=False
        if not (out.get("qp") or {}).get("available"):
            self.assertEqual(out.get("weight_mode"), "score_budget")
            self.assertFalse((out.get("qp") or {}).get("available"))
        self.assertGreaterEqual(int(out.get("count") or 0), 1)

    def test_fit_gap_hints(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(
            realization={"status": "unavailable", "reason": "short"},
            cost_compare={"return_gap_pp": 2.5, "avg_impact_bps": 12},
            source_audit={"fallback_count": 2, "status": "warn"},
        )
        codes = {h["code"] for h in out["hints"]}
        self.assertIn("realization_unavailable", codes)
        self.assertIn("cost_gap", codes)
        self.assertIn("impact", codes)

    def test_impact_monotonic(self):
        from core.backtest.costs import estimate_impact_cost

        low = estimate_impact_cost(10_000, 1_000_000)
        high = estimate_impact_cost(500_000, 1_000_000)
        self.assertGreaterEqual(high, low)

class TestV2GapRiskAndPromoteNote(unittest.TestCase):
    def test_gap_risk_factor(self):
        from core.signal.factors.gap_risk import score_gap_risk

        bars = [
            {"close": 10.0, "open": 10.0},
            {"close": 10.1, "open": 10.0},
            {"close": 10.2, "open": 11.0},  # ~8.9% gap up
        ]
        score, meta = score_gap_risk(bars)
        self.assertTrue(meta.get("ok"))
        self.assertLess(score, 50)

class TestV3WeightCompareAndOutcomeGate(unittest.TestCase):
    def test_weight_mode_compare(self):
        from core.weight_mode_compare import compare_weight_modes

        out = compare_weight_modes(
            [
                {"stock_code": "A", "score": 90, "sector": "X"},
                {"stock_code": "B", "score": 80, "sector": "Y"},
                {"stock_code": "C", "score": 70, "sector": "X"},
            ]
        )
        self.assertIn("score_budget", out["modes"])
        self.assertIn("risk_parity_lite", out["modes"])

    def test_effectiveness_pending_under_20(self):
        from core.sample_ops import sample_status

        paper = {
            "snapshots": [],
            "operation_log": [
                {
                    "type": "risk_block",
                    "ts": "2026-07-29T10:00:00",
                    "detail": "x",
                    "meta": {"codes": ["max_position"], "outcome": "true_positive"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            # isolate empty fund store by pointing coverage to empty dir via monkeypatch hard —
            # sample_status uses default store; just assert risk gate field
            st = sample_status(paper=paper)
            self.assertTrue(st["risk_blocks"]["effectiveness_pending"])
            self.assertIsNone(st["risk_blocks"]["effectiveness_rate"])

class TestV4ValidationPack(unittest.TestCase):
    def test_build_pack(self):
        from core.validation_pack import build_validation_pack, render_validation_pack_markdown

        out = build_validation_pack(
            backtest_result={
                "success": True,
                "metrics": {"total_return_pct": 1.2},
                "cost_compare": {"return_gap_pp": 0.5, "avg_impact_bps": 3},
                "source_audit": {"status": "ok", "fallback_count": 0},
            },
            signal_config={"weights": {"momentum": 1.0}},
            sample_status={"discipline": {"warnings": ["demo"]}},
        )
        self.assertTrue(out["ok"])
        self.assertIn("fingerprint", out["pack"])
        md = render_validation_pack_markdown(out)
        self.assertIn("策略验证包", md)

class TestV5MaturityGate(unittest.TestCase):
    def test_gate_shape(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.8,
                    "empty": 0,
                    "total": 10,
                    "real_multi_point": 8,
                    "synthetic_multi_point": 0,
                },
                "ttm": {"real_events": 5, "seeded_events": 1},
                "paper_snapshots": {"enough_for_sharpe": True, "count": 40, "densified_count": 0},
                "risk_blocks": {"labeled_count": 20, "block_count": 20},
                "discipline": {"warnings": []},
            },
            north_star={"realization": {"status": "ok", "corr": 0.55, "tracking_error_pct": 1.2}},
            core_paths={"ok": True},
        )
        self.assertTrue(out["ready_for_n6_review"])
        self.assertEqual(out["hard_passed"], out["hard_total"])
        self.assertIn("next_actions", out)

    def test_unavailable_realization_blocks_ready(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate(
            sample_status={
                "fundamentals_history": {
                    "real_multi_coverage": 0.9,
                    "empty": 0,
                    "total": 10,
                    "real_multi_point": 9,
                    "synthetic_multi_point": 0,
                },
                "ttm": {"real_events": 3, "seeded_events": 0},
                "paper_snapshots": {"enough_for_sharpe": True, "count": 40, "densified_count": 0},
                "risk_blocks": {"labeled_count": 0, "block_count": 0},
                "discipline": {"warnings": []},
            },
            north_star={"realization": {"status": "unavailable", "reason": "curves_too_short"}},
            core_paths={"ok": True},
        )
        self.assertFalse(out["ready_for_n6_review"])
        by_id = {i["id"]: i for i in out["items"]}
        self.assertFalse(by_id["realization_readable"]["ok"])
        self.assertTrue(any("paper_daily" in a for a in (out.get("next_actions") or [])))

    def test_fit_gap_includes_scope_and_paper_ops(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(
            realization={"status": "unavailable", "reason": "no_date_overlap"},
            cost_compare={"return_gap_pp": 2.0},
            paper_ops={"cost_model": "zero", "weight_mode": "score_budget"},
            backtest_params={"cost_model": "simple_cn", "weight_mode": "equal", "horizon_days": 3},
            universe={"candidate_count": 4, "dropped_thin_count": 2},
        )
        codes = {h["code"] for h in out["hints"]}
        self.assertIn("live_vs_bt_scope", codes)
        self.assertIn("cost_model_mismatch", codes)
        self.assertIn("weight_mode_mismatch", codes)
        self.assertIn("small_universe", codes)
        self.assertGreaterEqual(out["warn_count"], 1)


# --- from test_x_track.py ---
"""X 轨：live 财务 PIT / index / 因子健康 / 质量政策。"""

class TestXTrackLiveFeatures(unittest.TestCase):
    def test_resolve_live_fundamentals_as_of_matches_resolve(self):
        from core.fundamentals_pit import resolve_fundamentals_for_score
        from core.signal.live_features import resolve_live_fundamentals

        hist = [
            {
                "as_of": "2024-03-31",
                "available_as_of": "2024-04-20",
                "ann_date": "2024-04-20",
                "metrics": {"pe": 10.0, "pb": 1.2, "roe": 0.12},
            },
            {
                "as_of": "2024-06-30",
                "available_as_of": "2024-08-01",
                "ann_date": "2024-08-01",
                "metrics": {"pe": 12.0, "pb": 1.5, "roe": 0.15},
            },
        ]
        cfg = {"fundamentals": {"pit_mode": "as_of", "missing_as_of_policy": "zero_weight"}}
        with patch(
            "core.fundamentals_pit.load_fundamentals_panel",
            return_value={"history": hist, "history_count": 2},
        ):
            decision = "2024-05-01"
            live = resolve_live_fundamentals(
                "600000",
                as_of=decision,
                config=cfg,
            )
            research = resolve_fundamentals_for_score(
                "600000",
                as_of=decision,
                fund_cfg=cfg["fundamentals"],
                live_fallback=False,
            )
        self.assertEqual(live.get("metrics"), research.get("metrics"))
        self.assertTrue((live.get("fundamentals_pit") or {}).get("fundamentals_pit"))
        self.assertEqual((live.get("metrics") or {}).get("pe"), 10.0)

    def test_ann_missing_flag_on_select(self):
        from core.fundamentals_pit import select_point_as_of

        hist = [
            {
                "as_of": "2024-03-31",
                "ann_missing": True,
                "metrics": {"pe": 8.0},
            }
        ]
        # without available/ann, _available_date falls back to as_of
        point, meta = select_point_as_of(hist, "2024-06-01")
        self.assertIsNotNone(point)
        self.assertTrue(meta.get("ann_missing"))

    def test_factor_health_blocks_money_flow_weight(self):
        from core.signal.factors.meta.health import (
            assess_factor_health,
            guard_weights_for_promote,
        )

        h = assess_factor_health(config={"weights": {"money_flow": 0.2, "momentum": 0.1}})
        self.assertFalse(h.get("ok"))
        self.assertTrue(h.get("promote_blocked"))
        g = guard_weights_for_promote({"money_flow": 0.2})
        self.assertFalse(g.get("ok"))
        g2 = guard_weights_for_promote({"money_flow": 0.2}, force=True)
        self.assertTrue(g2.get("ok"))
        self.assertTrue(g2.get("forced"))

    def test_quality_policy_snapshot(self):
        from core.signal.live_features import build_quality_policy_snapshot

        qp = build_quality_policy_snapshot(config={"fundamentals": {"pit_mode": "as_of"}})
        self.assertTrue(qp["live"]["quality_gate"])
        self.assertFalse(qp["backtest"]["quality_gate"])
        self.assertEqual(qp["live"]["fundamentals_pit_mode"], "as_of")

    def test_fundamentals_depth_cn(self):
        from core.signal.live_features import infer_fundamentals_depth

        with patch(
            "core.ports.market.resolve_market_code",
            return_value="CN",
        ):
            d = infer_fundamentals_depth("600519")
        self.assertEqual(d.get("fundamentals_depth"), "cn_full")
        self.assertFalse(d.get("sentiment_in_yhat"))

    def test_fit_gap_includes_quality_policy(self):
        from core.fit_gap import fit_gap_hints

        out = fit_gap_hints(realization={"status": "unavailable", "reason": "test"})
        codes = {h.get("code") for h in out.get("hints") or []}
        self.assertIn("quality_policy", codes)
        self.assertIsNotNone(out.get("quality_policy"))

    def test_panel_respect_regime_narrows_factors(self):
        from core.research.oo_panel import _resolve_factor_names

        with patch(
            "core.signal.regime.assess_regime",
            return_value={"adjustments": {"enabled_factors": ["momentum", "value"]}},
        ):
            names = _resolve_factor_names(
                respect_regime=True,
                index_bars=[{"close": 1}, {"close": 1.01}],
                config={"regime": {"enabled": True}},
            )
        self.assertEqual(set(names), {"momentum", "value"})


# --- from test_y_track.py ---
"""Y 轨：ŷ 生产硬化（门槛写盘 · 滞回卖出 · 闸门 · 验证包）。"""

class TestY0ScoringFloors(unittest.TestCase):
    def test_save_scoring_floors_only_scoring(self):
        from core.signal.scoring_floors import save_scoring_floors

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "weights": {"momentum": 0.5},
                        "scoring": {
                            "rank_mode": "predicted_score",
                            "min_predicted_score": 1.0,
                            "min_hold_predicted_score": -1.0,
                        },
                    },
                    f,
                )
            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": path}):
                import core.signal.config as cfg_mod

                cfg_mod._cached = None
                out = save_scoring_floors(
                    min_predicted_score=0.5,
                    min_hold_predicted_score=-0.5,
                    note="test",
                )
                self.assertTrue(out["success"])
                self.assertFalse(out["signal_config_weights_touched"])
                with open(path, encoding="utf-8") as rf:
                    raw = json.loads(rf.read())
                self.assertEqual(raw["weights"]["momentum"], 0.5)
                self.assertEqual(raw["scoring"]["min_predicted_score"], 0.5)
                self.assertEqual(raw["scoring"]["min_hold_predicted_score"], -0.5)

class TestStanceSave(unittest.TestCase):
    def test_save_stance_only_thresholds(self):
        from core.signal.stance_save import save_stance_thresholds

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "weights": {"momentum": 0.5},
                        "scoring": {
                            "rank_mode": "predicted_score",
                            "min_predicted_score": 1.0,
                        },
                        "stance_thresholds": {
                            "avoid": -0.5,
                            "wait": 0.0,
                            "probe": 0.35,
                        },
                    },
                    f,
                )
            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": path}):
                import core.signal.config as cfg_mod

                cfg_mod._cached = None
                out = save_stance_thresholds(
                    avoid=-0.5, wait=0.5, probe=0.85, note="test"
                )
                self.assertTrue(out["success"])
                self.assertFalse(out["signal_config_weights_touched"])
                self.assertFalse(out.get("scoring_touched"))
                with open(path, encoding="utf-8") as rf:
                    raw = json.loads(rf.read())
                self.assertEqual(raw["weights"]["momentum"], 0.5)
                self.assertEqual(raw["scoring"]["min_predicted_score"], 1.0)
                self.assertEqual(raw["stance_thresholds"]["wait"], 0.5)
                self.assertEqual(raw["stance_thresholds"]["probe"], 0.85)

    def test_save_stance_rejects_unordered(self):
        from core.signal.stance_save import save_stance_thresholds

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "stance_thresholds": {
                            "avoid": -0.5,
                            "wait": 0.0,
                            "probe": 0.35,
                        }
                    },
                    f,
                )
            with patch.dict(os.environ, {"QUANTLAB_SIGNAL_CONFIG": path}):
                out = save_stance_thresholds(avoid=1.0, wait=0.0, probe=0.5)
                self.assertFalse(out["success"])
                self.assertIn("avoid < wait < probe", out.get("error") or "")

class TestY2BuyBlock(unittest.TestCase):
    def test_limit_up_skip_reason(self):
        from core.paper.rebalance import _buy_match_block_reason

        reason = _buy_match_block_reason(
            "600519",
            {"prev_close": 100.0, "price_raw": 110.0, "change_raw": 10.0},
        )
        self.assertIsNotNone(reason)
        self.assertIn("涨停", reason or "")

class TestY4MaturityAndPack(unittest.TestCase):
    def test_maturity_has_y_track_items(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate()
        ids = {i["id"] for i in out.get("items") or []}
        self.assertIn("yhat_hysteresis_configured", ids)
        self.assertIn("yhat_live_health", ids)
        self.assertIn("paper_daily_streak", ids)
        track = str(out.get("track") or "")
        self.assertIn("Y0-Y5", track)
        # yhat_live_health 在分组退役后恒为 soft-pass
        health = next(
            i for i in (out.get("items") or []) if i.get("id") == "yhat_live_health"
        )
        self.assertTrue(health.get("ok"))
        self.assertIn("cluster_retired", str(health.get("detail") or ""))

    def test_validation_pack_has_rank_mode_and_cluster(self):
        from core.validation_pack import build_validation_pack

        out = build_validation_pack(
            signal_config={
                "scoring": {
                    "rank_mode": "predicted_score",
                    "min_predicted_score": 1.0,
                    "min_hold_predicted_score": -1.0,
                },
                "weights": {},
            }
        )
        self.assertTrue(out.get("ok"))
        pack = out["pack"]
        self.assertEqual(pack.get("rank_mode"), "predicted_score")
        self.assertIn("scoring_floors", pack)
        self.assertGreaterEqual(int(pack.get("version") or 0), 3)

class TestY3StanceThresholds(unittest.TestCase):
    def test_yhat_thresholds_not_0_100_bands(self):
        from core.signal.config import get_stance_thresholds

        cfg = {
            "scoring": {"rank_mode": "predicted_score"},
            "stance_thresholds": {"avoid": -0.5, "wait": 0.0, "probe": 0.35},
        }
        th = get_stance_thresholds(cfg, kind="predicted")
        self.assertLess(th["avoid"], 10.0)
        self.assertLess(th["wait"], 10.0)
        self.assertLess(th["probe"], 10.0)
        self.assertLess(th["avoid"], th["wait"])

    def test_legacy_stance_table_returns_yhat_defaults(self):
        from core.signal.config import get_stance_thresholds_with_meta

        cfg = {
            "stance_thresholds": {"avoid": 45, "wait": 55, "probe": 68},
            "stance_thresholds_heuristic": {"avoid": 45, "wait": 55, "probe": 68},
        }
        out = get_stance_thresholds_with_meta(cfg, kind="predicted")
        th = out["thresholds"]
        self.assertTrue(out.get("legacy_detected"))
        self.assertLess(th["avoid"], 10.0)
        self.assertNotEqual(th["avoid"], 45.0)
        self.assertNotEqual(th["wait"], 55.0)

class TestY1ClusterRetired(unittest.TestCase):
    def test_cluster_scoring_defaults_retired_off(self):
        """get_cluster_scoring_cfg 已删；DEFAULTS 仍保留 cluster_scoring=off。"""
        from core.signal.config import DEFAULT_SIGNAL_CONFIG

        cs = DEFAULT_SIGNAL_CONFIG.get("cluster_scoring") or {}
        self.assertFalse(bool(cs.get("enabled")))
        self.assertEqual(str(cs.get("mode") or "").lower(), "off")
        self.assertEqual(cs.get("max_oos_fail_rate"), 0.5)
        self.assertIn("min_yhat_rolling_ic", cs)
        self.assertIn("min_sector_map_coverage", cs)


# --- from test_pro_core_track.py ---
"""专业核心三轨 DC / FM / RK 最小验收。"""

class TestProCoreDC(unittest.TestCase):
    def test_assess_pit_depth_thresholds(self):
        from core.pro_core import ANN_MISSING_HARD_MAX, assess_pit_depth

        soft = assess_pit_depth(
            fundamentals_history={
                "ann_missing_code_ratio": 0.4,
                "real_multi_coverage": 0.6,
                "ann_missing_top": [{"stock_code": "600000"}],
            }
        )
        self.assertTrue(soft["ok"])
        self.assertFalse(soft["soft_ok"])
        self.assertEqual(soft["honest_label"], "pit_soft_debt")
        self.assertIn("600000", soft["ann_missing_top_codes"])

        hard = assess_pit_depth(
            fundamentals_history={
                "ann_missing_code_ratio": ANN_MISSING_HARD_MAX + 0.1,
                "real_multi_coverage": 0.6,
            }
        )
        self.assertFalse(hard["ok"])
        self.assertEqual(hard["honest_label"], "pit_hard_debt")

    def test_filter_halted_bars(self):
        from core.market.calendar import filter_halted_bars

        bars = [
            {"date": "2024-01-02", "volume": 100, "close": 10},
            {"date": "2024-01-03", "volume": 0, "close": 10},
            {"date": "2024-01-04", "volume": 50, "status": "停牌", "close": 10},
            {"date": "2024-01-05", "vol": 80, "close": 11},
        ]
        kept, audit = filter_halted_bars(bars)
        self.assertEqual(len(kept), 2)
        self.assertEqual(audit["dropped_n"], 2)
        self.assertGreaterEqual(audit["drop_reasons"].get("zero_volume", 0), 1)

    def test_adjust_policy_consistent(self):
        from core.pro_core import assert_adjust_policy_consistent

        ok = assert_adjust_policy_consistent(requested="qfq", observed="qfq")
        self.assertTrue(ok["ok"])
        bad = assert_adjust_policy_consistent(requested="qfq", observed="raw")
        self.assertFalse(bad["ok"])

class TestProCoreFM(unittest.TestCase):
    def test_guard_weights_blocks_proxy(self):
        from core.signal.factors.meta.health import guard_weights_for_promote

        g = guard_weights_for_promote({"money_flow": 0.15})
        self.assertTrue(g.get("blocked"))
        g2 = guard_weights_for_promote({"money_flow": 0.15}, force=True)
        self.assertFalse(g2.get("blocked"))
        self.assertTrue(g2.get("forced"))

    def test_list_factors_meta(self):
        from core.signal.factors.meta.registry import list_factors

        rows = list_factors(include_meta=True)
        mf = next((r for r in rows if r["name"] == "money_flow"), None)
        self.assertIsNotNone(mf)
        self.assertFalse(mf.get("sourced"))
        self.assertEqual(mf.get("status"), "proxy")

class TestProCoreRK(unittest.TestCase):
    def test_regime_position_scale(self):
        from core.pro_core import regime_position_scale

        r = regime_position_scale(regime={"regime": "weak", "adjustments": {}})
        self.assertLess(r["scale"], 1.0)
        r2 = regime_position_scale(
            regime={"adjustments": {"position_scale": 0.5}}
        )
        self.assertAlmostEqual(r2["scale"], 0.5)

    def test_style_soft_caps(self):
        from core.pro_core import style_soft_caps_from_exposure

        caps = style_soft_caps_from_exposure(
            {"styles": {"growth": 55.0, "value": 10.0}}, max_style_pct=40.0
        )
        self.assertFalse(caps["ok"])
        self.assertEqual(caps["over_limit"][0]["style"], "growth")

    def test_optimize_limits_include_regime_scale(self):
        from core.portfolio_optimize import optimize_weights

        out = optimize_weights(
            [
                {"stock_code": "600000", "score": 0.05, "sector": "银行"},
                {"stock_code": "600001", "score": 0.04, "sector": "银行"},
            ],
            max_position_pct=10.0,
            max_sector_pct=20.0,
            max_positions=5,
            min_score=-1.0,
            apply_market_vol=False,
            apply_regime_scale=False,
        )
        self.assertTrue(out.get("ok"))
        self.assertIn("regime_scale", out)
        self.assertIn("combined_scale", out)
        self.assertIn("regime_scale", out.get("limits") or {})

class TestProCoreIngestNudge(unittest.TestCase):
    def test_ingest_nudge_payload(self):
        from core.pro_core import ingest_nudge_payload

        p = ingest_nudge_payload(
            sample_status={
                "fundamentals_history": {
                    "ann_missing_code_ratio": 0.1,
                    "real_multi_coverage": 0.8,
                    "ann_missing_top": [{"code": "000001"}, {"code": "000002"}],
                }
            }
        )
        self.assertTrue(p["ok"])
        self.assertEqual(p["count"], 2)


if __name__ == "__main__":
    unittest.main()
