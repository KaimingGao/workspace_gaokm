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

        replay = self._read("web", "static", "partials", "replay_panel.html")
        self.assertIn("quant-neutral-compare-table", replay)
        self.assertIn('id="replay-kpi-row"', replay)
        self.assertIn('id="replay-kpi-return"', replay)

    def test_t0_trades_fullscreen_toggle(self):
        table_js = self._read("web", "static", "js", "paper", "t0_table.js")
        css = self._read("web", "static", "css", "follow.css")
        self.assertIn("data-trades-fs-toggle", table_js)
        self.assertIn("paper-t0-trades-panel", table_js)
        self.assertIn("bindT0TradesFullscreen", table_js)
        self.assertIn(".paper-t0-trades-panel.is-fs", css)

    def test_interpret_request_has_offline(self):
        try:
            from web.schemas import QuantInterpretRequest
        except ImportError:
            self.skipTest("fastapi/pydantic not installed")
        self.assertIn("offline", QuantInterpretRequest.model_fields)


if __name__ == "__main__":
    unittest.main()
