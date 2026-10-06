"""P102：前端 JS 门禁 —— 防止 ??/|| 混用等导致整页模块加载失败。"""

from __future__ import annotations

import unittest

from scripts.check_frontend_js import (
    has_illegal_nullish_logic_mix,
    run_checks,
)


class TestP102FrontendJs(unittest.TestCase):
    def test_nullish_mix_detector_catches_original_bug(self):
        bad = 't.top_k ?? (t.legs || []).length || "—"'
        self.assertTrue(has_illegal_nullish_logic_mix(bad))
        # 模板字符串里的同一写法也应被扫到
        from scripts.check_frontend_js import find_nullish_mix_issues, STATIC

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


if __name__ == "__main__":
    unittest.main()
