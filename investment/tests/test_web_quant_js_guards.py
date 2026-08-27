"""量化前端关键字符串守卫（指向 quant.js / partials，不再读已拆空的 index.html/app.js）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestWebQuantJsGuards(unittest.TestCase):
    def _read(self, *parts):
        path = os.path.join(ROOT, *parts)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_quant_js_neutral_compare_and_export(self):
        js = self._read("web", "static", "js", "quant.js")
        export_js = self._read("web", "static", "js", "quant", "domain_export.js")
        self.assertIn("QUANT_EXPORT_PRESETS", js)
        self.assertIn('previewQuantExport("markdown")', js)
        self.assertIn("runQuantInterpret", js)
        self.assertIn("forceOffline: true", js)
        self.assertIn("llmAvailable", export_js)
        self.assertIn("offline: useOffline", export_js)
        self.assertIn("readDailyBtOverrides", export_js)
        self.assertIn("quant_daily_bt_opts_v3", export_js)
        self.assertIn("quant-daily-top-k", export_js)

    def test_daily_bt_option_js(self):
        export_js = self._read("web", "static", "js", "quant", "domain_export.js")
        self.assertIn("readDailyBtOverrides", export_js)
        self.assertIn("{ preset, ...readDailyBtOverrides() }", export_js)

    def test_partials_have_key_controls(self):
        panel = self._read("web", "static", "partials", "quant_panel.html")
        self.assertIn('id="quant-daily-fold"', panel)
        self.assertIn('id="quant-daily-top-k"', panel)
        self.assertIn('id="quant-daily-horizon"', panel)
        self.assertIn('id="quant-daily-lookback"', panel)
        self.assertIn('<option value="30" selected>30</option>', panel)
        self.assertIn('<option value="60">60</option>', panel)
        self.assertIn('<option value="90">90</option>', panel)
        self.assertIn('<option value="3" selected>3</option>', panel)
        self.assertIn('<option value="1" selected>1日</option>', panel)
        self.assertIn("quant-interpret-offline", panel)
        self.assertIn("规则解读", panel)
        self.assertIn("quant-interpret-neutral", panel)
        self.assertIn("quant-ols-run", panel)
        self.assertIn("quant-ols-code", panel)
        self.assertIn("quant-probe-picker", panel)
        self.assertIn("quant-ols-code-menu", panel)
        self.assertIn("quant-probe-run", panel)
        self.assertIn("quant-global-fold", panel)
        self.assertIn("quant-section-threshold", panel)
        self.assertIn("quant-threshold-apply", panel)
        self.assertIn("quant-section-cross", panel)
        self.assertIn("对照验证", panel)
        self.assertIn("quant-ols-summary", panel)
        self.assertIn("quant-factor-list", panel)
        self.assertIn("quant-ridge-lambda", panel)
        self.assertIn("quant-cross-list", panel)
        self.assertIn("quant-weight-table-wrap", panel)
        self.assertIn("quant-cross-run", panel)

        self.assertIn('id="quant-section-minute"', panel)
        self.assertIn('id="quant-cluster-minute-refresh"', panel)
        self.assertIn("强更 5m", panel)
        self.assertIn("增量 merge", panel)
        self.assertIn("强更全量更新", panel)
        minute_js = self._read("web", "static", "js", "quant", "cluster_minute_ui.js")
        self.assertIn("installClusterMinuteUi", minute_js)
        self.assertIn("/api/quant/cluster-minute/status", minute_js)
        self.assertIn("/api/jobs/cluster-minute-refresh", minute_js)
        self.assertIn("unwrapJobSnap", minute_js)
        self.assertIn("applyJobFailure", minute_js)
        self.assertIn("syncJobSlot", minute_js)
        bars_js = self._read("web", "static", "js", "quant", "cluster_bars_ui.js")
        self.assertIn("unwrapJobSnap", bars_js)
        self.assertIn("syncJobSlot", bars_js)
        self.assertIn("applyJobFailure", bars_js)
        job_js = self._read("web", "static", "js", "quant", "cluster_job_ui.js")
        self.assertIn("unwrapJobSnap", job_js)
        quant_js = self._read("web", "static", "js", "quant.js")
        self.assertIn("installClusterMinuteUi", quant_js)

        replay = self._read("web", "static", "partials", "replay_panel.html")
        self.assertIn("quant-neutral-compare-table", replay)
        self.assertIn('id="replay-kpi-row"', replay)
        self.assertIn('id="replay-kpi-return"', replay)

    def test_t0_trade_table_has_nc_oc_column(self):
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        self.assertIn("y_nc_oc", table_js)
        self.assertIn("paper-t0-col-nc-oc", table_js)
        self.assertIn("fmtNowcastOcCell", table_js)

    def test_score_tooltip_nc_oc_tip_mode(self):
        tip_js = self._read("web", "static", "js", "score_tooltip.js")
        self.assertIn('tip === "nc_oc"', tip_js)
        self.assertIn("formatNcOcSection", tip_js)
        self.assertIn('tipMode === "nc_oc"', tip_js)

    def test_y_nc_column_uses_resolve_nowcast_cc_score(self):
        fmt_js = self._read("web", "static", "js", "paper", "fmt.js")
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        self.assertIn("resolveNowcastCcScore", fmt_js)
        self.assertIn("nowcast: resolveNowcastCcScore", table_js)
        self.assertIn("resolveNowcastScore(it)", fmt_js)
        self.assertIn("return resolveNowcastCcScore(it)", fmt_js)
        self.assertIn("Y_NC_TITLE", fmt_js)
        self.assertIn("Y_NC_OC_TITLE", fmt_js)
        self.assertIn("nowcast = nc", fmt_js)

    def test_follow_panel_nc_terminology(self):
        panel = self._read("web", "static", "partials", "follow_panel.html")
        self.assertIn("nowcast oc", panel)
        self.assertIn("nc异号", panel)

    def test_t0_trades_fullscreen_toggle(self):
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        css = self._read("web", "static", "css", "follow.css")
        self.assertIn("data-trades-fs-toggle", table_js)
        self.assertIn("paper-t0-trades-panel", table_js)
        self.assertIn("bindT0TradesFullscreen", table_js)
        self.assertIn(".paper-t0-trades-panel.is-fs", css)

    def test_t0_trade_date_col_fits_iso_day(self):
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        css = self._read("web", "static", "css", "follow.css")
        self.assertIn('date: "104px"', table_js)
        self.assertIn("fmtTradeDate", table_js)
        self.assertIn("width: 104px", css)
        self.assertIn(".paper-t0-col-date", css)

    def test_live_t0_tables_omit_realized_pair(self):
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        ui_js = self._read("web", "static", "js", "paper", "t0_ui.js")
        self.assertIn("fmtPredRealizedText", table_js)
        self.assertIn("showRealized = true", table_js)
        self.assertEqual(ui_js.count("showRealized: false"), 2)

    def test_holdings_table_has_t0_column(self):
        island_js = self._read("web", "static", "js", "holdings_table_island.js")
        badge_js = self._read("web", "static", "js", "paper", "holding_t0_badge.js")
        css = self._read("web", "static", "css", "follow.css")
        self.assertIn('id: "t0"', island_js)
        self.assertIn("holdingT0BadgeHtml", island_js)
        self.assertIn("t0_intraday", island_js)
        self.assertIn("paper-hold-t0-badge", badge_js)
        self.assertIn(".paper-hold-t0-badge", css)

    def test_follow_panel_t0_form_defaults(self):
        panel = self._read("web", "static", "partials", "follow_panel.html")
        self.assertNotIn('name="t0_ratio"', panel)
        self.assertNotIn("动仓%", panel)
        self.assertIn('name="sell_trigger_pct"', panel)
        self.assertIn('value="1"', panel)
        self.assertIn('name="enabled" checked', panel)
        self.assertIn('name="must_cover_same_day" checked', panel)
        self.assertIn('name="y_path_enter"', panel)
        self.assertIn('name="y_path_enter" min="0.01" max="5" step="0.01" value="0.02"', panel)
        self.assertIn('value="3.0"', panel)

    def test_execution_ui_fill_form_sets_checkbox_false(self):
        ui = self._read("web", "static", "js", "paper", "execution_ui.js")
        self.assertIn('if (el.type === "checkbox")', ui)
        self.assertIn("el.checked = !!val", ui)
        self.assertIn('set("enabled", t0.enabled !== false)', ui)
        self.assertIn('t0_ratio: 1.0', ui)
        self.assertIn('specKpi("启用", enabledLbl', ui)

    def test_follow_panel_target_price_ratio_controls(self):
        panel = self._read("web", "static", "partials", "follow_panel.html")
        ui = self._read("web", "static", "js", "paper", "execution_ui.js")
        self.assertIn('name="y_ratio_cut"', panel)
        self.assertIn('name="y_ratio_boost_cap"', panel)
        self.assertIn("目标弱%", panel)
        self.assertIn("目标强%", panel)
        self.assertIn("y_ratio_boost_cap", ui)
        self.assertIn("adaptiveSizingBounds", ui)

    def test_interpret_request_has_offline(self):
        try:
            from web.schemas import QuantInterpretRequest
        except ImportError:
            self.skipTest("fastapi/pydantic not installed")
        self.assertIn("offline", QuantInterpretRequest.model_fields)


if __name__ == "__main__":
    unittest.main()
